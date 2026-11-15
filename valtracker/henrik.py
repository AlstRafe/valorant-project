"""プレイヤーと試合を HenrikDev API（https://docs.henrikdev.xyz）から: どの Riot ID にも答える非公式 API。
キーは持ち主がダッシュボードで作る無料のもの（settings.py が持つ）。

Riot 自身の API は VALORANT の試合に答える前に本番審査が必要なので、こちらを使う。
キーの呼び出し回数は訪問者全員で分け合うので、答えはしばらく持ち（TTLCache）、呼び出しは 1 つの予算を分け合う（Budget）
。"""

from __future__ import annotations

import contextlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlencode

from valtracker import net, rounds
from valtracker.cache import Budget, TTLCache
from valtracker.util import to_int

BASE = "https://api.henrikdev.xyz"
PLATFORM = "pc"
#: 答えの種類ごとに持つ秒数。終わった試合は変わらない。
KEEP = {
    "account": 6 * 3600,
    "mmr": 300,
    "rr": 180,
    "matches": 180,
    "match": 7 * 24 * 3600,
    "leaderboard": 600,
    "status": 300,
    "featured": 1800,
    "news": 1800,
    "esports": 300,
    # HenrikDev が持つ、プレイヤーのそのモードの全試合（seasons.py）: そのページでだけ頼む
    "stored": 1800,
}
#: プレイヤーのシーズンを数えるモード（保存済み試合。デスマッチには数えるラウンドもチームも無い）。
STORED_MODES = ("competitive", "unrated", "swiftplay", "premier")
#: 保存済み試合のモード（``meta.mode``、小文字）-> v4 の試合のキュー ID。
STORED_QUEUES = {
    "spike rush": "spikerush",
    "team deathmatch": "hurm",
    "escalation": "ggteam",
    "replication": "onefa",
    "snowball fight": "snowball",
}
#: プレイヤーページは読んでからこの秒数が過ぎたら読み直せる（「データを更新」）。
FRESH_AFTER = 60
#: 新しい呼び出しが失敗したとき用に、「データを更新」で捨てたものを手元に置く秒数。
SPARE_KEEP = 120
#: HenrikDev が答えないとき用に、各呼び出しの最後の答えを手元に置く秒数。
LAST_KEEP = 24 * 3600
#: ランキング 1 ページの人数。
BOARD_PAGE = 50
#: Riot ID: 名前（3〜16 文字、空白可）とタグ（英数字 3〜5 文字）。
NAME = re.compile(r"^[^#/\\?<>\x00-\x1f]{1,16}$")
TAG = re.compile(r"^[^\W_]{2,5}$")
#: 全角の ０-９、Ａ-Ｚ、ａ-ｚ -> 半角。
HALF_WIDTH = {
    code: code - 0xFEE0
    for start, end in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
    for code in range(start, end + 1)
}
MATCH_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REGIONS = ("ap", "br", "eu", "kr", "latam", "na")
#: プレイヤーページに並べる試合数: ゲーム自身のキャリアページと同じ。
RECENT = 15
#: 1 回の答えの最大試合数。一覧は 2 回に分けて同時に頼む。
PAGE = 10
#: プレイヤーの試合を絞れるモード: HenrikDev の名前 -> その試合のキュー ID。
MODES = {
    "competitive": "competitive",
    "unrated": "unrated",
    "swiftplay": "swiftplay",
    "spikerush": "spikerush",
    "deathmatch": "deathmatch",
    "teamdeathmatch": "hurm",
    "escalation": "ggteam",
    "premier": "premier",
}
#: ページが使う試合の部分。全ラウンド・全キル（1 試合 0.5 MB ほど）は ``digest``（rounds.py）
#: にまとめてから捨てて保存するので、小さな VM のメモリに何百試合も入る。
METADATA = (
    "match_id",
    "map",
    "game_length_in_ms",
    "started_at",
    "is_completed",
    "queue",
    "season",
    "region",
    "cluster",
)
PLAYER = ("puuid", "name", "tag", "team_id", "party_id", "agent", "stats", "tier", "account_level", "economy")
TEAM = ("team_id", "rounds", "won")


