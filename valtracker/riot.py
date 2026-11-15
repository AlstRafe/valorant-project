"""持ち主自身の Riot アカウント: VALORANT のストアのサーバー（PD）が言う所持品。公開の API では取れない。

サインイン: Riot のサインインページには captcha があるので、このサーバーはパスワードを見ない。
持ち主はブラウザーで 1 回サインインし、設定ページに次のどちらかを貼る:

- auth.riotgames.com の ``ssid`` cookie（または Cookie ヘッダーまるごと）: 必要なときに 1 時間のトークンと交換し、
  Riot が毎回返す新しい cookie で置き換えて保存する
- サインイン後にブラウザーが行き着くアドレス（``https://playvalorant.com/opt_in#access_token=...``）
  : 1 時間のトークンだけで、長く残るものは持たない

デスクトップアプリ（desktop.py）ではどちらも要らない: PC の Riot Client にサインインしているアカウントがトークンを出し（
 localclient.py）、ゲームの状態は GLZ のセッションに聞かなくてもクライアントのイベントでわかる。

複数のアカウントを連携でき、そのうち 1 つを使う: ページはそのアカウントのストア・コレクション・進み具合を出し、
持ち主が切り替える（トップページ、設定）。デスクトップアプリでは Riot Client のアカウントもその 1 つで、
この PC のゲーム（/live: キュー、ピック）はどれを表示していてもいつもそのアカウントのもの。

instance/riot.json に残す（持ち主だけが読める、Git には入れない）: 使用中のアカウントの cookie、アカウント（puuid、
Riot ID、シャード）、切れるまでのトークン。ほかの連携アカウントは ``others`` に（``{puuid: 同じキー}``）、
``linked_first``（デスクトップアプリ: Riot Client が別のアカウントでも連携アカウントを表示する）。
これらはゲームクライアント自身のアドレスで、公開の API ではない: Riot は予告なく変えられるので、
どの項目も無くてよいものとして読み、失敗したものは持ち主が見られるように残す。"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from valtracker import net, pdmatch
from valtracker.cache import TTLCache
from valtracker.settings import Settings
from valtracker.util import lower_id, to_float, token_claims

AUTHORIZE = (
    "https://auth.riotgames.com/authorize?redirect_uri=https%3A%2F%2Fplayvalorant.com%2Fopt_in"
    "&client_id=play-valorant-web-prod&response_type=token%20id_token&nonce=1&scope=account%20openid"
)
ENTITLEMENTS = "https://entitlements.auth.riotgames.com/api/token/v1"
USERINFO = "https://auth.riotgames.com/userinfo"
GEO = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"
PD = "https://pd.{shard}.a.pvp.net"
#: パーティーサーバー: ゲームのキュー設定（マップのプール、一緒に並べる人）はここから。リージョンはシャードと同じだが、
#: ラテンアメリカとブラジル（na シャードの latam、br）は別で、それはデスクトップアプリしか知らない。
GLZ = "https://glz-{region}-1.{shard}.a.pvp.net"
#: キュー設定を持つ秒数: パッチやマップのローテーションで変わる。
QUEUES_KEEP = 1800
#: 持ち主のゲームの状態を持つ秒数: 2 つのページが同時に聞いても Riot に聞くのが 1 回で済むようにするためだけ。
#: ページはその時に必要な頻度で聞く（live.EVERY）: 試合が見つかったら毎秒。
LIVE_KEEP = 1
#: 持ち主がいうる試合の段階と、GLZ がそれぞれを答える場所。
PHASES = (("pregame", "/pregame/v1"), ("ingame", "/core-game/v1"))
#: ゲームのセッション（loopState）-> 最初に GLZ に聞く段階。
LOOPS = {"PREGAME": "pregame", "INGAME": "ingame"}
#: パーティーがこのどれかの状態ならマッチング中、または試合が見つかった（エージェント選択前のゲームの 5 秒カウント、
#: "MATCHMADE_GAME_STARTING"）: すぐエージェント選択を聞き、持ち主のエージェントを先に読む。
QUEUEING = ("MATCHMAKING", "STARTING")
#: 持ち主のエージェント（ピック画面用）を持つ秒数: エージェントの解放はめったに無い。
AGENTS_KEEP = 1800
#: パーティーと試合でのプレイヤーの名前を持つ秒数: 数秒ごとではなく 1 回だけ聞く。
NAME_KEEP = 3600
#: エージェント選択を見るときにプレイヤーのランクを待つ秒数: それまでに読めなかったものは次の確認で来て、
#: その間ページはゲーム自身のバッジを出す。
RANK_WAIT = 0.8
#: 持ち主が /live で押せるもの（Riot.act）: キューの開始（キュー ID）、離脱、エージェントのピック・ロック（uuid）。
ACTIONS = ("start", "leave", "select", "lock")
#: プレイヤーのランク（PD の mmr）を聞くのは 1 試合 1 回まで: 試合が終わったときにしか変わらない。
RANK_KEEP = 900
#: 持ち主の試合でのパーティーの仲間（Riot.mates）を持つ秒数: 試合の間パーティーは変わらないので、1 試合に 1 回読む。
#: 読めなかったときは MATES_RETRY 秒あとにまた聞く（その間、名前を隠している仲間の名前は出さない）。
MATES_KEEP, MATES_RETRY = 600, 60
#: リージョンのランキングの 1 ページを持つ秒数: Riot 自身が数分ごとに数え直す。
BOARD_KEEP = 300
#: Riot がランキングを出さなかったリージョンにまた聞くまでの時間（その間は HenrikDev のものを出す）。
BOARD_RESTING = 600
#: ランキングのリージョン（ゲームの名前）-> それを持つ PD サーバー。
BOARD_REGIONS = {"ap": "ap", "kr": "kr", "eu": "eu", "na": "na", "latam": "na", "br": "na"}
_QUEUE_ID = re.compile(r"^[a-z0-9_-]{2,40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: アカウントのリージョン（Riot の geo の答え）-> そのアイテムを持つ PD サーバー。
SHARDS = {"na": "na", "latam": "na", "br": "na", "eu": "eu", "ap": "ap", "kr": "kr", "pbe": "pbe"}
#: プレイヤーの履歴の 1 回の呼び出しで頼む試合（または RR の変化）の数: ゲームのクライアントと同じ。
HISTORY_PAGE = 20
#: PD に伝える PC のゲームクライアント。
PLATFORM = base64.b64encode(
    json.dumps(
        {
            "platformType": "PC",
            "platformOS": "Windows",
            "platformOSVersion": "10.0.19042.1.256.64bit",
            "platformChipset": "Unknown",
        }
    ).encode()
).decode()
#: 持ち主のロードアウトの場所（PD の playerloadout）、
#: 新しい順: 2026 年 9 月からは v3（そこでは v2 が 404 RESOURCE_NOT_FOUND。v3 のスプレーは ``ActiveExpressions``）。
#: 404 なら次を聞き、変更はロードアウトを読んだ場所に戻す。
LOADOUTS = ("/personalization/v3/players/{puuid}/playerloadout", "/personalization/v2/players/{puuid}/playerloadout")
#: コレクションの種類 -> PD のアイテムタイプ。
ITEM_TYPES = {
    "skins": "e7c63390-eda7-46e0-bb7a-a6abdacd2433",  # スキンのレベル
    "chromas": "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",  # ガンバディのレベル。コピーごとにエンタイトルメント 1 つ
    "cards": "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "sprays": "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "titles": "de7caa6b-adf7-4588-bbd1-143831e786c6",
    "flex": "03a572de-4234-31ed-d344-ababa488f981",
    "agents": "01bb38e1-da47-4e6a-9b3d-945fe4655707",
}
#: コレクションを持つ秒数: ゲームで買ったスキンはこれが過ぎてからここに出る。
KEEP = 90
#: riot.json が連携アカウント 1 つについて持つもの（ファイルの残りは 1 つのアカウントのものではない）。
ACCOUNT_KEYS = ("cookies", "token", "puuid", "name", "tag", "shard", "signed_out")
#: 連携できるアカウントの最大数。
ACCOUNTS_MAX = 8
#: トークンが切れるこの時間前に新しいものと交換する。
EARLY = 300
#: 貼られたものから読む最大の長さ（Cookie ヘッダーまるごとで数 kB）。
MAX_PASTE = 8000
_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_COOKIE_VALUE = re.compile(r"^[\x21-\x7e]{1,4096}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._\-]{20,8192}$")


class RiotError(Exception):
    """答えが無い理由: ``kind`` は nologin（アカウント未連携）、
    badpaste（貼ったものが cookie でもサインイン後のアドレスでもない）、signedout（Riot が cookie を受け付けない、
    または 1 時間が過ぎた）、busy、down（答えが無い、または読めない）、full（もう ACCOUNTS_MAX 個連携している）、
    unknown（そのアカウントやボタンは無い）のどれか。``detail``: 持ち主向けの Riot の文。"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


