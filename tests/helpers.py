"""What the tests share: the outside APIs' answers from tests/fixtures/, and an app that uses them."""

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
    """Answers like HenrikDev and valorant-api.com would, from the fixtures; ``fail`` makes everything fail
    with that status; every asked URL is in ``asked``."""

    def __init__(self):
        self.asked: list[tuple[str, dict]] = []
        self.fail = None
        self.henrik_status = None
        #: Riot's own news list and status files (riotweb.py) answer this status instead, when set.
        self.riot_web_status = None
        #: Riot's esports schedule page answers (from the fixture) only when this is set; else HTTP 404.
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
    """A JWT's shape (Riot's tokens are read, never checked, by us)."""
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{part}.c2lnbmF0dXJl"


ACCESS = _token({"sub": PUUID, "scp": ["account", "openid"]})
ID_TOKEN = _token({"sub": PUUID, "nonce": "1"})
ENTITLEMENTS = _token({"entitlements": [], "sub": PUUID})


class FakeRiot:
    """Answers like Riot's sign-in and PD would. ``ssid``: the cookie it takes (a new one each time it is used,
    as Riot does); ``refuse``: PD says the token is bad that many times; ``broken``: kinds PD fails for."""

    def __init__(self):
        self.asked: list[tuple[str, str, dict, bytes | None]] = []
        self.ssid = "ssid-1"
        self.renewals = 0
        self.refuse = 0
        self.broken: set[str] = set()
        self.pd = json.loads(fixture("riot_pd.json"))
        #: Another player's match history, two of their matches and their RR (riot_match.json).
        self.pd |= json.loads(fixture("riot_match.json"))
        #: Whether VALORANT is open, and where the owner is: "" (nowhere: closed), "menus", "pregame" (agent
        #: select) or "ingame". A phase other than "" means the game is open.
        self.phase = ""
        #: GLZ's session cannot be read (its address answers 500): the party tells whether the game is open.
        self.no_session = False
        #: The owner's buttons as GLZ got them: (what, value).
        self.pressed: list[tuple[str, str]] = []
        #: The loadouts sent back (PUT playerloadout).
        self.worn: list[dict] = []
        #: The playerloadout PD gives: 3 (since September 2026; v2 answers 404) or 2 (before: v3 answers 404).
        self.loadout_version = 3
        #: PD answers a PUT playerloadout with 200 but keeps the loadout it had.
        self.ignore_put = False

    def address(self, access: str = ACCESS) -> str:
        return f"https://playvalorant.com/opt_in#access_token={access}&scope=account+openid&iss=x&id_token={ID_TOKEN}&token_type=Bearer&expires_in=3600"

    def __call__(self, method: str, url: str, headers: dict, body):
        self.asked.append((method, url, headers, body))
        if url.endswith(f"/store/v3/storefront/{PUUID}") and method != "POST":
            return 404, [], b"{}"  # v2's GET is gone
        if url.startswith("https://auth.riotgames.com/authorize"):
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
            if method != "POST" or headers.get("Authorization") != f"Bearer {ACCESS}":
                return 400, [], b"{}"
            return 200, [], json.dumps({"entitlements_token": ENTITLEMENTS}).encode()
        if url == "https://auth.riotgames.com/userinfo":
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
            return self._pd(url, method, body)
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
            loop = {"menus": "MENUS", "pregame": "PREGAME", "ingame": "INGAME"}[self.phase]
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
            if query:  # Riot looks for names that start so
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
            found = self.pd["mmr"].get(url.rsplit("/", 1)[-1])
            return self._answer("rank", found) if found else missing
        for phase, path, match in (
            ("pregame", "/pregame/v1", self.pd["pregame"]["ID"]),
            ("ingame", "/core-game/v1", self.pd["coregame"]["MatchID"]),
        ):
            if url == f"{glz}{path}/players/{PUUID}":
                if self.phase != phase:
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
            self.pd["loadout"] = json.loads(body) | {"Version": 8}  # what the game wears from now on
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
                return self._answer(kind, {"ItemTypeID": item_type, "Entitlements": items})
        return 404, [], b"{}"

    def _answer(self, kind: str, value):
        if kind in self.broken:
            return 500, [], json.dumps({"errorCode": "SERVER_ERROR"}).encode()
        return 200, [], json.dumps(value).encode()

    def pd_calls(self) -> int:
        return sum(url.startswith(("https://pd.", "https://glz-")) for _, url, _, _ in self.asked)


def make_app(key: str = "HDEV-test-key", mount: str = ""):
    """(app, fetch, instance folder) with a fresh instance/ in a temporary folder; the Riot fake is app.riot."""
    folder = tempfile.TemporaryDirectory()
    fetch = FakeFetch()
    riot = FakeRiot()
    app = create_app(mount=mount, instance=Path(folder.name), fetch=fetch, send=riot)
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
    app._test_folder = folder  # removed with the app
    return app, fetch, Path(folder.name)
