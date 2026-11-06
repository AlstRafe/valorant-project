"""The owner's own Riot account: what VALORANT's store servers (PD) say it owns, which no public API tells.

Signing in: Riot's sign-in page has a captcha, so this server never sees the password. The owner signs in once in
a browser and pastes on the settings page either

- the ``ssid`` cookie of auth.riotgames.com (or a whole Cookie header): it is traded for an hour's tokens whenever
  they are needed, and the fresh cookie Riot hands back each time is saved in its place, or
- the address the browser lands on after signing in (``https://playvalorant.com/opt_in#access_token=...``): an
  hour's tokens, with nothing long-lived kept.

Kept in instance/riot.json (readable by its owner only, never in Git): the cookies, the account (puuid, Riot ID,
shard) and the tokens until they run out. These are the game client's own addresses, not a published API: Riot
can change them without notice, so every field is read as optional, and what failed is kept for the owner to see.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from valtracker import net
from valtracker.cache import TTLCache
from valtracker.settings import Settings

AUTHORIZE = (
    "https://auth.riotgames.com/authorize?redirect_uri=https%3A%2F%2Fplayvalorant.com%2Fopt_in"
    "&client_id=play-valorant-web-prod&response_type=token%20id_token&nonce=1&scope=account%20openid"
)
ENTITLEMENTS = "https://entitlements.auth.riotgames.com/api/token/v1"
USERINFO = "https://auth.riotgames.com/userinfo"
GEO = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"
PD = "https://pd.{shard}.a.pvp.net"
#: The party server: the game's queue settings (map pools, who may queue together) come from here.
GLZ = "https://glz-{shard}-1.{shard}.a.pvp.net"
#: How long the queue settings are kept (seconds): they change with a patch or a map rotation.
QUEUES_KEEP = 1800
#: The match being played (agent select, or the match itself) is asked again after this long.
LIVE_KEEP = 4
#: The phases of a match the owner can be in, and where GLZ tells of each.
PHASES = (("pregame", "/pregame/v1"), ("ingame", "/core-game/v1"))
#: What the owner can press on /live (Riot.act): start a queue (its id), leave it, pick or lock an agent (its uuid).
ACTIONS = ("start", "leave", "select", "lock")
#: A player's rank (PD's mmr) is asked once a match at most: it changes only when a match ends.
RANK_KEEP = 900
_QUEUE_ID = re.compile(r"^[a-z0-9_-]{2,40}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: The account's region (Riot's geo answer) -> the PD server that keeps its items.
SHARDS = {"na": "na", "latam": "na", "br": "na", "eu": "eu", "ap": "ap", "kr": "kr", "pbe": "pbe"}
#: The PC game client, as PD wants to be told.
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
#: The collection's kinds -> PD's item types.
ITEM_TYPES = {
    "skins": "e7c63390-eda7-46e0-bb7a-a6abdacd2433",  # skin levels
    "chromas": "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",  # buddy levels, one entitlement per copy
    "cards": "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "sprays": "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "titles": "de7caa6b-adf7-4588-bbd1-143831e786c6",
    "flex": "03a572de-4234-31ed-d344-ababa488f981",
    "agents": "01bb38e1-da47-4e6a-9b3d-945fe4655707",
}
#: How long the collection is kept (seconds): a skin bought in the game shows here after this.
KEEP = 90
#: A token is traded for a new one this long before it runs out.
EARLY = 300
#: At most this much of what is pasted (a whole Cookie header is a few kB).
MAX_PASTE = 8000
_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_COOKIE_VALUE = re.compile(r"^[\x21-\x7e]{1,4096}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._\-]{20,8192}$")


class RiotError(Exception):
    """Why there is no answer: ``kind`` is one of nologin (no account linked), badpaste (what was pasted is
    neither a cookie nor a signed-in address), signedout (Riot no longer takes the cookie, or the hour is over),
    busy, down (no answer, or one that cannot be read). ``detail``: what Riot said, for the owner."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