class Riot:
    def __init__(self, folder: Path, send: net.Send = net.send, version=dict, clock=time.time, local=None):
        """``version``: () -> PD が求める valorant-api.com の /version（content.Content.client_version）。
        ``local``: この PC の Riot Client（localclient.LocalClient、デスクトップアプリ）、無ければ None。"""
        self.store = Settings(folder, "riot.json")
        self.send, self.version, self.clock, self.local = send, version, clock, local
        #: 最後に /live を読んだときのクライアントのイベント番号: 新しいイベントがあればすぐ読み直す。
        self._local_version = -1
        #: 最後に見たとき Riot Client にサインインしていたアカウント（"": 閉じている、None: まだ見ていない）。
        self._client_seen: str | None = None
        self.cache = TTLCache(8)
        #: 持ち主の試合のプレイヤーのランク: {puuid: PD の mmr の答え}。
        self.ranks = TTLCache(64)
        #: 持ち主のパーティーか試合のプレイヤーの名前: {puuid: [name, tag]}。
        self.names = TTLCache(64)
        #: 持ち主の試合での同じパーティーのメンバー: {(持ち主, 試合の ID): frozenset(puuid)}。
        self.mates = TTLCache(8)
        #: GLZ で最後に持ち主がいた場所（{"party": id, "pregame": id, "ingame": id}）: 次の確認では、
        #: まず持ち主の居場所を聞かずにすぐそこを聞く。
        self.where: dict[str, str] = {}
        #: 読んでいる途中のランク（riot-rank スレッド）。何回の確認が求めても 1 回だけ聞く。
        self._asking: set[str] = set()
        self._asking_lock = threading.Lock()
        self._ranker = ThreadPoolExecutor(5, thread_name_prefix="riot-rank")
        #: ランキングのページと、失敗して休んでいるリージョン。
        self.boards = TTLCache(64)
        self._lock = threading.Lock()

    # -- ページが使うもの ----------------------------------------------------------------------------

    def status(self, game: bool = False) -> dict:
        """設定ページ用: 使用中のアカウント、それが続くか（cookie）``until`` で終わるか（1 時間のアドレス）、
        前回 Riot に断られたか（``signed_out``）。``client``: そのアカウントがこの PC の Riot Client
        にサインインしているもの（デスクトップアプリ）。
        ``game``: この PC でゲームをしているアカウント（ページがどれを表示していても Riot Client のもの）。"""
        saved = self.store.load()
        found = self._client(game=game)
        if found is not None:
            account = found[1]
            return {
                "linked": True,
                "name": account["name"],
                "tag": account["tag"],
                "shard": account["shard"],
                "lasting": True,
                "until": 0.0,
                "signed_out": False,
                "problem": str(saved.get("problem") or ""),
                "client": True,
            }
        token = saved.get("token") if isinstance(saved.get("token"), dict) else {}
        lasting = bool(saved.get("cookies"))
        return {
            "linked": bool(saved.get("puuid")),
            "name": str(saved.get("name") or ""),
            "tag": str(saved.get("tag") or ""),
            "shard": str(saved.get("shard") or ""),
            "lasting": lasting,
            "until": 0.0 if lasting else to_float(token.get("until")) + EARLY,
            "signed_out": bool(saved.get("signed_out"))
            or (not lasting and to_float(token.get("until")) < self.clock()),
            "problem": str(saved.get("problem") or ""),
            "client": False,
        }

    def me(self, game: bool = False) -> str:
        """使用中のアカウントの puuid: この PC の Riot Client のもの（デスクトップアプリ）、無ければ連携しているもの。
        ``game``: この PC でゲームをしているアカウントのもの。"""
        found = self._client(game=game)
        return found[1]["puuid"] if found is not None else str(self.store.load().get("puuid") or "").lower()

    def _client(self, renew: bool = False, game: bool = False) -> tuple[dict, dict] | None:
        """この PC の Riot Client からの (tokens, account)、または None: デスクトップアプリでない、
        クライアントが閉じている、または（``game``＝ゲーム自体に聞くとき以外で）
        持ち主が連携アカウントを表示すると選んだ。"""
        if self.local is None:
            return None
        found = self.local.signed_in(renew)
        self._follow(found[1]["puuid"] if found is not None else "")
        if found is None or game:
            return found
        saved = self.store.load()
        linked = str(saved.get("puuid") or "").lower()
        return None if saved.get("linked_first") and linked and linked != found[1]["puuid"] else found

    def _follow(self, puuid: str) -> None:
        """デスクトップアプリ: Riot Client が開いたとき、または別のアカウントにサインインしたとき、
        そのアカウントをまた表示する。それまでは持ち主が選んだ連携アカウントを表示する。"""
        if puuid == self._client_seen:
            return
        self._client_seen = puuid
        if puuid and self.store.load().get("linked_first"):
            self.store.save(linked_first=None)
            self._forget()

    def accounts(self) -> list[dict]:
        """持ち主が使える全アカウント。いつも同じ順（Riot Client のものが先、次に連携しているものを名前順）
        : ``[{"puuid", "name", "tag", "shard", "current": 今ページに出している, "client": この PC の Riot Client にサインインしている, "linked": cookie かアドレスつき（解除できる）
        , "signed_out": 前回 Riot に断られた}]``。"""
        saved = self.store.load()
        signed = self._client(game=True)
        found: list[dict] = []
        if signed is not None:
            found.append({**_row(signed[1]), "client": True, "linked": False, "signed_out": False})
        linked = [item for item in (saved, *_others(saved).values()) if item.get("puuid")]
        for item in sorted(linked, key=lambda item: str(item.get("name") or "").casefold()):
            row = _row(item)
            if found and found[0]["client"] and row["puuid"] == found[0]["puuid"]:
                found[0]["linked"] = True  # クライアントのアカウントも連携している: 1 行にまとめる
                continue
            token = item.get("token") if isinstance(item.get("token"), dict) else {}
            over = not item.get("cookies") and to_float(token.get("until")) < self.clock()
            found.append({**row, "client": False, "linked": True, "signed_out": bool(item.get("signed_out")) or over})
        current = self.me()
        for row in found:
            row["current"] = row["puuid"] == current
        return found

    def switch(self, puuid: str) -> dict:
        """今から持ち主の別のアカウントを表示する: 連携しているもの、または（デスクトップアプリ）
        Riot Client にサインインしているもの。どちらでもなければ RiotError ``unknown``。"""
        puuid = str(puuid or "").strip().lower()
        with self._lock:
            saved = self.store.load()
            others = _others(saved)
            signed = self._client(game=True)
            first = True if self.local is not None else None
            if signed is not None and puuid == signed[1]["puuid"]:
                changes = {"linked_first": None}  # どれであっても、また Riot Client のアカウント
            elif puuid and puuid == str(saved.get("puuid") or "").lower():
                changes = {"linked_first": first}
            elif puuid in others:
                changes = _swap(saved, others, puuid) | {"linked_first": first}
            else:
                raise RiotError("unknown")
            self.store.save(**changes, problem=None)
            self._forget()
        return self.status()

    def _forget(self) -> None:
        """今まで表示していたアカウントで読んだものは、次のアカウントでは出さない。"""
        self.cache = TTLCache(8)
        self.where = {}

    def sign_in(self, pasted: str) -> dict:
        """持ち主が貼ったものでアカウントを連携する（モジュールの説明を参照）。うまくいかなければ RiotError。"""
        pasted = pasted.strip()[:MAX_PASTE]
        with self._lock:
            if "access_token=" in pasted:
                tokens, cookies = _tokens_in(pasted, self.clock()), {}
                if not tokens:
                    raise RiotError("badpaste")
            else:
                cookies = _pasted_cookies(pasted)
                if not cookies.get("ssid"):
                    raise RiotError("badpaste")
                tokens, cookies = self._authorize(cookies)
            tokens["entitlements"] = self._entitlements(tokens["access"])
            account = self._account(tokens)
            saved = self.store.load()
            others, now = _others(saved), str(saved.get("puuid") or "").lower()
            if now and now != account["puuid"]:
                # 別のアカウント: 今まで使っていたものはほかのものと一緒に連携したまま
                if account["puuid"] not in others and len(others) + 2 > ACCOUNTS_MAX:
                    raise RiotError("full")
                others = {key: value for key, value in others.items() if key != account["puuid"]}
                others[now] = _kept_of(saved)
            self.store.save(
                cookies=cookies or None,
                token=tokens,
                signed_out=None,
                problem=None,
                others=others or None,
                linked_first=True if self.local is not None else None,  # 今連携したアカウントを表示する
                **account,
            )
            self._forget()  # 別のアカウントのコレクションは出さない
        return self.status()

    def sign_out(self, puuid: str = "") -> None:
        """アカウントの連携を解除する（名前が無ければ使用中の連携アカウント）。ほかに連携アカウントがあれば、
        以後はそれを使う。"""
        with self._lock:
            saved = self.store.load()
            others, now = _others(saved), str(saved.get("puuid") or "").lower()
            puuid = str(puuid or "").strip().lower() or now
            if puuid and puuid in others:
                del others[puuid]
                self.store.save(others=others or None)
            elif puuid and puuid == now and others:
                following = min(others, key=lambda key: str(others[key].get("name") or "").casefold())
                self.store.save(**_swap({}, others, following), problem=None)
            elif puuid == now:
                self.store.save(**dict.fromkeys(ACCOUNT_KEYS), problem=None, others=None, linked_first=None)
            self._forget()

    def collection(self) -> dict:
        """``{"name", "tag", "wallet": {通貨 uuid: 量} か None, "loadout": PD の playerloadout か None, "owned": {種類: {アイテム uuid: コピー数}}, "instances": {ガンバディのレベル uuid: [各コピーの InstanceID]}, "missing": [読めなかったもの], "problem":
        理由}``。何も読めなければ RiotError。"""
        return self._kept("collection", self._collect)

    def loadout(self) -> dict:
        """今読んだ持ち主の PD の playerloadout（コレクションが持っているものではない）
        : コレクションのページから保存するセットの中身。読めなければ RiotError。"""

        def read(tokens: dict, saved: dict) -> dict:
            base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
            return {"loadout": self._loadout(base, puuid, headers)[0], "missing": [], "problem": ""}

        return self._kept("loadout", read, 0)["loadout"]

    def shop(self) -> dict:
        """今日のストア（shop.py が読む）
        : ``{"name", "tag", "wallet", "storefront": PD の storefront か None, "missing", "problem"}``。
        何も読めなければ RiotError。"""
        return self._kept("shop", self._storefront)

    def queues(self) -> dict:
        """ゲームのキュー設定（GLZ の customgameconfigs: リージョンの全員で同じなので、公開ページで出してよい）
        : ``{"config": Riot の答え, "at", "missing", "problem"}``。"""
        return self._kept("queues", self._queues, QUEUES_KEEP)

    def contracts(self) -> dict:
        """バトルパス、イベントのパス、エージェントのコントラクト、ミッション（PD の contracts）
        : ``{"contracts": PD の答え, "missing", "problem"}``。読めなければ RiotError。"""
        return self._kept("contracts", self._contracts)

    def agents(self) -> set[str]:
        """持ち主のエージェント（uuid。エージェント種類の PD のエンタイトルメント）。
        ピック画面用（なのでゲームをしているアカウントのもの）
        : 1 回の呼び出しで AGENTS_KEEP 持つ（コレクション全部だと 10 回）。読めなければ RiotError。"""

        def read(tokens: dict, saved: dict) -> dict:
            url = f"{self._base(saved)}/store/v1/entitlements/{saved['puuid']}/{ITEM_TYPES['agents']}"
            answer = self._pd("agents", url, self._headers(tokens))
            owned = [
                str(item["ItemID"]).lower()
                for item in answer.get("Entitlements") or []
                if isinstance(item, dict) and item.get("ItemID")
            ]
            return {"agents": owned, "missing": [], "problem": ""}

        return set(self._kept("agents", read, AGENTS_KEEP, game=True)["agents"])

    def live(self) -> dict:
        """今の持ち主のゲームの状態（GLZ）
        : ``{"running": VALORANT が開いているか（None: わからない）, "phase": "menus"、"pregame"（エージェント選択）、"ingame"、""（プレイしていない）, "match": GLZ の試合か None, "party": GLZ のパーティー（メニュー）
        か None, "names": {puuid: [name, tag]}, "ranks": {puuid: PD の mmr}, "mates": 持ち主と同じパーティーのプレイヤー（puuid）,
        "at", "missing", "problem"}``。
        名前は隠していない（``Incognito`` でない）プレイヤーと、持ち主と同じパーティーのプレイヤーのものだけ聞く
        （ゲームも「パーティー外のプレイヤーに名前を隠す」: パーティーの仲間には名前を見せる）。"""
        if self.local is not None and self.local.version != self._local_version:
            # クライアントが変化（試合成立、エージェント選択、ピック）を知らせた: 持っている答えではなく今読む
            self._local_version = self.local.version
            self.cache.put("live", None, 0)
        return self._kept("live", self._live, LIVE_KEEP, game=True)

    def leaderboard(self, region: str, season: str, start: int = 0, size: int = 50, query: str = "") -> dict:
        """ゲーム自身のサーバー（PD）からのリージョンのランク戦ランキングの 1 ページ。
        henrik.Henrik.leaderboard の形: ``{"players": [{"rank", "name", "tag", "puuid", "card", "tier", "rr", "wins", "hidden"}], "thresholds": [{"tier", "rr", "from"}], "total",
        "updated"}``。全プレイヤーで同じなので公開ページで出してよい。
        名前を隠しているプレイヤー（Riot の ``IsAnonymized``）は名前無し。``query``: 探す名前。
        Riot が出さなければ RiotError（そのリージョンはしばらく休む）。"""
        if region not in BOARD_REGIONS or not _UUID.match(season):
            raise RiotError("down", "leaderboard: no such region or act")
        resting = self.boards.get(("resting", region))
        if resting:
            raise RiotError(resting, "leaderboard: asked again later")
        start = max(0, int(start))
        key = _board_key(region, season, start, size, query)
        kept = self.boards.get(key)
        if kept is not None:
            return kept

        def read(tokens: dict, saved: dict) -> dict:
            asked = {"startIndex": start, "size": int(size)} | ({"query": query} if query else {})
            url = (
                f"{PD.format(shard=BOARD_REGIONS[region])}/mmr/v1/leaderboards/affinity/{region}"
                f"/queue/competitive/season/{season}?{urlencode(asked)}"
            )
            return _board(self._pd("leaderboard", url, self._headers(tokens)), self.clock())

        try:
            found = self._read(read)
        except RiotError as error:
            if error.kind in ("busy", "down"):
                self.boards.put(("resting", region), error.kind, BOARD_RESTING)
            raise
        self.boards.put(key, found, BOARD_KEEP)
        return found

    def has_board(self, region: str, season: str, start: int = 0, size: int = 50, query: str = "") -> bool:
        """そのページを持っているか（あれば呼び出しは要らない）。"""
        return self.boards.get(_board_key(region, season, start, size, query)) is not None

    def history(self, region: str, puuid: str, start: int = 0) -> dict:
        """ゲーム自身のサーバー（PD）からの、任意のプレイヤーの試合履歴の 1 ページ（新しい順）
        : ``{"total", "matches": [{"id", "at", "queue"}]}``（pdmatch.history）。無ければ RiotError。"""
        if not _UUID.match(puuid):
            raise RiotError("down", "history: not a puuid")
        asked = urlencode({"startIndex": max(0, int(start)), "endIndex": max(0, int(start)) + HISTORY_PAGE})

        def read(tokens: dict, saved: dict) -> dict:
            url = f"{PD.format(shard=SHARDS.get(region, 'ap'))}/match-history/v1/history/{puuid}?{asked}"
            return pdmatch.history(self._pd("history", url, self._headers(tokens)))

        return self._read(read)

    def match_details(self, region: str, match_id: str) -> dict:
        """ゲーム自身のサーバーが持つ形の終わった試合（PD の match-details。pdmatch.to_v4 が読む）。"""
        if not _UUID.match(match_id):
            raise RiotError("down", "match: not a match id")

        def read(tokens: dict, saved: dict) -> dict:
            url = f"{PD.format(shard=SHARDS.get(region, 'ap'))}/match-details/v1/matches/{match_id}"
            return self._pd("match", url, self._headers(tokens))

        return self._read(read)

    def rr_updates(self, region: str, puuid: str, start: int = 0) -> dict:
        """任意のプレイヤーのコンペティティブの試合と、それぞれの RR の動きの 1 ページ（PD の competitiveupdates）、
        新しい順: ``{"changes": {試合 ID: {"change", "tier", "rr", "elo", "at"}}, "count": ページの試合数}``。"""
        if not _UUID.match(puuid):
            raise RiotError("down", "rr: not a puuid")
        start = max(0, int(start))
        asked = urlencode({"startIndex": start, "endIndex": start + HISTORY_PAGE, "queue": "competitive"})

        def read(tokens: dict, saved: dict) -> dict:
            url = f"{PD.format(shard=SHARDS.get(region, 'ap'))}/mmr/v1/players/{puuid}/competitiveupdates?{asked}"
            answer = self._pd("rr", url, self._headers(tokens))
            listed = answer.get("Matches") if isinstance(answer.get("Matches"), list) else []
            return {"changes": pdmatch.rr_updates(answer), "count": len(listed)}

        return self._read(read)

    def act(self, action: str, value: str = "") -> dict:
        """/live の持ち主のボタンを、ゲーム自身のクライアントと同じように送る: ``start``（値: キュー ID）
        はパーティーのキューを設定してマッチングを始め、``leave`` は止め、
        ``select`` / ``lock``（値: エージェントの uuid）はエージェント選択でピック・ロックする。
        RiotError: ``unknown``（このどれでもない）、``notnow``（パーティーにいない、またはエージェント選択でない）、
        または Riot の言ったこと。"""
        value = str(value or "").strip().lower()
        if (
            action not in ACTIONS
            or (action == "start" and not _QUEUE_ID.match(value))
            or (action in ("select", "lock") and not _UUID.match(value))
        ):
            raise RiotError("unknown")

        def send(tokens: dict, saved: dict) -> dict:
            glz, puuid, headers = self._glz(saved), str(saved["puuid"]), self._headers(tokens)
            posted = {**headers, "Content-Type": "application/json"}
            if action in ("select", "lock"):
                # ページが出しているエージェント選択（前回の確認のもの）: ピックは 2 回ではなく 1 回の呼び出しで送る
                match_id = self.where.get("pregame") or self._now(
                    "pregame", f"{glz}/pregame/v1/players/{puuid}", headers, "MatchID"
                )
                url = f"{glz}/pregame/v1/matches/{match_id}/{action}/{value}"
                answer = self._pd(action, url, headers, "POST", b"")
            else:
                party = self._now("party", f"{glz}/parties/v1/players/{puuid}", headers, "CurrentPartyID")
                base = f"{glz}/parties/v1/parties/{party}"
                if action == "start":
                    self._pd("queue", f"{base}/queue", posted, "POST", json.dumps({"queueId": value}).encode())
                answer = self._pd(
                    action, f"{base}/matchmaking/{'join' if action == 'start' else 'leave'}", posted, "POST", b""
                )
            return {"answer": answer, "missing": [], "problem": ""}

        try:
            return self._kept("act", send, 0, game=True)
        finally:
            self.cache.put("live", None, 0)  # ページが次に出すものを聞き直す

    def _now(self, key: str, url: str, headers: dict, field: str) -> str:
        """今持ち主がいる場所（パーティー、エージェント選択）の GLZ の ID。無ければ RiotError notnow。"""
        try:
            found = str(self._pd(key, url, headers).get(field) or "").lower()
        except RiotError as error:
            if error.kind == "down" and " 404" in f" {error.detail}":
                raise RiotError("notnow", error.detail) from None
            raise
        if not _UUID.match(found):
            raise RiotError("notnow", f"{key}: no {field}")
        return found

    def equip(self, change) -> dict:
        """持ち主のロードアウトを変える: ``change(loadout)`` が PD の playerloadout（新しく読んだもの）
        をその場で書き換え、ゲームクライアントと同じくまるごと送り返す。そのあとコレクションを読み直す。"""

        def send(tokens: dict, saved: dict) -> dict:
            base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
            loadout, url = self._loadout(base, puuid, headers)
            change(loadout)
            # 本文は Subject と Version を除いたロードアウト（新しい Version は PD が付ける）
            loadout.pop("Version", None)
            loadout.pop("Subject", None)
            body = json.dumps(loadout).encode()
            answer = self._pd("equip", url, {**headers, "Content-Type": "application/json"}, "PUT", body)
            if isinstance(answer.get("Guns"), list) and wearing(answer) != wearing(loadout):
                raise RiotError("notsaved", "equip: PD answered 200 with another loadout")
            return {"loadout": answer, "missing": [], "problem": ""}

        try:
            return self._kept("equip", send, 0)
        finally:
            self.cache = TTLCache(8)

    def _contracts(self, tokens: dict, saved: dict) -> dict:
        url = f"{self._base(saved)}/contracts/v1/contracts/{saved['puuid']}"
        return {"contracts": self._pd("contracts", url, self._headers(tokens)), "missing": [], "problem": ""}

    def _glz(self, saved: dict) -> str:
        shard = str(saved.get("shard") or "ap")
        shard = shard if shard in SHARDS.values() and shard != "pbe" else "ap"
        region = str(saved.get("region") or "")
        return GLZ.format(region=region if region in SHARDS and SHARDS[region] == shard else shard, shard=shard)

    def _live(self, tokens: dict, saved: dict) -> dict:
        glz, puuid, headers = self._glz(saved), str(saved["puuid"]), self._headers(tokens)
        # ゲームのセッションで、開いているかとどこにいるか（メニュー、エージェント選択、試合）がわかる。
        # デスクトップアプリでは Riot Client がもう知らせているので（イベント）GLZ には聞かない
        seen = self.local.presence() if self.local is not None else None
        if seen is not None and not seen["running"]:
            self.where = {}
            return {
                "running": False,
                "phase": "",
                "match": None,
                "party": None,
                "names": {},
                "ranks": {},
                "mates": [],
                "at": self.clock(),
                "missing": [],
                "problem": "",
            }
        if seen is not None:
            running, loop = True, seen["loop"]
            if _UUID.match(seen.get("party") or ""):
                # すぐ聞く（そして確かめる: 持ち主がそこにいるはず）。試合中も: パーティーの仲間を知るため
                self.where["party"] = seen["party"]
        else:
            running, loop = self._session(glz, puuid, headers)
        # 前の確認で覚えた試合が持ち主のものなのは、セッションがまだその段階にいる間だけ
        for phase in ("pregame", "ingame"):
            if LOOPS.get(loop) != phase:
                self.where.pop(phase, None)
        if loop == "MENUS":
            found = self._menus(glz, puuid, headers, saved, quick=True)
            state = str((found.get("party") or {}).get("State") or "").upper()
            if "STARTING" in state:
                # 試合が見つかった（ゲームの 5 秒カウント）: セッションが言う前にエージェント選択がある
                early = self._match(glz, puuid, headers, saved, ("pregame",))
                if early["phase"]:
                    return early | {"running": True}
            if any(word in state for word in QUEUEING):
                self._read_agents_ahead()
            return found
        first = LOOPS.get(loop, "pregame")
        found = self._match(glz, puuid, headers, saved, sorted(dict(PHASES), key=lambda phase: phase != first))
        if found["phase"]:
            return found | {"running": True}
        if running is None:  # セッションが読めなかった: パーティーでゲームが開いているかわかる
            return self._menus(glz, puuid, headers, saved)
        return found | {"running": running}

    def _session(self, glz: str, puuid: str, headers: dict) -> tuple[bool | None, str]:
        """持ち主の GLZ のセッション: （ゲームが開いているか。None は不明, その loopState）。"""
        try:
            session = self._pd("session", f"{glz}/session/v1/sessions/{puuid}", headers)
        except RiotError as error:
            if error.kind == "expired":
                raise
            return (False if error.kind == "down" and " 404" in f" {error.detail}" else None), ""
        loop = str(session.get("loopState") or "").upper()
        return str(session.get("cxnState") or "CONNECTED").upper() == "CONNECTED" and bool(loop), loop

    def _read_agents_ahead(self) -> None:
        """パーティーがマッチング中に読んでおく持ち主のエージェント: エージェント選択がそれを待たずに開く（AGENTS_KEEP
        持つので、聞くのはたまにだけ）。"""
        if self.cache.get("agents") is None and not self.cache.get("agents-resting"):
            try:
                self.agents()
            except RiotError:
                # 確認のたびには聞き直さない。エージェント選択でもう 1 回聞き、失敗したら全エージェントを出す
                self.cache.put("agents-resting", True, 300)

    def _menus(self, glz: str, puuid: str, headers: dict, saved: dict, quick: bool = False) -> dict:
        """メニューで: 持ち主のパーティー（キュー、マッチング中か、メンバー）。
        ``quick``: セッションが持ち主はメニューにいると言うので、前回の確認のパーティーをすぐ聞く。"""
        empty = {"phase": "", "match": None, "party": None, "names": {}, "ranks": {}, "mates": [], "at": self.clock()}
        party = None
        if quick and self.where.get("party"):
            party = self._remembered("party", f"{glz}/parties/v1/parties/{self.where['party']}", headers)
            members = party.get("Members") if isinstance(party, dict) else None
            if not any(isinstance(item, dict) and str(item.get("Subject")).lower() == puuid for item in members or []):
                party = None  # そのパーティーを離れた
        if party is None:
            self.where.pop("party", None)
            try:
                party_id = self._now("party", f"{glz}/parties/v1/players/{puuid}", headers, "CurrentPartyID")
            except RiotError as error:
                if error.kind != "notnow":
                    raise
                return empty | {"running": False, "missing": [], "problem": ""}
            party = self._pd("party", f"{glz}/parties/v1/parties/{party_id}", headers)
            self.where["party"] = party_id
        members = [item for item in party.get("Members") or [] if isinstance(item, dict)]
        mates = {str(item.get("Subject") or "").lower() for item in members} - {"", puuid}
        names, problem = self._open_names(saved, headers, members, mates)
        return empty | {
            "running": True,
            "phase": "menus",
            "party": party,
            "names": names,
            "mates": sorted(mates),
            "missing": ["names"] if problem else [],
            "problem": problem,
        }

    def _remembered(self, key: str, url: str, headers: dict) -> dict | None:
        """前の確認で覚えたアドレスでの GLZ の答え、または None（もう無い: 持ち主の居場所を聞き直す）。"""
        try:
            return self._pd(key, url, headers)
        except RiotError as error:
            if error.kind == "expired":
                raise
            return None

    def _open_names(self, saved: dict, headers: dict, players: list, mates=frozenset()) -> tuple[dict, str]:
        """名前を隠していないプレイヤーと、持ち主と同じパーティーのプレイヤー（``mates``）の名前と、問題。
        ほかの名前を隠している人のものは決して聞かない。"""
        open_names = [
            str(item.get("Subject")).lower()
            for item in players
            if isinstance(item, dict)
            and item.get("Subject")
            and (not (item.get("PlayerIdentity") or {}).get("Incognito") or str(item.get("Subject")).lower() in mates)
        ]
        try:
            return self._names(saved, headers, open_names), ""
        except RiotError as error:
            if error.kind == "expired":
                raise
            return {}, error.detail or error.kind

    def _ranks(self, saved: dict, headers: dict, subjects: list[str]) -> dict:
        """各プレイヤーの PD の mmr（ゲーム自身のスコアボードと同じランク）。RANK_KEEP 持つ。
        まだ無いランクは同時に読み、最大 RANK_WAIT 待つ: 残りはあとの確認で来る。"""
        found, wanted = {}, []
        for subject in subjects:
            kept = self.ranks.get(subject)
            if kept is not None:
                found[subject] = kept
            elif _UUID.match(subject):
                wanted.append(subject)
        base = self._base(saved)

        def ask(subject: str) -> dict:
            try:
                answer = self._pd("rank", f"{base}/mmr/v1/players/{subject}", headers)
            except RiotError:
                answer = {}  # この試合ではもう聞かない: ページは GLZ のバッジにする
            self.ranks.put(subject, answer, RANK_KEEP)
            with self._asking_lock:
                self._asking.discard(subject)
            return answer

        asked = {}
        with self._asking_lock:
            for subject in wanted:
                if subject not in self._asking:
                    self._asking.add(subject)
                    asked[subject] = self._ranker.submit(ask, subject)
        if asked:
            done, _ = wait(asked.values(), timeout=RANK_WAIT)
            found |= {subject: future.result() for subject, future in asked.items() if future in done}
        return found

    def _match(self, glz: str, puuid: str, headers: dict, saved: dict, phases=("pregame", "ingame")) -> dict:
        """持ち主がいるエージェント選択か試合（どちらでもなければ ``phase`` は ""）。GLZ には ``phases`` の順で聞く。
        前回の確認の試合はすぐ聞く（2 回ではなく 1 回の呼び出し）。"""
        paths = dict(PHASES)
        for phase in phases:
            path = paths[phase]
            match, match_id = None, self.where.get(phase, "")
            if match_id:
                match = self._remembered(phase, f"{glz}{path}/matches/{match_id}", headers)
                if match is not None and puuid not in _subjects(match, phase):
                    match = None
            if match is None:
                self.where.pop(phase, None)
                try:
                    player = self._pd(phase, f"{glz}{path}/players/{puuid}", headers)
                except RiotError as error:
                    if error.kind == "down" and " 404" in f" {error.detail}":
                        continue  # この段階にはいない
                    raise
                match_id = str(player.get("MatchID") or "").lower()
                if not _UUID.match(match_id):
                    continue
                match = self._pd(phase, f"{glz}{path}/matches/{match_id}", headers)
                self.where[phase] = match_id
            players = _players(match, phase)
            mates = self._mates(glz, puuid, headers, match_id, players)
            names, problem = self._open_names(saved, headers, players, mates)
            ranks = self._ranks(
                saved, headers, [str(item.get("Subject") or "").lower() for item in players if not item.get("IsCoach")]
            )
            return {
                "phase": phase,
                "match": match,
                "party": None,
                "names": names,
                "ranks": ranks,
                "mates": sorted(mates),
                "at": self.clock(),
                "missing": ["names"] if problem else [],
                "problem": problem,
            }
        return {
            "phase": "",
            "match": None,
            "party": None,
            "names": {},
            "ranks": {},
            "mates": [],
            "at": self.clock(),
            "missing": [],
            "problem": "",
        }

    def _mates(self, glz: str, puuid: str, headers: dict, match_id: str, players: list) -> set[str]:
        """試合（エージェント選択、対戦中）で持ち主と同じパーティーにいるプレイヤー（持ち主を除く）。
        推測ではなく Riot 自身の GLZ のパーティーのメンバーで決める。GLZ の試合にはパーティーが無いので、
        持ち主のパーティーを試合ごとに 1 回だけ読む（MATES_KEEP）。読めなければ空: 名前は隠したまま。"""
        key = (puuid, match_id)
        members = self.mates.get(key)
        if members is None:
            found = self._party_members(glz, puuid, headers)
            members = found if found is not None else frozenset()
            self.mates.put(key, members, MATES_KEEP if found is not None else MATES_RETRY)
        in_match = {str(item.get("Subject") or "").lower() for item in players if not item.get("IsCoach")}
        return (members & in_match) - {puuid}

    def _party_members(self, glz: str, puuid: str, headers: dict) -> frozenset[str] | None:
        """持ち主の今の GLZ のパーティーのメンバー（puuid）。読めなければ None。"""
        try:
            party = None
            if self.where.get("party"):
                party = self._remembered("party", f"{glz}/parties/v1/parties/{self.where['party']}", headers)
            members = (party or {}).get("Members") if isinstance(party, dict) else None
            subjects = {str(item.get("Subject") or "").lower() for item in members or [] if isinstance(item, dict)}
            if puuid not in subjects:  # 覚えたパーティーを離れた、またはまだ知らない: 今のパーティーを聞く
                party_id = self._now("party", f"{glz}/parties/v1/players/{puuid}", headers, "CurrentPartyID")
                party = self._pd("party", f"{glz}/parties/v1/parties/{party_id}", headers)
                self.where["party"] = party_id
                members = party.get("Members") if isinstance(party, dict) else None
                subjects = {str(item.get("Subject") or "").lower() for item in members or [] if isinstance(item, dict)}
        except RiotError as error:
            if error.kind == "expired":
                raise
            return None
        return frozenset(subjects - {""}) if puuid in subjects else None

    def _names(self, saved: dict, headers: dict, subjects: list[str]) -> dict:
        """PD の名前サービス: このプレイヤーたちの ``{puuid: [ゲーム名, タグ]}``。
        それぞれ NAME_KEEP 持つ（パーティーと試合は数秒ごとに見る）。"""
        found, wanted = {}, []
        for subject in subjects:
            kept = self.names.get(subject)
            if kept is not None:
                found[subject] = kept
            else:
                wanted.append(subject)
        if not wanted:
            return found
        status, _, body = self.send(
            "PUT",
            f"{self._base(saved)}/name-service/v2/players",
            {**headers, "Content-Type": "application/json"},
            json.dumps(wanted).encode(),
        )
        if status == 401:
            raise RiotError("expired", "names 401")
        if status != 200:
            raise RiotError("busy" if status == 429 else "down", f"names {status or 'no answer'}")
        try:
            answer = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            answer = []
        for item in answer if isinstance(answer, list) else []:
            subject = str(item.get("Subject") or "").lower() if isinstance(item, dict) else ""
            if subject in wanted and item.get("GameName"):
                found[subject] = [str(item["GameName"]), str(item.get("TagLine") or "")]
                self.names.put(subject, found[subject], NAME_KEEP)
        return found

    def _queues(self, tokens: dict, saved: dict) -> dict:
        url = self._glz(saved) + "/parties/v1/parties/customgameconfigs"
        return {
            "config": self._pd("queues", url, self._headers(tokens)),
            "at": self.clock(),
            "missing": [],
            "problem": "",
        }

    def _kept(self, key: str, read, seconds: float = KEEP, game: bool = False) -> dict:
        """``read(tokens, saved)`` の答えを ``seconds`` 持つ（0: ボタン。押すたびに実行する。
        Windows の時計は 16 ms 刻みなので、0 秒持つ答えが次の押下にまだ残っていた）。
        断られたトークンは 1 回だけ新しいものと交換する。``game``: この PC でゲームをしているアカウントとして聞く。"""
        kept = self.cache.get(key) if seconds > 0 else None
        if kept is not None:
            return kept
        found = self._read(read, game)
        if (self.store.load().get("problem") or None) != (found["problem"] or None):  # 確認のたびには書かない
            self.store.save(problem=found["problem"] or None)
        if seconds > 0:
            self.cache.put(key, found, seconds)
        return found

    def _read(self, read, game: bool = False) -> dict:
        """``read(tokens, saved)``。断られたトークンは 1 回だけ新しいものと交換する。"""
        for renew in (False, True):
            tokens, saved = self._tokens(renew, game)
            try:
                return read(tokens, saved)
            except RiotError as error:
                if error.kind != "expired":
                    raise
                if renew:  # 作ったばかりのトークンも断られる: cookie のせいではない
                    raise RiotError("down", error.detail) from None
        raise RiotError("down")  # ここには来ない

    # -- トークン ------------------------------------------------------------------------------------

    def _tokens(self, renew: bool = False, game: bool = False) -> tuple[dict, dict]:
        """1 時間のトークン（この PC の Riot Client のもの、無ければ保存したもの、それも無ければ cookie と交換）と、
        保存しているもの（クライアントのアカウントつき）。"""
        found = self._client(renew, game)
        if found is not None:
            tokens, account = found
            return tokens, {**self.store.load(), **account}
        with self._lock:
            saved = self.store.load()
            if not saved.get("puuid"):
                raise RiotError("nologin")
            token = saved.get("token") if isinstance(saved.get("token"), dict) else {}
            if (
                not renew
                and token.get("access")
                and token.get("entitlements")
                and to_float(token.get("until")) > self.clock()
            ):
                return token, saved
            cookies = saved.get("cookies") if isinstance(saved.get("cookies"), dict) else {}
            if not cookies:  # 1 時間のアドレスで、もう過ぎた
                self.store.save(token=None, signed_out=True)
                raise RiotError("signedout")
            try:
                token, cookies = self._authorize(cookies)
                token["entitlements"] = self._entitlements(token["access"])
            except RiotError as error:
                # cookie は残す: 断られたのは一時的かもしれない（それに持ち主がまた貼るのも同じもの）
                self.store.save(token=None, signed_out=True if error.kind == "signedout" else None)
                raise
            self.store.save(cookies=cookies, token=token, signed_out=None)
            return token, {**saved, "cookies": cookies, "token": token}

    def _authorize(self, cookies: dict) -> tuple[dict, dict]:
        """cookie -> （1 時間のトークン, Riot が更新した cookie）。"""
        header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        status, headers, _ = self.send("GET", AUTHORIZE, {"Cookie": header, "User-Agent": self._agent()}, None)
        _check(status, "auth")
        tokens = _tokens_in(_header(headers, "location"), self.clock())
        if not tokens:  # 代わりにサインインページに送られた
            raise RiotError("signedout", f"auth {status}")
        return tokens, _renewed(cookies, headers)

    def _entitlements(self, access: str) -> str:
        status, _, body = self.send(
            "POST",
            ENTITLEMENTS,
            {"Authorization": f"Bearer {access}", "Content-Type": "application/json", "User-Agent": self._agent()},
            b"{}",
        )
        if status in (400, 401, 403):
            raise RiotError("signedout", f"entitlements {status}")
        _check(status, "entitlements")
        token = str(_json(body).get("entitlements_token") or "")
        if not _TOKEN.match(token):
            raise RiotError("down", "entitlements: no token")
        return token

    def _account(self, tokens: dict) -> dict:
        """サインインしているアカウントの ``{"puuid", "name", "tag", "shard"}``。"""
        bearer = {"Authorization": f"Bearer {tokens['access']}", "User-Agent": self._agent()}
        status, _, body = self.send("GET", USERINFO, bearer, None)
        if status in (400, 401, 403):
            raise RiotError("signedout", f"userinfo {status}")
        info = _json(body) if status == 200 else {}
        puuid = str(info.get("sub") or token_claims(tokens["access"]).get("sub") or "").lower()
        if not puuid:
            raise RiotError("down", f"userinfo {status}")
        acct = info.get("acct") if isinstance(info.get("acct"), dict) else {}
        shard = ""
        if tokens.get("id"):
            status, _, body = self.send(
                "PUT",
                GEO,
                {**bearer, "Content-Type": "application/json"},
                json.dumps({"id_token": tokens["id"]}).encode(),
            )
            affinities = _json(body).get("affinities") if status == 200 else None
            live = affinities.get("live") if isinstance(affinities, dict) else ""
            shard = SHARDS.get(str(live or "").lower(), "")
        return {
            "puuid": puuid,
            "name": str(acct.get("game_name") or ""),
            "tag": str(acct.get("tag_line") or ""),
            "shard": shard or "ap",  # 持ち主は日本で遊ぶ: Riot が言わなければアジア太平洋
        }

    def _agent(self) -> str:
        build = str((self.version() or {}).get("riotClientBuild") or "99.0.0.0000000.0000000")
        return f"RiotClient/{build} rso-auth (Windows;10;;Professional, x64)"

    # -- PD -------------------------------------------------------------------------------------------

    def _headers(self, tokens: dict) -> dict:
        return {
            "Authorization": f"Bearer {tokens['access']}",
            "X-Riot-Entitlements-JWT": str(tokens["entitlements"]),
            "X-Riot-ClientPlatform": PLATFORM,
            "X-Riot-ClientVersion": str(tokens.get("version") or (self.version() or {}).get("riotClientVersion") or ""),
            "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit",
            "Accept": "application/json",
        }

    @staticmethod
    def _base(saved: dict) -> str:
        shard = str(saved.get("shard") or "ap")
        return PD.format(shard=shard if shard in SHARDS.values() else "ap")

    def _storefront(self, tokens: dict, saved: dict) -> dict:
        """ストア（PD の storefront: 今日の 4 スキン、バンドル、ナイトマーケット、アクセサリー）とウォレットを同時に。"""
        base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
        with ThreadPoolExecutor(2, thread_name_prefix="riot") as pool:
            asked = {
                # v3 は 2024 年から POST（v2 の GET は止められた）
                "storefront": pool.submit(
                    self._pd,
                    "storefront",
                    f"{base}/store/v3/storefront/{puuid}",
                    {**headers, "Content-Type": "application/json"},
                    "POST",
                    b"{}",
                ),
                "wallet": pool.submit(self._pd, "wallet", f"{base}/store/v1/wallet/{puuid}", headers),
            }
        answers, missing, problems = _gather(asked)
        if "storefront" not in answers:
            raise RiotError("down", problems[0] if problems else "")
        return {
            "name": str(saved.get("name") or ""),
            "tag": str(saved.get("tag") or ""),
            "wallet": _wallet(answers.get("wallet")),
            "storefront": answers["storefront"],
            "at": self.clock(),
            "missing": missing,
            "problem": ", ".join(dict.fromkeys(problems)),
        }

    def _collect(self, tokens: dict, saved: dict) -> dict:
        base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
        asks = {"wallet": f"/store/v1/wallet/{puuid}"}
        asks |= {kind: f"/store/v1/entitlements/{puuid}/{item_type}" for kind, item_type in ITEM_TYPES.items()}
        with ThreadPoolExecutor(4, thread_name_prefix="riot") as pool:
            asked = {"loadout": pool.submit(lambda: self._loadout(base, puuid, headers)[0])}
            asked |= {key: pool.submit(self._pd, key, base + path, headers) for key, path in asks.items()}
        answers, missing, problems = _gather(asked)
        if not answers:
            raise RiotError("down", problems[0] if problems else "")
        owned, instances = {}, {}
        for kind in ITEM_TYPES:
            if kind not in answers:
                continue
            counts: dict[str, int] = {}
            for item in answers[kind].get("Entitlements") or []:
                if isinstance(item, dict) and item.get("ItemID"):
                    key = str(item["ItemID"]).lower()
                    counts[key] = counts.get(key, 0) + 1
                    if kind == "buddies" and item.get("InstanceID"):  # コピーごとに 1 つの武器のもの（CharmInstanceID）
                        instances.setdefault(key, []).append(str(item["InstanceID"]).lower())
            owned[kind] = counts
        wallet = _wallet(answers.get("wallet"))
        return {
            "name": str(saved.get("name") or ""),
            "tag": str(saved.get("tag") or ""),
            "wallet": wallet,
            "loadout": answers.get("loadout"),
            "owned": owned,
            "instances": instances,
            "missing": missing,
            "problem": ", ".join(dict.fromkeys(problems)),
        }

    def _loadout(self, base: str, puuid: str, headers: dict) -> tuple[dict, str]:
        """持ち主の PD の playerloadout と、その出どころのアドレス（LOADOUTS: 無ければ次のもの）。"""
        for number, path in enumerate(LOADOUTS):
            url = base + path.format(puuid=puuid)
            try:
                return self._pd("loadout", url, headers), url
            except RiotError as error:
                gone = error.kind == "down" and " 404" in f" {error.detail}"
                if not gone or number == len(LOADOUTS) - 1:
                    raise
        raise RiotError("down", "loadout")  # ここには来ない

    def _pd(self, key: str, url: str, headers: dict, method: str = "GET", body: bytes | None = None) -> dict:
        status, _, body = self.send(method, url, headers, body)
        said = _json(body)
        code = str(said.get("errorCode") or "")
        if status == 401 or code in ("BAD_CLAIMS", "TOKEN_EXPIRED", "INVALID_TOKEN"):
            raise RiotError("expired", f"{key} {status} {code}".strip())
        if status != 200:
            kind = "busy" if status == 429 else "down"
            raise RiotError(kind, f"{key} {status or 'no answer'} {code}".strip())
        return said


