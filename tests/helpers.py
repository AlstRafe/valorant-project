"""テストで共通のもの: tests/fixtures/ からの外部 API の答えと、それを使うアプリ。"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from valtracker import create_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeFetch:
    """HenrikDev と valorant-api.com のような答えをフィクスチャから返す。``fail`` なら全部そのステータスで失敗させる。
    頼まれた URL は全部 ``asked`` に入る。"""

    def __init__(self):
        self.asked: list[tuple[str, dict]] = []
        self.fail = None
        self.henrik_status = None
        #: 設定されていれば、Riot 自身のニュース一覧とステータスファイル（riotweb.py）は代わりにこのステータスで答える。
        self.riot_web_status = None
        #: Riot の eスポーツ予定ページは、これが設定されているときだけ（フィクスチャから）答える。でなければ HTTP 404。
        self.esports_page = False

    def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.asked.append((url, headers))
        if self.fail is not None:
            return self.fail, b""
        if url.startswith(("https://content.publishing.riotgames.com/", "https://valorant.secure.dyn.riotcdn.net/")):
            if self.riot_web_status:
                return self.riot_web_status, b"<html>busy</html>"
            if "/channels/public/x/status/" in url:
                return 200, json.dumps(json.loads(fixture("henrik_status.json"))["data"]).encode()
            news = json.loads(fixture("riot_news.json"))
            if "tags=patch_notes" in url:
                news["data"] = [item for item in news["data"] if isinstance(item, dict) and item.get("tags")]
            return 200, json.dumps(news).encode()
        if url.startswith("https://valorantesports.com/"):
            return (200, fixture("riot_esports.html")) if self.esports_page else (404, b"")
        if url.startswith("https://api.henrikdev.xyz/"):
            if self.henrik_status:
                return self.henrik_status, json.dumps({"status": self.henrik_status, "errors": []}).encode()
            if "/v2/account/" in url:
                return 200, fixture("henrik_account.json")
            if "/v3/mmr/" in url:
                return 200, fixture("henrik_mmr.json")
            if "/v1/mmr-history/" in url:
                return 200, fixture("henrik_mmr_history.json")
            if "/v3/leaderboard/" in url:
                return 200, fixture("henrik_leaderboard.json")
            if "/v1/status/" in url:
                return 200, fixture("henrik_status.json")
            if "/v2/store-featured" in url:
                return 200, fixture("henrik_featured.json")
            if "/v1/website/" in url:
                return 200, fixture("henrik_news.json")
            if "/v1/esports/schedule" in url:
                return 200, fixture("henrik_esports.json")
            if "/v1/stored-matches/" in url:
                return 200, fixture("henrik_stored.json")
            if "/v4/matches/" in url or "/v4/by-puuid/matches/" in url:
                return 200, fixture("henrik_matches.json")
            if "/v4/match/" in url:
                matches = json.loads(fixture("henrik_matches.json"))["data"]
                wanted = url.rsplit("/", 1)[-1]
                for match in matches:
                    if match["metadata"]["match_id"] == wanted:
                        return 200, json.dumps({"status": 200, "data": match}).encode()
                return 404, b"{}"
        if url.startswith("https://valorant-api.com/v1/agents"):
            return 200, fixture("content_agents.json")
        if url.startswith("https://valorant-api.com/v1/maps"):
            return 200, fixture("content_maps.json")
        if url.startswith("https://valorant-api.com/v1/competitivetiers"):
            return 200, fixture("content_tiers.json")
        kind = url.removeprefix("https://valorant-api.com/v1/").split("?")[0]
        lists = json.loads(fixture("content_collection.json"))
        if kind in lists:
            return 200, json.dumps({"status": 200, "data": lists[kind]}).encode()
        if url.startswith(
            (
                "https://media.valorant-api.com/",
                "https://static.lolesports.com/",
                "https://cmsassets",
                "https://images.contentstack.io/",
            )
        ):
            return 200, PNG
        return 404, b""

    def henrik_calls(self) -> list[str]:
        return [url for url, _ in self.asked if url.startswith("https://api.henrikdev.xyz/")]


PUUID = "0c0c0c0c-1111-4222-8333-444444444444"


def _token(claims: dict) -> str:
    """JWT の形（Riot のトークンは読むだけで確かめない）。"""
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{part}.c2lnbmF0dXJl"


ACCESS = _token({"sub": PUUID, "scp": ["account", "openid"]})
#: 持ち主の 2 つ目のアカウント（Smurf#JP2、cookie "sub-1"）: PD は 1 つ目と同じように答える。
PUUID2 = "0d0d0d0d-1111-4222-8333-555555555555"
ACCESS2 = _token({"sub": PUUID2, "scp": ["account", "openid"]})
ID_TOKEN = _token({"sub": PUUID, "nonce": "1"})
ENTITLEMENTS = _token({"entitlements": [], "sub": PUUID})


class FakeRiot:
    """Riot のサインインと PD のような答え。``ssid``: 受け取る cookie（Riot と同じく使うたびに新しくなる）、
    ``refuse``: PD がトークンを悪いと言う回数、``broken``: PD が失敗する種類。"""

    def __init__(self):
        self.asked: list[tuple[str, str, dict, bytes | None]] = []
        self.ssid = "ssid-1"
        self.renewals = 0
        #: 2 つ目のアカウントの cookie（1 つ目と同じく使うたびに更新）。
        self.second = "sub-1"
        self.second_renewals = 0
        self.refuse = 0
        self.broken: set[str] = set()
        self.pd = json.loads(fixture("riot_pd.json"))
        #: 別のプレイヤーの試合履歴、試合 2 つと RR（riot_match.json）。
        self.pd |= json.loads(fixture("riot_match.json"))
        #: VALORANT が開いているか、持ち主がどこにいるか: ""（どこにもいない＝閉じている）、"menus"、
        #: "pregame"（エージェント選択）、"ingame"。"" 以外ならゲームは開いている。
        self.phase = ""
        #: GLZ のセッションが読めない（アドレスが 500）: パーティーでゲームが開いているかわかる。
        self.no_session = False
        #: パーティーの試合が見つかった時点でエージェント選択になる（State MATCHMADE_GAME_STARTING、
        #: ゲームの 5 秒カウント）。セッションはまだ MENUS。
        self.early_pregame = False
        #: GLZ のセッションの loopState（None: ``phase`` のもの）。ロード画面でセッションが遅れるときのため。
        self.loop: str | None = None
        #: これがある間、PD のランク（mmr）は答える前にそれが立つまで待つ（最大 5 秒）: 遅いランク。
        self.slow_ranks = None
        #: GLZ が受けた持ち主のボタン操作: （何を, 値）。
        self.pressed: list[tuple[str, str]] = []
        #: 送り返したロードアウト（PUT playerloadout）。
        self.worn: list[dict] = []
        #: PD が出す playerloadout: 3（2026 年 9 月から。v2 は 404）か 2（それ以前。v3 は 404）。
        self.loadout_version = 3
        #: PD は PUT playerloadout に 200 で答えるが、元のロードアウトのままにする。
        self.ignore_put = False

    def address(self, access: str = ACCESS) -> str:
        return f"https://playvalorant.com/opt_in#access_token={access}&scope=account+openid&iss=x&id_token={ID_TOKEN}&token_type=Bearer&expires_in=3600"

    def __call__(self, method: str, url: str, headers: dict, body):
        self.asked.append((method, url, headers, body))
        if url.endswith(f"/store/v3/storefront/{PUUID}") and method != "POST":
            return 404, [], b"{}"  # v2 の GET はもう無い
        if url.startswith("https://auth.riotgames.com/authorize"):
            if f"ssid={self.second}" in headers.get("Cookie", "").split("; "):
                self.second_renewals += 1
                self.second = f"sub-{self.second_renewals + 1}"
                cookie = ("Set-Cookie", f"ssid={self.second}; Path=/; Max-Age=2592000; Secure; HttpOnly")
                return 303, [("Location", self.address(ACCESS2)), cookie], b""
            if f"ssid={self.ssid}" not in headers.get("Cookie", "").split("; "):
                return 303, [("Location", "/login")], b""
            self.renewals += 1
            self.ssid = f"ssid-{self.renewals + 1}"
            cookies = [
                ("Set-Cookie", f"ssid={self.ssid}; Path=/; Max-Age=2592000; Secure; HttpOnly"),
                ("Set-Cookie", "asid=; Max-Age=0"),
            ]
            return 303, [("Location", self.address()), *cookies], b""
        if url == "https://entitlements.auth.riotgames.com/api/token/v1":
            if method != "POST" or headers.get("Authorization") not in (f"Bearer {ACCESS}", f"Bearer {ACCESS2}"):
                return 400, [], b"{}"
            return 200, [], json.dumps({"entitlements_token": ENTITLEMENTS}).encode()
        if url == "https://auth.riotgames.com/userinfo":
            if headers.get("Authorization") == f"Bearer {ACCESS2}":
                return 200, [], json.dumps({"sub": PUUID2, "acct": {"game_name": "Smurf", "tag_line": "JP2"}}).encode()
            return 200, [], json.dumps({"sub": PUUID, "acct": {"game_name": "Owner", "tag_line": "JP1"}}).encode()
        if url.startswith("https://riot-geo.pas.si.riotgames.com/"):
            if json.loads(body).get("id_token") != ID_TOKEN:
                return 400, [], b"{}"
            return 200, [], json.dumps({"token": "x", "affinities": {"pbe": "na", "live": "ap"}}).encode()
        if url.startswith(("https://pd.ap.a.pvp.net/", "https://glz-ap-1.ap.a.pvp.net/")):
            if self.refuse:
                self.refuse -= 1
                return 400, [], json.dumps({"httpStatus": 400, "errorCode": "BAD_CLAIMS"}).encode()
            if headers.get("X-Riot-Entitlements-JWT") != ENTITLEMENTS:
                return 400, [], json.dumps({"errorCode": "BAD_CLAIMS"}).encode()
            return self._pd(url.replace(PUUID2, PUUID), method, body)
        return 404, [], b""

    def _pd(self, url: str, method: str = "GET", body=None):
        from valtracker.riot import ITEM_TYPES

        glz = "https://glz-ap-1.ap.a.pvp.net"
        if url.endswith(f"/contracts/v1/contracts/{PUUID}"):
            return self._answer("contracts", self.pd["contracts"])
        missing = 404, [], json.dumps({"errorCode": "RESOURCE_NOT_FOUND"}).encode()
        if url == f"{glz}/session/v1/sessions/{PUUID}":
            if self.no_session:
                return 500, [], b"{}"
            if not self.phase:
                return missing
            loop = self.loop or {"menus": "MENUS", "pregame": "PREGAME", "ingame": "INGAME"}[self.phase]
            return self._answer("session", {"subject": PUUID, "cxnState": "CONNECTED", "loopState": loop})
        party = self.pd["party"]
        if url == f"{glz}/parties/v1/players/{PUUID}":
            return self._answer("party", {"Subject": PUUID, "CurrentPartyID": party["ID"]}) if self.phase else missing
        if url == f"{glz}/parties/v1/parties/{party['ID']}" and method == "GET":
            return self._answer("party", party)
        if url.startswith(f"{glz}/parties/v1/parties/{party['ID']}/") and method == "POST":
            what = url.rsplit("/", 1)[-1]
            if what == "queue":
                queue = json.loads(body)["queueId"]
                self.pressed.append(("queue", queue))
                party["MatchmakingData"]["QueueID"] = queue
            else:
                self.pressed.append((what, ""))
                party["State"] = "MATCHMAKING" if what == "join" else "DEFAULT"
                party["QueueEntryTime"] = "2026-11-06T00:00:00Z" if what == "join" else "0001-01-01T00:00:00Z"
            return self._answer("party", party)
        pregame_id = self.pd["pregame"]["ID"]
        if url.startswith(f"{glz}/pregame/v1/matches/{pregame_id}/") and method == "POST":
            what, agent = url.rsplit("/", 2)[-2:]
            self.pressed.append((what, agent))
            mine = next(item for item in self.pd["pregame"]["AllyTeam"]["Players"] if item["Subject"] == PUUID)
            mine.update({"CharacterID": agent, "CharacterSelectionState": "locked" if what == "lock" else "selected"})
            return self._answer("pregame", self.pd["pregame"])
        if "/mmr/v1/leaderboards/affinity/" in url:
            board = json.loads(json.dumps(self.pd["leaderboard"]))
            query = parse_qs(urlsplit(url).query).get("query", [""])[0]
            if query:  # Riot はそう始まる名前を探す
                board["Players"] = [p for p in board["Players"] if p["gameName"].lower().startswith(query.lower())]
            return self._answer("leaderboard", board)
        if f"/match-history/v1/history/{self.pd['player']}?" in url:
            asked = parse_qs(urlsplit(url).query)
            start, end = int(asked["startIndex"][0]), int(asked["endIndex"][0])
            history = dict(self.pd["history"], BeginIndex=start, EndIndex=end)
            return self._answer("history", history | {"History": self.pd["history"]["History"][start:end]})
        if "/match-details/v1/matches/" in url:
            found = self.pd["matches"].get(url.rsplit("/", 1)[-1])
            return self._answer("match", found) if found else missing
        if f"/mmr/v1/players/{self.pd['player']}/competitiveupdates?" in url:
            return self._answer("rr", self.pd["updates"])
        if "/mmr/v1/players/" in url:
            if self.slow_ranks is not None:
                self.slow_ranks.wait(5)
            found = self.pd["mmr"].get(url.rsplit("/", 1)[-1])
            return self._answer("rank", found) if found else missing
        for phase, path, match in (
            ("pregame", "/pregame/v1", self.pd["pregame"]["ID"]),
            ("ingame", "/core-game/v1", self.pd["coregame"]["MatchID"]),
        ):
            if url == f"{glz}{path}/players/{PUUID}":
                found = self.phase == "menus" and party["State"] == "MATCHMADE_GAME_STARTING" and self.early_pregame
                if self.phase != phase and not (phase == "pregame" and found):
                    return missing
                return self._answer(phase, {"Subject": PUUID, "MatchID": match})
            if url == f"{glz}{path}/matches/{match}":
                return self._answer(phase, self.pd["pregame" if phase == "pregame" else "coregame"])
        if url.endswith("/name-service/v2/players") and method == "PUT":
            asked = set(json.loads(body))
            return self._answer("names", [item for item in self.pd["names"] if item["Subject"] in asked])
        for version in (2, 3):
            if (
                url.endswith(f"/personalization/v{version}/players/{PUUID}/playerloadout")
                and version != self.loadout_version
            ):
                return 404, [], json.dumps({"httpStatus": 404, "errorCode": "RESOURCE_NOT_FOUND"}).encode()
        loadout = f"/personalization/v{self.loadout_version}/players/{PUUID}/playerloadout"
        if url.endswith(loadout) and method == "PUT":
            self.worn.append(json.loads(body))
            if self.ignore_put:
                return self._answer("equip", self.pd["loadout"])
            self.pd["loadout"] = json.loads(body) | {"Version": 8}  # ここからゲームが着けるもの
            return self._answer("equip", self.pd["loadout"])
        if url.endswith(f"/store/v3/storefront/{PUUID}"):
            return self._answer("storefront", self.pd["storefront"])
        if url == "https://glz-ap-1.ap.a.pvp.net/parties/v1/parties/customgameconfigs":
            return self._answer("queues", self.pd["customgameconfigs"])
        if url.endswith(f"/store/v1/wallet/{PUUID}"):
            return self._answer("wallet", self.pd["wallet"])
        if url.endswith(loadout):
            return self._answer("loadout", self.pd["loadout"])
        for kind, item_type in ITEM_TYPES.items():
            if url.endswith(f"/store/v1/entitlements/{PUUID}/{item_type}"):
                items = [{"TypeID": item_type, "ItemID": item} for item in self.pd["entitlements"][kind]]
                if kind == "buddies":  # コピーごとに別のインスタンス（武器の CharmInstanceID）
                    for number, item in enumerate(items, 1):
                        item["InstanceID"] = f"i{number}"
                return self._answer(kind, {"ItemTypeID": item_type, "Entitlements": items})
        return 404, [], b"{}"

    def _answer(self, kind: str, value):
        if kind in self.broken:
            return 500, [], json.dumps({"errorCode": "SERVER_ERROR"}).encode()
        return 200, [], json.dumps(value).encode()

    def pd_calls(self) -> int:
        return sum(url.startswith(("https://pd.", "https://glz-")) for _, url, _, _ in self.asked)


def make_app(key: str = "HDEV-test-key", mount: str = "", local=None):
    """一時フォルダーに新しい instance/ を作った（app, fetch, インスタンスフォルダー）。Riot の偽物は app.riot。
    ``local``: PC の Riot Client（デスクトップアプリの localclient.LocalClient）、サイトなら None。"""
    folder = tempfile.TemporaryDirectory()
    fetch = FakeFetch()
    riot = FakeRiot()
    app = create_app(mount=mount, instance=Path(folder.name), fetch=fetch, send=riot, local=local)
    app.riot = riot
    app.config["TESTING"] = True
    if key:
        app.config["VAL_SETTINGS"].save(henrik_key=key)
    remove = folder.cleanup

    def cleanup():
        app.config["VAL_ACTIVITY"].close()
        app.config["VAL_RATINGS"].close()
        app.config["VAL_ARCHIVE"].close()
        remove()

    folder.cleanup = cleanup
    app._test_folder = folder  # アプリと一緒に消す
    return app, fetch, Path(folder.name)