class HenrikError(Exception):
    """答えが無い理由: ``kind`` は nokey（API キー未設定）、badkey（キーを断られた）、notfound、
    busy（今は呼び出しが多すぎる。こちらかあちらか）、down（答えが無い、または読めない）のどれか。"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind = kind
        #: 返ってきたもの（持ち主に見せる用。``HTTP 500``、``no answer`` など）。
        self.detail = detail


def split_riot_id(text: str) -> tuple[str, str] | None:
    """``Name#TAG``（全角の ＃ も、前後の空白も可）-> (name, tag)、または None。日本語キーボードの全角文字（ＪＰ１）
    で入れたタグも同じタグ: Riot のタグは半角。"""
    name, mark, tag = text.strip().replace("＃", "#").rpartition("#")
    name, tag = name.strip(), tag.strip().translate(HALF_WIDTH)
    if not mark or not NAME.match(name) or not TAG.match(tag):
        return None
    return name, tag


class Henrik:
    def __init__(self, key, fetch: net.Fetch = net.fetch, budget: Budget | None = None):
        #: () -> API キー（未設定なら ""）: 呼び出しのたびに読むので、設定で保存したキーがすぐ効く。
        self.key = key
        self.fetch = fetch
        self.budget = budget or Budget(25, 60.0)
        self.cache = TTLCache(1024)
        #: キャッシュのキー -> 答えを読んだ時刻（unix 秒）。答えと同じ間持つ。
        self.read_times = TTLCache(1024)
        #: キャッシュのキー -> 「データを更新」で捨てた (答え, 読んだ時刻): HenrikDev が答えられなければまた出す。
        self.spares = TTLCache(64)
        #: 各呼び出しの最後の答え。HenrikDev が答えないときに出す（LAST_KEEP）
        self.lasts = TTLCache(128)

    def account(self, name: str, tag: str) -> dict:
        """``{"puuid", "region", "account_level", "name", "tag", "card", "title"}``。"""
        path = f"/valorant/v2/account/{_part(name)}/{_part(tag)}"
        return self._get(("account", name.lower(), tag.lower()), path, KEEP["account"])

    def mmr(self, region: str, name: str, tag: str) -> dict:
        """``{"current": {"tier": {"id", "name"}, "rr", "last_change", "games_needed_for_rating", "leaderboard_placement", ...}, "peak": ..., "seasons": プレイした全アクト、新しい順（_slim_act）
        }``。"""
        path = f"/valorant/v3/mmr/{known_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}"
        return self._get(("mmr", region, name.lower(), tag.lower()), path, KEEP["mmr"], _slim_mmr)

    def rr_changes(self, region: str, name: str, tag: str) -> dict:
        """最近のコンペティティブの試合の
        RR: ``{試合 ID: {"change": +18, "tier": 21, "rr": 42, "elo": 1842, "at": unix 秒}}``（elo: ランクと RR を 1
        つの数にしたもの。1 ランク 100）。"""
        path = f"/valorant/v1/mmr-history/{known_region(region)}/{_part(name)}/{_part(tag)}"
        return self._get(("rr", region, name.lower(), tag.lower()), path, KEEP["rr"], _slim_rr)

    def matches(self, region: str, name: str, tag: str, mode: str = "") -> list:
        """最新 RECENT 試合（MODES のどれか、または全部）、新しい順。どれも ``match`` が返す形。
        新しい方の部分が無ければ一覧は無し（HenrikError）、古い方が無ければ短くなる。"""
        with ThreadPoolExecutor(2, thread_name_prefix="henrik-matches") as pool:
            parts = [pool.submit(self._matches, region, name, tag, mode, start) for start in (0, PAGE)]
        found = parts[0].result()
        with contextlib.suppress(HenrikError):
            found = found + parts[1].result()
        wanted = MODES.get(mode)
        seen, kept = set(), []
        for match in found:
            metadata = match["metadata"]
            match_id = str(metadata.get("match_id") or "")
            if match_id in seen:  # 古い方の部分が新しい方と同じものを返してきた
                continue
            seen.add(match_id)
            if wanted and str((metadata.get("queue") or {}).get("id") or "").lower() != wanted:
                continue
            if match_id and metadata.get("is_completed", True):  # どれもまるごとの試合: 開いても呼び出しは要らない
                self.cache.put(("match", match_id), match, KEEP["match"])
            kept.append(match)
        return kept[:RECENT]

    def _matches(self, region: str, name: str, tag: str, mode: str, start: int) -> list:
        size = min(PAGE, RECENT - start)
        query = {"size": size} | ({"start": start} if start else {}) | ({"mode": mode} if mode in MODES else {})
        path = f"/valorant/v4/matches/{known_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}?" + urlencode(query)
        key = _matches_key(region, name, tag, mode, start)
        return self._get(key, path, KEEP["matches"], _slim_matches)

    def stored(self, region: str, name: str, tag: str, mode: str = "competitive") -> list:
        """HenrikDev が持つプレイヤーのそのモードの全試合、新しい順（v1 stored-matches: 読んだ試合だけで、
        履歴の一部が増えていく。全部ではない）。どれも ``_slim_stored`` の形。"""
        mode = mode if mode in STORED_MODES else "competitive"
        query = urlencode({"mode": mode})
        path = f"/valorant/v1/stored-matches/{known_region(region)}/{_part(name)}/{_part(tag)}?{query}"
        key = ("stored", known_region(region), name.lower(), tag.lower(), mode)
        return self._get(key, path, KEEP["stored"], _slim_stored)

    def older(self, region: str, name: str, tag: str, start: int) -> list:
        """プレイヤーの ``start`` からの PAGE 試合（新しい順、全モード）。
        どれも ``match`` が返す形: Riot に聞けないときのアーカイブ用（archive.py）。"""
        query = urlencode({"size": PAGE, "start": max(0, int(start))})
        path = f"/valorant/v4/matches/{known_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}?{query}"
        key = ("older", known_region(region), name.lower(), tag.lower(), max(0, int(start)))
        found = self._get(key, path, KEEP["matches"], _slim_matches)
        return [match for match in found if match["metadata"].get("is_completed", True)]

    def stored_all(self, region: str, name: str, tag: str) -> list:
        """HenrikDev が持つプレイヤーの全モードの全試合（モード無しの ``stored``）: Riot がもう出さない試合のための、
        アーカイブの薄い成績。"""
        path = f"/valorant/v1/stored-matches/{known_region(region)}/{_part(name)}/{_part(tag)}"
        key = ("stored", known_region(region), name.lower(), tag.lower(), "")
        return self._get(key, path, KEEP["stored"], _slim_stored)

    def has_player(self, name: str, tag: str, mode: str = "") -> bool:
        """プレイヤーのアカウント・ランク・試合が全部手元にあるか: あればページに呼び出しは要らない。"""
        account = self.cache.get(("account", name.lower(), tag.lower()))
        if not isinstance(account, dict):
            return False
        region = str(account.get("region") or "ap")
        name, tag = str(account.get("name") or name), str(account.get("tag") or tag)
        keys = [("mmr", region, name.lower(), tag.lower())]
        keys += [_matches_key(region, name, tag, mode, start) for start in (0, PAGE)]
        if shows_rr(mode):
            keys.append(("rr", region, name.lower(), tag.lower()))
        return all(self.cache.get(key) is not None for key in keys)

    def player_keys(self, region: str, name: str, tag: str, mode: str = "") -> list:
        """試合ごとに変わる、プレイヤーページが持つもの: ランク、RR、試合。"""
        keys = [("mmr", region, name.lower(), tag.lower()), ("rr", region, name.lower(), tag.lower())]
        return keys + [_matches_key(region, name, tag, mode, start) for start in (0, PAGE)]

    def read_at(self, region: str, name: str, tag: str, mode: str = "") -> float:
        """プレイヤーの最新の試合を読んだ時刻（0: 持っていない）。"""
        return float(self.read_times.get(_matches_key(region, name, tag, mode, 0)) or 0)

    def forget_player(self, region: str, name: str, tag: str, mode: str = "") -> bool:
        """プレイヤーのランク・RR・試合を HenrikDev に聞き直す（ページの「データを更新」）。
        読んでから FRESH_AFTER 経っていなければしない: 1 回押すと、訪問者全員で分け合うキーを最大 4 回使う。"""
        if time.time() - self.read_at(region, name, tag, mode) < FRESH_AFTER:
            return False
        for key in self.player_keys(region, name, tag, mode):
            kept, at = self.cache.get(key), self.read_times.get(key)
            if kept is not None and at:
                self.spares.put(key, (kept, at), SPARE_KEEP)
            self.cache.drop(key)
            self.read_times.drop(key)
        return True

    def match(self, region: str, match_id: str) -> dict:
        """``{"metadata", "players", "teams", "rounds", "kills", ...}``。"""
        if not MATCH_ID.match(match_id):
            raise HenrikError("notfound")
        kept = self.cache.get(("match", match_id))
        if kept is not None:
            return kept
        path = f"/valorant/v4/match/{known_region(region)}/{match_id}"
        return self._get(("match", match_id), path, KEEP["match"], slim_match)

    def leaderboard(self, region: str, start: int = 0, name: str = "", tag: str = "") -> dict:
        """リージョンのランク戦ランキングの 1 ページ:
        ``{"players": [{"rank", "name", "tag", "puuid", "card", "tier", "rr", "wins", "hidden"}], "thresholds":
        [{"tier", "rr", "from"}], "total",
        "updated"}``。``name``（と ``tag``）: そのプレイヤーだけ（どこにいても）。"""
        start = max(0, int(start))
        # v3 のドキュメントはページ番号（1 から）、古い答えは start_index だった: どちらも同じページ
        asked = {"size": BOARD_PAGE, "start_index": start, "page": start // BOARD_PAGE + 1}
        if name:
            asked |= {"name": name} | ({"tag": tag} if tag else {})
        path = f"/valorant/v3/leaderboard/{known_region(region)}/{PLATFORM}?{urlencode(asked)}"
        key = ("leaderboard", known_region(region), start) + ((name.casefold(), tag.casefold()) if name else ())
        return self._get(key, path, KEEP["leaderboard"], _slim_board)

    def matches_by_puuid(self, region: str, puuid: str, size: int = 5) -> list:
        """puuid でのプレイヤーの最新の試合。activity.py が残す部分だけ（開始、サーバー、キュー、長さ、
        各プレイヤーの puuid とランク）: 名前を持たない裏のコレクター用。"""
        query = urlencode({"size": size})
        path = f"/valorant/v4/by-puuid/matches/{known_region(region)}/{PLATFORM}/{_part(puuid)}?{query}"
        return self._get(("by-puuid", puuid), path, KEEP["matches"], _slim_collected)

    def has_board(self, region: str, start: int = 0, name: str = "", tag: str = "") -> bool:
        """ランキングのそのページ（または探したプレイヤー）が手元にあるか: あれば呼び出しは要らない。"""
        key = ("leaderboard", known_region(region), max(0, int(start))) + (
            (name.casefold(), tag.casefold()) if name else ()
        )
        return self.cache.get(key) is not None

    def status(self, region: str) -> dict:
        """リージョンの Riot のお知らせ: ``{"maintenances": [...], "incidents": [...]}``（slim_status）。"""
        path = f"/valorant/v1/status/{known_region(region)}"
        return self._get(("status", known_region(region)), path, KEEP["status"], slim_status)

    def featured(self) -> list:
        """ストアのおすすめバンドル（全員同じ）: ``[{"uuid", "price", "until", "items"}]``。"""
        return self._get(("featured",), "/valorant/v2/store-featured", KEEP["featured"], _slim_featured)

    def news(self, country: str) -> list:
        """その言語（``ja-jp``、``en-gb`` など）
        の Riot の VALORANT ニュース: ``[{"title", "at", "category", "url", "banner"}]``、新しい順。"""
        if self.cache.get(("no-news", country)):
            raise HenrikError("notfound")  # 少し前に聞いたら無かった: しばらく聞かない
        try:
            return self._get(("news", country), f"/valorant/v1/website/{_part(country)}", KEEP["news"], _slim_news)
        except HenrikError as error:
            if error.kind in ("down", "notfound"):
                self.cache.put(("no-news", country), True, KEEP["news"])
            raise

    def esports(self) -> tuple[list, float | None]:
        """eスポーツの予定（全リーグ）
        : ``[{"at", "state", "league": {"name", "region", "icon"}, "tournament", "best_of", "vod", "teams": [{"name",
        "code", "icon", "won",
        "wins"}]}]``（時刻順）と、``None`` か、新しいものが来ないときに最後に来た予定の時刻（``time.time()``）
        : 何も出さないより、それを出す（1 日まで）。失敗したらしばらく何も聞かない（1 回で分け合う呼び出し回数を最大
        2 回使う）。"""
        fresh = self.cache.get(("esports",))
        if fresh is not None:
            return fresh, None
        last = self.cache.get(("esports-last",))
        waiting = self.cache.get(("esports-wait",))
        if waiting:
            if last:
                return last["games"], last["at"]
            raise HenrikError(*waiting)
        try:
            games = self._esports()
        except HenrikError as error:
            if error.kind in ("nokey", "badkey"):
                raise
            self.cache.put(("esports-wait",), (error.kind, error.detail), ESPORTS_WAIT.get(error.kind, 120))
            if last:
                return last["games"], last["at"]
            raise
        self.cache.put(("esports-last",), {"games": games, "at": time.time()}, ESPORTS_LAST)
        return games, None

    def _esports(self) -> list:
        """新しい予定: 全部、来なければ主要リーグのもの（1 回の呼び出し）。全部は大きい: タイムアウト、エラー、
        または HenrikDev からの HTTP 429（HenrikDev はそのために Riot に出す各リクエストをキーに数える、
        docs.henrikdev.xyz/general/rate-limiting）。本番では毎回断られた。"""
        path = "/valorant/v1/esports/schedule"
        if not self.cache.get(("no-esports",)):
            try:
                return self._get(("esports",), path, KEEP["esports"], _slim_esports, last=False)
            except HenrikError as error:
                if not (error.kind == "down" or _refused(error)):
                    raise
                first = error
                self.cache.put(("no-esports",), True, KEEP["news"])
        else:
            first = HenrikError("down", "the whole schedule did not come a while ago")
        failed = self.cache.get(("no-esports", "leagues"))
        if failed:  # 少し前に失敗した: しばらく聞かない（訪問のたびに予算を使ってしまう）
            raise HenrikError("down", f"{first.detail or first.kind}; leagues: {failed}")
        try:
            games = self._get(
                ("esports", "leagues"), f"{path}?league={ESPORTS_LEAGUES}", KEEP["esports"], _slim_esports
            )
        except HenrikError as error:
            if error.kind in ("nokey", "badkey"):
                raise
            detail = f"{first.detail or first.kind}; leagues: {error.detail or error.kind}"
            if error.kind == "busy":  # 今は呼び出し回数を使い切っている: また今度
                raise HenrikError("busy", detail) from None
            self.cache.put(("no-esports", "leagues"), error.detail or error.kind, KEEP["news"])
            raise HenrikError("down", detail) from None
        if not games:
            raise HenrikError("down", f"{first.detail or first.kind}; leagues: no matches")
        return games

    def _get(self, cache_key, path: str, seconds: float, shape=None, last: bool = True):
        kept = self.cache.get(cache_key)
        if kept is not None:
            return kept
        try:
            return self._ask(cache_key, path, seconds, shape)
        except HenrikError as error:
            # HenrikDev が今は答えられない: エラーにせず、
            # ページは前に出したものを出し続ける（「データを更新」を押した場合）か、その日の最後の答えを出す（``last``。
            # 古さはページに出す）
            spare = None
            if error.kind in ("busy", "down"):
                spare = self.spares.get(cache_key) or (self.lasts.get(cache_key) if last else None)
            if spare is None:
                raise
            data, at = spare
            self.cache.put(cache_key, data, FRESH_AFTER)
            self.read_times.put(cache_key, at, FRESH_AFTER)
            return data

    def _ask(self, cache_key, path: str, seconds: float, shape):
        key = self.key()
        if not key:
            raise HenrikError("nokey")
        if not self.budget.take():
            raise HenrikError("busy")
        status, body = self.fetch(BASE + path, {"Authorization": key, "Accept": "application/json"})
        if status >= 500 and self.budget.take():
            # HenrikDev 自身の一時的な問題（502、503、504 は出たり消えたりする）: もう 1 回聞く。
            # 10 秒の無応答のあとは聞き直さない（2 回目の 10 秒で倍になるだけ）
            status, body = self.fetch(BASE + path, {"Authorization": key, "Accept": "application/json"})
        if status in (401, 403):
            raise HenrikError("badkey")
        if status == 404:
            raise HenrikError("notfound", "HTTP 404")
        if status == 429:
            raise HenrikError("busy", "HTTP 429")
        if status != 200:
            raise HenrikError("down", f"HTTP {status}" if status else "no answer within 10 s")
        try:
            data = json.loads(body.decode("utf-8")).get("data")
        except (ValueError, AttributeError, UnicodeDecodeError):
            raise HenrikError("down", f"an answer that is not JSON ({len(body):,} bytes)") from None
        if data is None:
            raise HenrikError("notfound")
        if shape is not None:
            data = shape(data)
        self.cache.put(cache_key, data, seconds)
        self.read_times.put(cache_key, time.time(), seconds)  # 読んだ時刻: プレイヤーページに出す
        # 横にも残す: あとで HenrikDev が答えないとき、エラーの代わりにこれを出す
        self.lasts.put(cache_key, (data, time.time()), LAST_KEEP)
        return data


#: eスポーツの予定全体が来ないときに（1 回の呼び出しで）頼むリーグ。 HenrikDev のドキュメントの名前で（カンマ区切り、
#: docs.henrikdev.xyz/valorant/api-reference/esports）。
ESPORTS_LEAGUES = "vct-international,vct-americas,vct-emea,vct-pacific,vct-china"
#: 新しい予定が来ないとき、最後に来た予定を出す秒数と、
#: 失敗したあと何も聞かない秒数: 呼び出し回数を使い切ったときは短く、HenrikDev が答えなかったときは長く。
ESPORTS_LAST = 24 * 3600
ESPORTS_WAIT = {"busy": 20, "down": 120}


def _refused(error: HenrikError) -> bool:
    """このサイト自身の予算ではなく、HenrikDev 自身がキーの使いすぎと言った（HTTP 429）。"""
    return error.kind == "busy" and error.detail == "HTTP 429"


def shows_rr(mode: str) -> bool:
    """このモードの一覧にコンペティティブの試合があるか（RR の変化を呼び出す価値がある）。"""
    return mode in ("", "competitive") or mode not in MODES


def _slim_mmr(data) -> dict:
    slim = _pick(data, ("current", "peak"))
    if isinstance(slim.get("peak"), dict):  # 今までの最高ランク: アセンダント以前のアクトのもの。その頃の番号で
        slim["peak"] = slim["peak"] | {"old": _old_ranks(slim["peak"])}
    seasons = [_slim_act(season) for season in (data or {}).get("seasonal") or [] if isinstance(season, dict)]
    slim["seasons"] = sorted(
        (act for act in seasons if act["games"]), key=lambda act: _act_order(act["short"]), reverse=True
    )
    return slim


#: アクトランクのバッジ（三角、「おにぎり」）が出す勝利の数: 良いもの。
BADGE_WINS = 9


def _slim_act(season: dict) -> dict:
    """v3 mmr の ``seasonal`` のアクト 1 つ: 戦績、最終ランク、良い勝利のランク（``act_wins``）。"""

    def number(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def tier(value) -> int:
        return number(value.get("id")) if isinstance(value, dict) else number(value)

    short = str((season.get("season") or {}).get("short") or "") if isinstance(season.get("season"), dict) else ""
    board = season.get("leaderboard_placement")
    wins = sorted((tier(win) for win in season.get("act_wins") or []), reverse=True)
    return {
        "short": short.lower(),
        "games": number(season.get("games")),
        "wins": number(season.get("wins")),
        "end_tier": tier(season.get("end_tier")),
        "old": _old_ranks(season),
        "board": number(board.get("rank")) if isinstance(board, dict) else 0,
        "best": [win for win in wins if win > 2][:BADGE_WINS],
    }


def _old_ranks(item: dict) -> bool:
    """v3 mmr のアクト（または最高ランクのアクト）のランク番号がエピソード 5 より前のものか:
    アセンダントが無かったので、同じ番号でも別のランクだった。"""
    season = item.get("season") if isinstance(item.get("season"), dict) else {}
    episode = (_act_order(str(season.get("short") or ""))[:1] or (99,))[0]
    return str(item.get("ranking_schema") or "") == "base" or episode < 5


def _act_order(short: str) -> tuple:
    """ "e9a3" -> (9, 3): 一覧がどんな順でも、最新のアクトが一番大きい。"""
    return tuple(int(number) for number in re.findall(r"\d+", str(short or "")))


def _slim_rr(data) -> dict:
    """v1 の一覧（または v2 の ``{"history": [...]}``）-> ``{試合 ID: {"change", "tier", "rr"}}``。"""
    items = data.get("history") if isinstance(data, dict) else data
    found = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("match_id"):
            continue
        change = item.get("mmr_change_to_last_game", item.get("last_change"))
        tier = item.get(
            "currenttier", (item.get("tier") or {}).get("id") if isinstance(item.get("tier"), dict) else None
        )
        try:
            found[str(item["match_id"])] = {
                "change": int(change),
                "tier": int(tier or 0),
                "rr": int(item.get("ranking_in_tier", item.get("rr")) or 0),
                "elo": int(item.get("elo") or 0),
                "at": _moment(item.get("date_raw") or item.get("date")),
            }
        except (TypeError, ValueError):
            continue
    return found


def _moment(value) -> int:
    """数（v1 の date_raw）か ISO 時刻（v2 の date）からの unix 秒。どちらでもなければ 0。"""
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except ValueError:
        return 0


def _slim_stored(matches) -> list:
    """v1 の保存済み試合: 試合ごとにアクト（"e9a3"）、開始、マップ、エージェント、ランク、
    プレイヤーのチームが勝ったか（None: 引き分け）、ラウンド、プレイヤーのキル・デス・アシスト・スコア・命中・ダメージ。"""
    if not isinstance(matches, list):
        raise HenrikError("down")
    found = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        meta, line, teams = (
            match.get(key) if isinstance(match.get(key), dict) else {} for key in ("meta", "stats", "teams")
        )
        side = str(line.get("team") or "").lower()
        red, blue = to_int(teams.get("red")), to_int(teams.get("blue"))
        mine, theirs = (red, blue) if side == "red" else (blue, red)
        season = meta.get("season") if isinstance(meta.get("season"), dict) else {}
        shots = line.get("shots") if isinstance(line.get("shots"), dict) else {}
        damage = line.get("damage") if isinstance(line.get("damage"), dict) else {}
        agent = line.get("character") if isinstance(line.get("character"), dict) else {}
        place = meta.get("map") if isinstance(meta.get("map"), dict) else {}
        mode = str(meta.get("mode") or "").strip().lower()
        found.append(
            {
                "id": str(meta.get("id") or ""),
                "season": str(season.get("short") or "").lower(),
                "started": str(meta.get("started_at") or ""),
                "at": _moment(meta.get("started_at")) if meta.get("started_at") else 0,
                "queue": STORED_QUEUES.get(mode, mode.replace(" ", "")),
                "cluster": str(meta.get("cluster") or ""),
                "map": str(place.get("name") or ""),
                "map_id": str(place.get("id") or "").lower(),
                "us": mine,
                "them": theirs,
                "agent": str(agent.get("id") or "").lower(),
                "tier": to_int(line.get("tier")),
                "won": (mine > theirs) if side in ("red", "blue") and mine != theirs else None,
                "rounds": red + blue,
                "kills": to_int(line.get("kills")),
                "deaths": to_int(line.get("deaths")),
                "assists": to_int(line.get("assists")),
                "score": to_int(line.get("score")),
                "head": to_int(shots.get("head")),
                "body": to_int(shots.get("body")),
                "leg": to_int(shots.get("leg")),
                "made": to_int(damage.get("made")),
                "received": to_int(damage.get("received")),
            }
        )
    return found


def _slim_board(data) -> dict:
    """v3 のランキング（または名前の違う v2 のもの）-> ページに出すもの。"""
    if not isinstance(data, dict):
        raise HenrikError("down")
    players = []
    for item in data.get("players") or []:
        if not isinstance(item, dict):
            continue
        tier = item.get("tier", item.get("competitiveTier"))
        players.append(
            {
                "rank": to_int(item.get("leaderboard_rank", item.get("leaderboardRank"))),
                "name": str(item.get("name", item.get("gameName")) or ""),
                "tag": str(item.get("tag", item.get("tagLine")) or ""),
                "puuid": str(item.get("puuid") or ""),
                "card": str(item.get("card", item.get("PlayerCardID")) or "").lower(),
                "tier": to_int(tier.get("id") if isinstance(tier, dict) else tier),
                "rr": to_int(item.get("rr", item.get("rankedRating"))),
                "wins": to_int(item.get("wins", item.get("numberOfWins"))),
                "hidden": bool(item.get("is_anonymized", item.get("IsAnonymized"))),
            }
        )
    thresholds = []
    for item in data.get("thresholds") or []:
        if isinstance(item, dict):
            tier = item.get("tier")
            thresholds.append(
                {
                    "tier": to_int(tier.get("id") if isinstance(tier, dict) else tier),
                    "rr": to_int(item.get("threshold")),
                    "from": to_int(item.get("start_index")) + 1,
                }
            )
    for tier, key in ((27, "radiant_threshold"), (26, "immortal_3_threshold"), (25, "immortal_2_threshold")):
        if not thresholds and data.get(key) is not None:  # v2
            thresholds.append({"tier": tier, "rr": to_int(data[key]), "from": 0})
    results = data.get("results") if isinstance(data.get("results"), dict) else {}
    return {
        "players": players,
        "thresholds": sorted(thresholds, key=lambda item: -item["tier"]),
        "total": to_int(results.get("total", data.get("total_players"))),
        "updated": str(data.get("updated_at") or data.get("last_update") or ""),
    }


def https_only(value) -> str:
    """https のアドレス、または ""（それ以外はリンクも表示もしない）。"""
    value = str(value or "")
    return value if value.startswith("https://") else ""


def _slim_news(data) -> list:
    articles = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        articles.append(
            {
                "title": str(item["title"])[:200],
                "at": str(item.get("date") or ""),
                "category": str(item.get("category") or "").lower(),
                "url": https_only(item.get("external_link")) or https_only(item.get("url")),
                "banner": https_only(item.get("banner_url")),
            }
        )
    return sorted(articles, key=lambda item: item["at"], reverse=True)


def _slim_esports(data) -> list:
    games = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        match = item.get("match") if isinstance(item.get("match"), dict) else {}
        teams = [team for team in match.get("teams") or [] if isinstance(team, dict) and team.get("name")]
        if len(teams) != 2:
            continue
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        tournament = item.get("tournament") if isinstance(item.get("tournament"), dict) else {}
        kind = match.get("game_type") if isinstance(match.get("game_type"), dict) else {}
        games.append(
            {
                "at": str(item.get("date") or ""),
                "state": str(item.get("state") or ""),
                "league": {
                    "name": str(league.get("name") or ""),
                    "id": str(league.get("identifier") or league.get("name") or "").lower(),
                    "region": str(league.get("region") or ""),
                    "icon": https_only(league.get("icon")),
                },
                "tournament": str(tournament.get("name") or ""),
                "best_of": int(kind.get("count") or 0) if str(kind.get("count") or "").isdigit() else 0,
                "vod": https_only(item.get("vod")),
                "teams": [
                    {
                        "name": str(team.get("name"))[:60],
                        "code": str(team.get("code") or "")[:8],
                        "icon": https_only(team.get("icon")),
                        "won": bool(team.get("has_won")),
                        "wins": int(team.get("game_wins") or 0) if str(team.get("game_wins") or 0).isdigit() else 0,
                    }
                    for team in teams
                ],
            }
        )
    return sorted(games, key=lambda item: item["at"])


def slim_status(data) -> dict:
    """お知らせ 1 つずつ:
    ``{"kind": maintenance / incident, "severity", "status", "titles": {locale: 文}, "updates": [{"at", "words": {locale: 文}}]（新しい順）
    , "at"}``。"""

    def words(items) -> dict:
        return {
            str(item.get("locale") or "").replace("-", "_").lower(): str(item.get("content") or "")
            for item in items or []
            if isinstance(item, dict) and item.get("content")
        }

    found = {}
    for kind in ("maintenances", "incidents"):
        notices = []
        for item in (data or {}).get(kind) or [] if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            updates = [
                {"at": str(update.get("created_at") or ""), "words": words(update.get("translations"))}
                for update in item.get("updates") or []
                if isinstance(update, dict)
            ]
            notices.append(
                {
                    "kind": kind[:-1] if kind == "incidents" else "maintenance",
                    "severity": str(item.get("incident_severity") or ""),
                    "status": str(item.get("maintenance_status") or ""),
                    "titles": words(item.get("titles")),
                    "updates": sorted(updates, key=lambda update: update["at"], reverse=True)[:3],
                    "at": str(item.get("created_at") or ""),
                    "platforms": [str(name).lower() for name in item.get("platforms") or []],
                }
            )
        found[kind] = notices
    return found


def _slim_featured(data) -> list:
    """v2 の store-featured: 各バンドルの uuid、値段、残り時間、アイテム（uuid、種類、名前、値段）。"""
    bundles = data if isinstance(data, list) else []
    found = []
    for bundle in bundles:
        if not isinstance(bundle, dict) or not bundle.get("bundle_uuid"):
            continue
        items = [
            {
                "uuid": str(item.get("uuid") or "").lower(),
                "type": str(item.get("type") or ""),
                "name": str(item.get("name") or ""),
                "image": str(item.get("image") or ""),
                "amount": to_int(item.get("amount"), 1),
                "price": to_int(item.get("discounted_price", item.get("base_price"))),
                "base": to_int(item.get("base_price")),
            }
            for item in bundle.get("items") or []
            if isinstance(item, dict)
        ]
        found.append(
            {
                "uuid": str(bundle["bundle_uuid"]).lower(),
                "price": to_int(bundle.get("bundle_price")),
                "until": time.time() + to_int(bundle.get("seconds_remaining")),  # しばらく持つ: 終わるときまで
                "items": items,
            }
        )
    return found


def _matches_key(region: str, name: str, tag: str, mode: str, start: int) -> tuple:
    return ("matches", region, name.lower(), tag.lower(), mode if mode in MODES else "", start)


def _pick(item, keys) -> dict:
    return {key: item.get(key) for key in keys} if isinstance(item, dict) else {}


def slim_match(match) -> dict:
    """ページが使う部分だけの試合（METADATA、PLAYER、TEAM）。"""
    if not isinstance(match, dict) or not isinstance(match.get("metadata"), dict):
        raise HenrikError("down")
    return {
        "metadata": _pick(match["metadata"], METADATA),
        # 試合自身の一覧の順で: ダイジェストはその中の位置でプレイヤーを指す
        "players": [_pick(player, PLAYER) for player in match.get("players") or [] if isinstance(player, dict)],
        "teams": [_pick(team, TEAM) for team in match.get("teams") or [] if isinstance(team, dict)],
        "digest": rounds.digest(match),
    }


def _slim_collected(matches) -> list:
    if not isinstance(matches, list):
        raise HenrikError("down")
    return [
        {
            "metadata": _pick(match["metadata"], METADATA),
            "players": [
                _pick(player, ("puuid", "tier", "team_id", "agent"))
                for player in match.get("players") or []
                if isinstance(player, dict)
            ],
            # どのチームが勝ったか: エージェントの勝率（activity.py）
            "teams": [_pick(team, ("team_id", "won")) for team in match.get("teams") or [] if isinstance(team, dict)],
        }
        for match in matches
        if isinstance(match, dict) and isinstance(match.get("metadata"), dict)
    ]


def _slim_matches(matches) -> list:
    if not isinstance(matches, list):
        raise HenrikError("down")
    return [slim_match(match) for match in matches if isinstance(match, dict) and match.get("metadata")]


def _part(text: str) -> str:
    return quote(text, safe="")


def known_region(region: str) -> str:
    region = str(region or "").lower()
    return region if region in REGIONS else "ap"