def _others(saved: dict) -> dict:
    """使用中でない連携アカウント: ``{puuid: riot.json がそれぞれについて持つもの}``。"""
    others = saved.get("others")
    return {
        str(puuid).lower(): item
        for puuid, item in (others.items() if isinstance(others, dict) else [])
        if isinstance(item, dict) and item.get("puuid")
    }


def _kept_of(saved: dict) -> dict:
    """ほかのものの中に入れるための、riot.json が使用中のアカウントについて持つもの。"""
    return {key: saved[key] for key in ACCOUNT_KEYS if saved.get(key) not in ("", None)}


def _swap(saved: dict, others: dict, puuid: str) -> dict:
    """``puuid``（``others`` の 1 つ）を使用中にし、今まで使っていたもの（あれば ``saved`` のもの）
    をほかのものに入れるために保存するもの。"""
    rest = {key: value for key, value in others.items() if key != puuid}
    if saved.get("puuid"):
        rest[str(saved["puuid"]).lower()] = _kept_of(saved)
    return {key: others[puuid].get(key) for key in ACCOUNT_KEYS} | {"others": rest or None}


def _row(account: dict) -> dict:
    return {
        "puuid": str(account.get("puuid") or "").lower(),
        "name": str(account.get("name") or ""),
        "tag": str(account.get("tag") or ""),
        "shard": str(account.get("shard") or ""),
    }