class Riot:
    def __init__(self, folder: Path, send: net.Send = net.send, version=dict, clock=time.time):
        """``version``: () -> valorant-api.com's /version (content.Content.client_version), which PD wants."""
        self.store = Settings(folder, "riot.json")
        self.send, self.version, self.clock = send, version, clock
        self.cache = TTLCache(8)
        #: Players' ranks in the owner's match: {puuid: PD's mmr answer}.
        self.ranks = TTLCache(64)
        self._lock = threading.Lock()

    # -- what the pages use ---------------------------------------------------------------------------

    def status(self) -> dict:
        """For the settings page: the linked account, whether it lasts (a cookie) or ends at ``until`` (an
        hour's address), and whether Riot refused it last time (``signed_out``)."""
        saved = self.store.load()
        token = saved.get("token") if isinstance(saved.get("token"), dict) else {}
        lasting = bool(saved.get("cookies"))
        return {
            "linked": bool(saved.get("puuid")),
            "name": str(saved.get("name") or ""),
            "tag": str(saved.get("tag") or ""),
            "shard": str(saved.get("shard") or ""),
            "lasting": lasting,
            "until": 0.0 if lasting else _number(token.get("until")) + EARLY,
            "signed_out": bool(saved.get("signed_out")) or (not lasting and _number(token.get("until")) < self.clock()),
            "problem": str(saved.get("problem") or ""),
        }

    def sign_in(self, pasted: str) -> dict:
        """Link the account from what the owner pasted (see the module's words); RiotError when it does not work."""
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
            self.store.save(cookies=cookies or None, token=tokens, signed_out=None, problem=None, **account)
            self.cache = TTLCache(8)  # another account's collection is not shown
        return self.status()

    def sign_out(self) -> None:
        with self._lock:
            self.store.save(
                cookies=None, token=None, puuid=None, name=None, tag=None, shard=None, signed_out=None, problem=None
            )
            self.cache = TTLCache(8)

    def collection(self) -> dict:
        """``{"name", "tag", "wallet": {currency uuid: amount} or None, "loadout": PD's playerloadout or None,
        "owned": {kind: {item uuid: copies}}, "missing": [what could not be read], "problem": why}``.
        RiotError when nothing could be read."""
        return self._kept("collection", self._collect)

    def shop(self) -> dict:
        """Today's store (shop.py reads it): ``{"name", "tag", "wallet", "storefront": PD's storefront or None,
        "missing", "problem"}``. RiotError when nothing could be read."""
        return self._kept("shop", self._storefront)

    def queues(self) -> dict:
        """The game's settings of its queues (GLZ customgameconfigs: the same for everyone of the region, so the
        public pages may show them): ``{"config": Riot's answer, "at", "missing", "problem"}``."""
        return self._kept("queues", self._queues, QUEUES_KEEP)

    def contracts(self) -> dict:
        """The battle pass, the events' passes, the agents' contracts and the missions (PD's contracts):
        ``{"contracts": PD's answer, "missing", "problem"}``. RiotError when it could not be read."""
        return self._kept("contracts", self._contracts)

    def live(self) -> dict:
        """Where the owner's game is now (GLZ): ``{"running": whether VALORANT is open (None: could not tell),
        "phase": "menus", "pregame" (agent select), "ingame" or "" (not playing), "match": GLZ's match or None,
        "party": GLZ's party (in the menus) or None, "names": {puuid: [name, tag]}, "ranks": {puuid: PD's mmr},
        "at", "missing", "problem"}``. Names are asked only for players who do not hide theirs (``Incognito``)."""
        return self._kept("live", self._live, LIVE_KEEP)

    def act(self, action: str, value: str = "") -> dict:
        """One of the owner's buttons on /live, sent as the game's own client sends it: ``start`` (value: a queue
        id) sets the party's queue and starts matchmaking, ``leave`` stops it, ``select`` / ``lock`` (value: an
        agent's uuid) pick or lock an agent at agent select. RiotError: ``unknown`` (not one of these),
        ``notnow`` (not in a party, or not at agent select), or what Riot said."""
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
                match_id = self._now("pregame", f"{glz}/pregame/v1/players/{puuid}", headers, "MatchID")
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
            return self._kept("act", send, 0)
        finally:
            self.cache.put("live", None, 0)  # what the page shows next is asked again

    def _now(self, key: str, url: str, headers: dict, field: str) -> str:
        """The id GLZ gives for where the owner is now (their party, their agent select), or RiotError notnow."""
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
        """Changes the owner's loadout: ``change(loadout)`` edits PD's playerloadout (a fresh one) in place, and
        it is sent back whole, as the game client does. The collection is read again afterwards."""

        def send(tokens: dict, saved: dict) -> dict:
            base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
            url = f"{base}/personalization/v2/players/{puuid}/playerloadout"
            loadout = self._pd("loadout", url, headers)
            change(loadout)
            loadout.pop("Version", None)  # PD gives the new one
            body = json.dumps(loadout).encode()
            answer = self._pd("equip", url, {**headers, "Content-Type": "application/json"}, "PUT", body)
            return {"loadout": answer, "missing": [], "problem": ""}

        self.cache = TTLCache(8)
        try:
            return self._kept("equip", send, 0)
        finally:
            self.cache = TTLCache(8)

    def _contracts(self, tokens: dict, saved: dict) -> dict:
        url = f"{self._base(saved)}/contracts/v1/contracts/{saved['puuid']}"
        return {"contracts": self._pd("contracts", url, self._headers(tokens)), "missing": [], "problem": ""}

    def _glz(self, saved: dict) -> str:
        shard = str(saved.get("shard") or "ap")
        return GLZ.format(shard=shard if shard in SHARDS.values() and shard != "pbe" else "ap")

    def _live(self, tokens: dict, saved: dict) -> dict:
        glz, puuid, headers = self._glz(saved), str(saved["puuid"]), self._headers(tokens)
        # the game's session says whether it is open and where: the menus, agent select or a match
        running, loop = None, ""
        try:
            session = self._pd("session", f"{glz}/session/v1/sessions/{puuid}", headers)
            loop = str(session.get("loopState") or "").upper()
            running = str(session.get("cxnState") or "CONNECTED").upper() == "CONNECTED" and bool(loop)
        except RiotError as error:
            if error.kind == "expired":
                raise
            if error.kind == "down" and " 404" in f" {error.detail}":
                running = False
        if loop == "MENUS":
            return self._menus(glz, puuid, headers, saved)
        found = self._match(glz, puuid, headers, saved)
        if found["phase"]:
            return found | {"running": True}
        if running is None:  # the session could not be read: the party tells whether the game is open
            return self._menus(glz, puuid, headers, saved)
        return found | {"running": running}

    def _menus(self, glz: str, puuid: str, headers: dict, saved: dict) -> dict:
        """In the menus: the owner's party (its queue, whether it is matchmaking, its members)."""
        empty = {"phase": "", "match": None, "party": None, "names": {}, "ranks": {}, "at": self.clock()}
        try:
            party_id = self._now("party", f"{glz}/parties/v1/players/{puuid}", headers, "CurrentPartyID")
        except RiotError as error:
            if error.kind != "notnow":
                raise
            return empty | {"running": False, "missing": [], "problem": ""}
        party = self._pd("party", f"{glz}/parties/v1/parties/{party_id}", headers)
        members = [item for item in party.get("Members") or [] if isinstance(item, dict)]
        names, problem = self._open_names(saved, headers, members)
        return empty | {
            "running": True,
            "phase": "menus",
            "party": party,
            "names": names,
            "missing": ["names"] if problem else [],
            "problem": problem,
        }

    def _open_names(self, saved: dict, headers: dict, players: list) -> tuple[dict, str]:
        """The names of the players who do not hide theirs (the others' are never asked), and a problem."""
        open_names = [
            str(item.get("Subject")).lower()
            for item in players
            if isinstance(item, dict)
            and item.get("Subject")
            and not (item.get("PlayerIdentity") or {}).get("Incognito")
        ]
        try:
            return self._names(saved, headers, open_names), ""
        except RiotError as error:
            if error.kind == "expired":
                raise
            return {}, error.detail or error.kind

    def _ranks(self, saved: dict, headers: dict, subjects: list[str]) -> dict:
        """PD's mmr of each player (their rank, as the game's own scoreboard shows it), kept RANK_KEEP."""
        found, wanted = {}, []
        for subject in subjects:
            kept = self.ranks.get(subject)
            if kept is not None:
                found[subject] = kept
            elif _UUID.match(subject):
                wanted.append(subject)
        base = self._base(saved)

        def ask(subject: str):
            try:
                return subject, self._pd("rank", f"{base}/mmr/v1/players/{subject}", headers)
            except RiotError:
                return subject, {}  # not asked again this match either: the page falls back to GLZ's badge

        if wanted:
            with ThreadPoolExecutor(5, thread_name_prefix="riot-rank") as pool:
                for subject, answer in pool.map(ask, wanted):
                    self.ranks.put(subject, answer, RANK_KEEP)
                    found[subject] = answer
        return found

    def _match(self, glz: str, puuid: str, headers: dict, saved: dict) -> dict:
        """Agent select or the match the owner is in (``phase`` "" when neither)."""
        for phase, path in PHASES:
            try:
                player = self._pd(phase, f"{glz}{path}/players/{puuid}", headers)
            except RiotError as error:
                if error.kind == "down" and " 404" in f" {error.detail}":
                    continue  # not in this phase
                raise
            match_id = str(player.get("MatchID") or "")
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", match_id):
                continue
            match = self._pd(phase, f"{glz}{path}/matches/{match_id}", headers)
            team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
            players = [
                item
                for item in (team.get("Players") if phase == "pregame" else match.get("Players")) or []
                if isinstance(item, dict)
            ]
            names, problem = self._open_names(saved, headers, players)
            ranks = self._ranks(
                saved, headers, [str(item.get("Subject") or "").lower() for item in players if not item.get("IsCoach")]
            )
            return {
                "phase": phase,
                "match": match,
                "party": None,
                "names": names,
                "ranks": ranks,
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
            "at": self.clock(),
            "missing": [],
            "problem": "",
        }

    def _names(self, saved: dict, headers: dict, subjects: list[str]) -> dict:
        """PD's name service: ``{puuid: [game name, tag]}`` of these players."""
        if not subjects:
            return {}
        status, _, body = self.send(
            "PUT",
            f"{self._base(saved)}/name-service/v2/players",
            {**headers, "Content-Type": "application/json"},
            json.dumps(subjects).encode(),
        )
        if status == 401:
            raise RiotError("expired", "names 401")
        if status != 200:
            raise RiotError("busy" if status == 429 else "down", f"names {status or 'no answer'}")
        try:
            answer = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, AttributeError):
            answer = []
        found = {}
        for item in answer if isinstance(answer, list) else []:
            if isinstance(item, dict) and item.get("Subject") and item.get("GameName"):
                found[str(item["Subject"]).lower()] = [str(item["GameName"]), str(item.get("TagLine") or "")]
        return found

    def _queues(self, tokens: dict, saved: dict) -> dict:
        url = self._glz(saved) + "/parties/v1/parties/customgameconfigs"
        return {
            "config": self._pd("queues", url, self._headers(tokens)),
            "at": self.clock(),
            "missing": [],
            "problem": "",
        }

    def _kept(self, key: str, read, seconds: float = KEEP) -> dict:
        """``read(tokens, saved)``'s answer, kept ``seconds``; a refused token is traded for a new one once."""
        kept = self.cache.get(key)
        if kept is not None:
            return kept
        for renew in (False, True):
            tokens, saved = self._tokens(renew)
            try:
                found = read(tokens, saved)
                break
            except RiotError as error:
                if error.kind != "expired":
                    raise
                if renew:  # a token just made is refused too: not the cookie's fault
                    raise RiotError("down", error.detail) from None
        self.store.save(problem=found["problem"] or None)
        self.cache.put(key, found, seconds)
        return found

    # -- tokens ---------------------------------------------------------------------------------------

    def _tokens(self, renew: bool = False) -> tuple[dict, dict]:
        """The hour's tokens (saved, or traded for with the cookies) and what is saved."""
        with self._lock:
            saved = self.store.load()
            if not saved.get("puuid"):
                raise RiotError("nologin")
            token = saved.get("token") if isinstance(saved.get("token"), dict) else {}
            if (
                not renew
                and token.get("access")
                and token.get("entitlements")
                and _number(token.get("until")) > self.clock()
            ):
                return token, saved
            cookies = saved.get("cookies") if isinstance(saved.get("cookies"), dict) else {}
            if not cookies:  # an hour's address, now over
                self.store.save(token=None, signed_out=True)
                raise RiotError("signedout")
            try:
                token, cookies = self._authorize(cookies)
                token["entitlements"] = self._entitlements(token["access"])
            except RiotError as error:
                # the cookies are kept: a refusal may pass (and they are what the owner would paste again anyway)
                self.store.save(token=None, signed_out=True if error.kind == "signedout" else None)
                raise
            self.store.save(cookies=cookies, token=token, signed_out=None)
            return token, {**saved, "cookies": cookies, "token": token}

    def _authorize(self, cookies: dict) -> tuple[dict, dict]:
        """The cookies -> (the hour's tokens, the cookies as Riot renewed them)."""
        header = "; ".join(f"{name}={value}" for name, value in cookies.items())
        status, headers, _ = self.send("GET", AUTHORIZE, {"Cookie": header, "User-Agent": self._agent()}, None)
        _check(status, "auth")
        tokens = _tokens_in(_header(headers, "location"), self.clock())
        if not tokens:  # sent to the sign-in page instead
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
        """``{"puuid", "name", "tag", "shard"}`` of the signed-in account."""
        bearer = {"Authorization": f"Bearer {tokens['access']}", "User-Agent": self._agent()}
        status, _, body = self.send("GET", USERINFO, bearer, None)
        if status in (400, 401, 403):
            raise RiotError("signedout", f"userinfo {status}")
        info = _json(body) if status == 200 else {}
        puuid = str(info.get("sub") or _claims(tokens["access"]).get("sub") or "").lower()
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
            "shard": shard or "ap",  # the owner plays in Japan: Asia-Pacific when Riot does not say
        }

    def _agent(self) -> str:
        build = str((self.version() or {}).get("riotClientBuild") or "99.0.0.0000000.0000000")
        return f"RiotClient/{build} rso-auth (Windows;10;;Professional, x64)"

    # -- PD ---------------------------------------------------------------------------------------------

    def _headers(self, tokens: dict) -> dict:
        return {
            "Authorization": f"Bearer {tokens['access']}",
            "X-Riot-Entitlements-JWT": str(tokens["entitlements"]),
            "X-Riot-ClientPlatform": PLATFORM,
            "X-Riot-ClientVersion": str((self.version() or {}).get("riotClientVersion") or ""),
            "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit",
            "Accept": "application/json",
        }

    @staticmethod
    def _base(saved: dict) -> str:
        shard = str(saved.get("shard") or "ap")
        return PD.format(shard=shard if shard in SHARDS.values() else "ap")

    def _storefront(self, tokens: dict, saved: dict) -> dict:
        """The store (PD's storefront: today's four skins, the bundles, the night market, the accessories) and
        the wallet, side by side."""
        base, puuid, headers = self._base(saved), str(saved["puuid"]), self._headers(tokens)
        with ThreadPoolExecutor(2, thread_name_prefix="riot") as pool:
            asked = {
                # v3 is a POST since 2024 (v2's GET was turned off)
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
        answers, missing, problems = {}, [], []
        for key, future in asked.items():
            try:
                answers[key] = future.result()
            except RiotError as error:
                if error.kind == "expired":
                    raise
                missing.append(key)
                problems.append(error.detail or error.kind)
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
        asks = {"wallet": f"/store/v1/wallet/{puuid}", "loadout": f"/personalization/v2/players/{puuid}/playerloadout"}
        asks |= {kind: f"/store/v1/entitlements/{puuid}/{item_type}" for kind, item_type in ITEM_TYPES.items()}
        with ThreadPoolExecutor(4, thread_name_prefix="riot") as pool:
            asked = {key: pool.submit(self._pd, key, base + path, headers) for key, path in asks.items()}
        answers, missing, problems = {}, [], []
        for key, future in asked.items():
            try:
                answers[key] = future.result()
            except RiotError as error:
                if error.kind == "expired":
                    raise
                missing.append(key)
                problems.append(error.detail or error.kind)
        if not answers:
            raise RiotError("down", problems[0] if problems else "")
        owned = {}
        for kind in ITEM_TYPES:
            if kind not in answers:
                continue
            counts: dict[str, int] = {}
            for item in answers[kind].get("Entitlements") or []:
                if isinstance(item, dict) and item.get("ItemID"):
                    key = str(item["ItemID"]).lower()
                    counts[key] = counts.get(key, 0) + 1
            owned[kind] = counts
        wallet = _wallet(answers.get("wallet"))
        return {
            "name": str(saved.get("name") or ""),
            "tag": str(saved.get("tag") or ""),
            "wallet": wallet,
            "loadout": answers.get("loadout"),
            "owned": owned,
            "missing": missing,
            "problem": ", ".join(dict.fromkeys(problems)),
        }

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


def _wallet(answer) -> dict | None:
    """PD's wallet -> ``{currency uuid: amount}`` (None without one)."""
    balances = (answer or {}).get("Balances") if isinstance(answer, dict) else None
    if not isinstance(balances, dict):
        return None
    return {str(key).lower(): int(value) for key, value in balances.items() if isinstance(value, int | float)}


def _check(status: int, what: str) -> None:
    if status == 429:
        raise RiotError("busy", f"{what} 429")
    if status == 0 or status >= 500:
        raise RiotError("down", f"{what} {status or 'no answer'}")


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _json(body: bytes) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}


def _header(headers: list, name: str) -> str:
    return next((str(value) for key, value in headers if str(key).lower() == name), "")


def _tokens_in(address: str, now: float) -> dict | None:
    """The address Riot's sign-in sends the browser to -> ``{"access", "id", "until"}`` (None without a token)."""
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
    """``ssid`` alone (its value), ``ssid=...``, or a whole Cookie header (``Cookie: a=1; ssid=...``)."""
    text = re.sub(r"^\s*cookie\s*:\s*", "", text.strip(), flags=re.I).strip().strip("\"'")
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
    """The cookies with Riot's Set-Cookie answers applied (a new ssid each time; a removed one goes)."""
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


def _claims(token: str) -> dict:
    """What our own access token says (its puuid), unchecked: Riot checks it, we only read it."""
    try:
        middle = token.split(".")[1]
        return _json(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except (IndexError, ValueError):
        return {}