def _players(match: dict, phase: str) -> list[dict]:
    """GLZ のエージェント選択（持ち主のチーム）か試合のプレイヤー。"""
    team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
    return [
        item
        for item in (team.get("Players") if phase == "pregame" else match.get("Players")) or []
        if isinstance(item, dict)
    ]


def _subjects(match: dict, phase: str) -> set[str]:
    return {lower_id(item.get("Subject")) for item in _players(match, phase)}


def _board_key(region: str, season: str, start: int, size: int, query: str) -> tuple:
    """ランキングの 1 ページのキャッシュのキー。"""
    return ("board", region, season, max(0, int(start)), int(size), query.casefold())


def _gather(asked: dict) -> tuple[dict, list[str], list[str]]:
    """同時に頼んだ PD の答え（キー → future）を集める: (答え, 来なかったキー, 理由)。トークン切れはそのまま上げる。"""
    answers, missing, problems = {}, [], []
    for key, future in asked.items():
        try:
            answers[key] = future.result()
        except RiotError as error:
            if error.kind == "expired":
                raise
            missing.append(key)
            problems.append(error.detail or error.kind)
    return answers, missing, problems


def _wallet(answer) -> dict | None:
    """PD のウォレット -> ``{通貨 uuid: 量}``（無ければ None）。"""
    balances = (answer or {}).get("Balances") if isinstance(answer, dict) else None
    if not isinstance(balances, dict):
        return None
    return {str(key).lower(): int(value) for key, value in balances.items() if isinstance(value, int | float)}


def _board(answer: dict, now: float) -> dict:
    """PD のランキング -> henrik.Henrik.leaderboard の形。隠しているプレイヤーは、ゲームと同じく順位・ランク・RR・
    勝利数だけ（名前・puuid・カード無し）。"""
    if not isinstance(answer.get("Players"), list):
        raise RiotError("down", "leaderboard: no players")
    players = []
    for item in answer["Players"]:
        if not isinstance(item, dict):
            continue
        hidden = bool(item.get("IsAnonymized")) or not item.get("gameName")
        players.append(
            {
                "rank": int(to_float(item.get("leaderboardRank"))),
                "name": "" if hidden else str(item.get("gameName") or ""),
                "tag": "" if hidden else str(item.get("tagLine") or ""),
                "puuid": "" if hidden else str(item.get("puuid") or "").lower(),
                "card": "" if hidden else str(item.get("PlayerCardID") or "").lower(),
                "tier": int(to_float(item.get("competitiveTier"))),
                "rr": int(to_float(item.get("rankedRating"))),
                "wins": int(to_float(item.get("numberOfWins"))),
                "hidden": hidden,
            }
        )
    details = answer.get("tierDetails") if isinstance(answer.get("tierDetails"), dict) else {}
    details = {
        int(to_float(tier)): info for tier, info in details.items() if isinstance(info, dict) and to_float(tier) > 2
    }
    # 最上位のランクは 1 位から始まる。Riot が順位を付けない（0）下のランクは一覧に無い
    thresholds = [
        {
            "tier": tier,
            "rr": int(to_float(info.get("rankedRatingThreshold"))),
            "from": int(to_float(info.get("startingIndex"))) + 1,
        }
        for tier, info in details.items()
        if tier == max(details) or to_float(info.get("startingIndex")) > 0
    ]
    return {
        "players": players,
        "thresholds": sorted(thresholds, key=lambda item: -item["tier"]),
        "total": int(to_float(answer.get("totalPlayers"))),
        "updated": datetime.fromtimestamp(now, UTC).isoformat(),  # Riot が言わない: 読んだ時刻
    }


def _check(status: int, what: str) -> None:
    if status == 429:
        raise RiotError("busy", f"{what} 429")
    if status == 0 or status >= 500:
        raise RiotError("down", f"{what} {status or 'no answer'}")


def wearing(loadout: dict) -> tuple:
    """PD が変更を残したかを見るための、playerloadout が着けているもの: 各武器のスキン・レベル・バリアント・ガンバディ、
    ホイール（v3 の ActiveExpressions か v2 の Sprays）、カード、称号（collection.py: 保存セットが今の装備と同じか）。"""

    def low(value) -> str:
        return str(value or "").lower()

    guns = {
        low(gun.get("ID")): tuple(low(gun.get(key)) for key in ("SkinID", "SkinLevelID", "ChromaID", "CharmID"))
        for gun in loadout.get("Guns") or []
        if isinstance(gun, dict)
    }
    places = loadout.get("ActiveExpressions") if isinstance(loadout.get("ActiveExpressions"), list) else None
    if places is None:
        wheel = [
            (low(item.get("EquipSlotID")), low(item.get("SprayID")))
            for item in loadout.get("Sprays") or []
            if isinstance(item, dict)
        ]
    else:
        wheel = [(low(item.get("TypeID")), low(item.get("AssetID"))) for item in places if isinstance(item, dict)]
    identity = loadout.get("Identity") if isinstance(loadout.get("Identity"), dict) else {}
    return guns, wheel, low(identity.get("PlayerCardID")), low(identity.get("PlayerTitleID"))


def _json(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}


def _header(headers: list, name: str) -> str:
    return next((str(value) for key, value in headers if str(key).lower() == name), "")


def _tokens_in(address: str, now: float) -> dict | None:
    """Riot のサインインがブラウザーを送るアドレス -> ``{"access", "id", "until"}``（トークンが無ければ None）。"""
    parts = urlsplit(str(address or "").strip())
    found = parse_qs(parts.fragment) or parse_qs(parts.query)
    access = (found.get("access_token") or [""])[0]
    if not _TOKEN.match(access):
        return None
    try:
        seconds = int((found.get("expires_in") or ["3600"])[0])
    except ValueError:
        seconds = 3600
    id_token = (found.get("id_token") or [""])[0]
    return {"access": access, "id": id_token if _TOKEN.match(id_token) else "", "until": now + seconds - EARLY}


def _pasted_cookies(text: str) -> dict:
    """``ssid`` だけ（その値）、``ssid=...``、または Cookie ヘッダーまるごと（``Cookie: a=1; ssid=...``）。"""
    text = re.sub(r"^\s*cookie\s*:\s*", "", text.strip(), flags=re.IGNORECASE).strip().strip("\"'")
    if "=" not in text:
        return {"ssid": text} if _COOKIE_VALUE.match(text) else {}
    found = {}
    for part in text.split(";"):
        name, _, value = part.strip().partition("=")
        value = value.strip().strip('"')
        if _COOKIE_NAME.match(name) and _COOKIE_VALUE.match(value):
            found[name] = value
    return dict(list(found.items())[:30])


def _renewed(cookies: dict, headers: list) -> dict:
    """Riot の Set-Cookie の答えを当てた cookie（毎回新しい ssid。消されたものは消す）。"""
    found = dict(cookies)
    for key, value in headers:
        if str(key).lower() != "set-cookie":
            continue
        first, *rest = str(value).split(";")
        name, _, content = first.strip().partition("=")
        if not _COOKIE_NAME.match(name):
            continue
        gone = not content or any(item.strip().lower() == "max-age=0" for item in rest)
        if gone:
            found.pop(name, None)
        elif _COOKIE_VALUE.match(content):
            found[name] = content
    return dict(list(found.items())[:30])
