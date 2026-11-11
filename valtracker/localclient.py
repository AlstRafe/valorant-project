"""The Riot Client on this PC, for the desktop app (desktop.py): the game's own sign-in and where the game is, read
on this PC instead of asked of Riot's servers every few seconds.

The Riot Client answers on 127.0.0.1 (its port and password are in its lockfile, there while it runs): the local
API its own window uses. From it come

- the tokens of the account signed in to the client (nothing to paste on the settings page),
- that account's Riot ID, and from VALORANT's launch its shard, its party server and the client's version,
- events, through one websocket: the owner's presence (the menus, matchmaking, the match found, agent select, the
  match), VALORANT opening or closing, and Riot's pushes about the party, agent select and the match. /live is
  read again the moment one comes (``wait``), and nothing is asked while nothing happens.

Nothing here touches VALORANT itself: no reading or writing of its process or memory, nothing drawn over it, no
keys pressed. Its log is read once (only for the server's name, when the launch does not say). Every field is read
as optional: this is the client's own API, which Riot can change without notice.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import ssl
import threading
import time
from pathlib import Path

_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
#: Written by the Riot Client while it runs: ``name:pid:port:password:protocol``.
LOCKFILE = _HOME / "Riot Games" / "Riot Client" / "Config" / "lockfile"
#: VALORANT's log: the party server's address (glz-<region>-1.<shard>.a.pvp.net) is in its first lines.
LOG = _HOME / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"
#: The events listened to: the owner's presence, the games the client has open, and Riot's pushes.
PRESENCES = "OnJsonApiEvent_chat_v4_presences"
SESSIONS = "OnJsonApiEvent_product-session_v1_external-sessions"
MESSAGES = "OnJsonApiEvent_riot-messaging-service_v1_message"
EVENTS = (PRESENCES, SESSIONS, MESSAGES)
#: Riot's pushes that change what /live shows (the party, agent select, the match).
PUSHED = ("ares-parties", "ares-pregame", "ares-core-game")
#: Seconds between looks for the lockfile while the Riot Client is closed (one small file read).
CLOSED_WAIT = 5.0
#: Seconds before connecting again after the websocket closed (the client restarting).
RETRY_WAIT = 3.0
#: A quiet websocket is asked whether it is still there this often (seconds), and the presence read again then
#: (an event missed is caught within this).
QUIET = 30.0
#: Without events (no websocket), VALORANT's launch (shard, version) is read again after this long (seconds).
LAUNCH_KEEP = 60.0
#: Pages waiting at once for the next change (each holds one of the server's threads): an older one is let go.
WAITING = 3
#: A token is asked for again this long (seconds) before it runs out.
EARLY = 300
#: How much of VALORANT's log is read for the server's name (the address is in its first few kB).
LOG_BYTES = 2 * 1024 * 1024
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_GLZ = re.compile(rb"https://glz-([a-z]+)-1\.([a-z]+)\.a\.pvp\.net")
_SHARDS = ("ap", "kr", "eu", "na", "pbe")


class LocalError(Exception):
    """The Riot Client did not answer (closed, starting, or it refused)."""


def read_lockfile(path: Path) -> tuple[int, str] | None:
    """(port, password) of the running Riot Client, or None (not running)."""
    try:
        parts = Path(path).read_text(encoding="utf-8", errors="replace").strip().split(":")
    except OSError:
        return None
    if len(parts) < 5 or not parts[2].isdigit() or not parts[3]:
        return None
    return int(parts[2]), parts[3]


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()


def _local_tls() -> ssl.SSLContext:
    """The Riot Client's own certificate (Riot's private root, made for 127.0.0.1) is not one Windows knows: it is
    taken for this one address, which nothing but this PC can answer."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def ask(door: tuple[int, str], path: str) -> tuple[int, object]:
    """GET ``path`` of the Riot Client: (status, its JSON or None). Status 0: no answer."""
    port, password = door
    connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=3, context=_local_tls())
    try:
        connection.request("GET", path, headers={"Authorization": _basic(password), "Accept": "application/json"})
        answer = connection.getresponse()
        body = answer.read(4 * 1024 * 1024)
        try:
            return answer.status, json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            return answer.status, None
    except (OSError, http.client.HTTPException):
        return 0, None
    finally:
        connection.close()


class _Socket:
    """The Riot Client's websocket (websocket-client), as LocalClient uses it: ``send``, ``recv`` (None when
    nothing came for QUIET seconds; ConnectionError when it closed) and ``close``."""

    def __init__(self, door: tuple[int, str]):
        import websocket  # the desktop app's own requirement (requirements-desktop.txt)

        self._closed = (websocket.WebSocketConnectionClosedException,)
        self._quiet = websocket.WebSocketTimeoutException
        port, password = door
        self.socket = websocket.create_connection(
            f"wss://127.0.0.1:{port}/",
            header=[f"Authorization: {_basic(password)}"],
            sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
            http_no_proxy=["127.0.0.1", "localhost"],  # never through a proxy the PC may name
            timeout=QUIET,
            enable_multithread=True,
        )

    def send(self, text: str) -> None:
        self.socket.send(text)

    def recv(self) -> str | None:
        try:
            message = self.socket.recv()
        except self._quiet:
            return None
        except self._closed as error:
            raise ConnectionError("closed") from error
        if message is None:
            raise ConnectionError("closed")
        return message.decode("utf-8", "replace") if isinstance(message, bytes) else message

    def ping(self) -> None:
        self.socket.ping()

    def close(self) -> None:
        try:
            self.socket.close()
        except Exception:  # noqa: BLE001 - closing a broken socket
            pass


def _claims(token: str) -> dict:
    try:
        middle = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _private(value) -> dict:
    """A presence's ``private`` part: base64 of a JSON object (or the object itself)."""
    if isinstance(value, dict):
        return value
    text = str(value or "")
    for decode in (lambda: base64.b64decode(text + "=" * (-len(text) % 4)), text.encode):
        try:
            found = json.loads(decode())
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(found, dict):
            return found
    return {}


def where(item: dict) -> dict:
    """The owner's presence -> ``{"loop": MENUS / PREGAME / INGAME or "", "party": the party's id, "party_state":
    e.g. MATCHMAKING or MATCHMADE_GAME_STARTING, "queue"}``. Both shapes the game has used: everything at the top
    (before 2024), or in matchPresenceData / partyPresenceData."""
    data = _private(item.get("private"))
    match = data.get("matchPresenceData") if isinstance(data.get("matchPresenceData"), dict) else data
    party = data.get("partyPresenceData") if isinstance(data.get("partyPresenceData"), dict) else data
    party_id = str(party.get("partyId") or data.get("partyId") or "").lower()
    return {
        "loop": str(match.get("sessionLoopState") or data.get("sessionLoopState") or "").upper(),
        "party": party_id if _UUID.match(party_id) else "",
        "party_state": str(party.get("partyState") or data.get("partyState") or "").upper(),
        "queue": str(match.get("queueId") or data.get("queueId") or "").lower(),
    }


def launched(sessions) -> dict:
    """The client's open games (product-session's external sessions) -> ``{"running": whether VALORANT is open,
    "shard", "version": the client's version (release-...)}`` (the shard from its launch arguments)."""
    for session in (sessions or {}).values() if isinstance(sessions, dict) else []:
        if not isinstance(session, dict) or str(session.get("productId") or "").lower() != "valorant":
            continue
        config = session.get("launchConfiguration") if isinstance(session.get("launchConfiguration"), dict) else {}
        arguments = " ".join(str(item) for item in config.get("arguments") or [] if isinstance(item, str))
        found = re.search(r"-ares-deployment=([a-z]+)", arguments) or re.search(
            r"shared\.([a-z]+)\.a\.pvp\.net", arguments
        )
        version = str(session.get("version") or "")
        return {
            "running": True,
            "shard": found.group(1) if found and found.group(1) in _SHARDS else "",
            "version": version if version.startswith("release-") else "",
        }
    return {"running": False, "shard": "", "version": ""}


def logged_server(path: Path) -> tuple[str, str] | None:
    """(region, shard) of the party server in VALORANT's log, or None. Read with the log left open to the game."""
    try:
        with open(path, "rb") as log:
            found = _GLZ.search(log.read(LOG_BYTES))
    except OSError:
        return None
    return (found.group(1).decode(), found.group(2).decode()) if found else None


class LocalClient:
    """The Riot Client on this PC. ``start`` begins listening to its events (a thread, asleep while nothing
    happens); ``signed_in`` gives riot.Riot the tokens; ``presence`` and ``version`` / ``wait`` tell /live where the
    game is and when it changed. ``ask`` and ``connect`` are the tests' own."""

    def __init__(self, lockfile: Path = LOCKFILE, log: Path = LOG, ask=ask, connect=_Socket, clock=time.time):
        self.lockfile, self.log = Path(lockfile), Path(log)
        self.ask, self.connect, self.clock = ask, connect, clock
        self._lock = threading.Lock()
        self._changed = threading.Condition()
        #: Grows by one at every change /live should show at once.
        self.version = 0
        #: The pages waiting for it (``wait``): their tickets, oldest first, and the ones told to stop waiting.
        self._tickets = 0
        self._waiting: list[int] = []
        self._let_go: set[int] = set()
        self._tokens: dict | None = None
        self._account: dict | None = None
        #: VALORANT's launch ({"running", "shard", "version"}), read when the client says it changed.
        self._game: dict | None = None
        self._server: tuple[str, str] | None = None
        #: The launch as read when not listening (no events): (when, launch), asked again after LAUNCH_KEEP.
        self._launch_read: tuple[float, dict] | None = None
        #: The owner's presence (where), while VALORANT is open.
        self._seen: dict | None = None
        self._listening = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- what riot.Riot and the pages use ----------------------------------------------------------------

    def signed_in(self, renew: bool = False) -> tuple[dict, dict] | None:
        """(tokens, account) of the account signed in to the Riot Client, or None (closed, or nobody signed in).
        tokens: riot.Riot's ``{"access", "entitlements", "until", "version"}``; account: ``{"puuid", "name",
        "tag", "shard", "region"}``. Asked of the client once an hour (and when ``renew``: a token refused)."""
        door = read_lockfile(self.lockfile)
        if door is None:
            with self._lock:
                self._tokens = self._account = None
            return None
        with self._lock:
            tokens = self._tokens
            if renew or tokens is None or tokens["door"] != door or tokens["until"] <= self.clock():
                tokens = self._read_tokens(door)
                if tokens is None:
                    return None
            if self._account is None or self._account["puuid"] != tokens["puuid"] or self._account["door"] != door:
                self._account = self._read_account(door, tokens["puuid"])
            game = self._launch(door)
            server = self._server_name(game)
            account = {
                "puuid": tokens["puuid"],
                "name": self._account["name"],
                "tag": self._account["tag"],
                "shard": server[1],
                "region": server[0],
            }
            given = {key: tokens[key] for key in ("access", "entitlements", "until")}
            return given | {"version": game.get("version") or ""}, account

    def presence(self) -> dict | None:
        """Where the game is, from the client's events: ``{"running": False}`` (VALORANT closed), ``{"running":
        True, "loop", "party", "party_state", "queue"}``, or None (not known: not listening, or the game still
        opening; the party server is asked then, as without the app)."""
        with self._lock:
            if not self._listening or self._game is None:
                return None
            if not self._game["running"]:
                return {"running": False}
            if not self._seen or not self._seen["loop"]:
                return None
            return {"running": True, **self._seen}

    def wait(self, seen: int, seconds: float) -> int:
        """Waits until ``version`` is not ``seen`` (or ``seconds`` passed): the version then. At most WAITING wait
        at once: a page left while it waited (its server thread still waiting) makes way for the next page's."""
        with self._changed:
            self._tickets += 1
            ticket = self._tickets
            self._waiting.append(ticket)
            if len(self._waiting) > WAITING:
                self._let_go.add(self._waiting.pop(0))
                self._changed.notify_all()
            try:
                self._changed.wait_for(
                    lambda: self.version != seen or ticket in self._let_go, timeout=max(0.0, seconds)
                )
            finally:
                self._let_go.discard(ticket)
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
            return self.version

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._listen, name="riot-client", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- reading the client ------------------------------------------------------------------------------

    def _get(self, door: tuple[int, str], path: str):
        status, value = self.ask(door, path)
        if status != 200:
            raise LocalError(f"{path} {status or 'no answer'}")
        return value

    def _read_tokens(self, door: tuple[int, str]) -> dict | None:
        try:
            found = self._get(door, "/entitlements/v1/token")
        except LocalError:
            return None
        found = found if isinstance(found, dict) else {}
        access, entitlements = str(found.get("accessToken") or ""), str(found.get("token") or "")
        puuid = str(found.get("subject") or _claims(access).get("sub") or "").lower()
        if not access or not entitlements or not _UUID.match(puuid):
            return None  # the client is open but nobody is signed in yet
        until = float(_claims(access).get("exp") or self.clock() + 3600) - EARLY
        self._tokens = {"access": access, "entitlements": entitlements, "until": until, "puuid": puuid, "door": door}
        return self._tokens

    def _read_account(self, door: tuple[int, str], puuid: str) -> dict:
        try:
            found = self._get(door, "/chat/v1/session")
        except LocalError:
            found = {}
        found = found if isinstance(found, dict) else {}
        mine = str(found.get("puuid") or "").lower() == puuid
        return {
            "puuid": puuid,
            "door": door,
            "name": str(found.get("game_name") or "") if mine else "",
            "tag": str(found.get("game_tag") or "") if mine else "",
        }

    def _read_game(self, door: tuple[int, str]) -> dict:
        try:
            return launched(self._get(door, "/product-session/v1/external-sessions"))
        except LocalError:
            return {"running": False, "shard": "", "version": ""}

    def _launch(self, door: tuple[int, str]) -> dict:
        """VALORANT's launch: the events' (kept as they come), else read now and then."""
        if self._game is not None:
            return self._game
        if self._launch_read is None or self.clock() - self._launch_read[0] > LAUNCH_KEEP:
            self._launch_read = (self.clock(), self._read_game(door))
        return self._launch_read[1]

    def _server_name(self, game: dict) -> tuple[str, str]:
        """(region, shard) of the party server: the launch's shard, the log's address, else Asia-Pacific (the
        owner plays in Japan)."""
        if self._server is None:
            self._server = logged_server(self.log)
        if self._server and (not game.get("shard") or self._server[1] == game["shard"]):
            return self._server
        shard = game.get("shard") or "ap"
        return shard, shard

    def _read_presence(self, door: tuple[int, str], puuid: str) -> dict | None:
        try:
            found = self._get(door, "/chat/v4/presences")
        except LocalError:
            return None
        return self._mine((found or {}).get("presences") if isinstance(found, dict) else None, puuid)

    @staticmethod
    def _mine(presences, puuid: str) -> dict | None:
        for item in presences or []:
            if (
                isinstance(item, dict)
                and str(item.get("puuid") or "").lower() == puuid
                and str(item.get("product") or "").lower() == "valorant"
            ):
                return where(item)
        return None

    # -- the events ---------------------------------------------------------------------------------------

    def _changed_now(self) -> None:
        with self._changed:
            self.version += 1
            self._changed.notify_all()

    def _look_again(self, door: tuple[int, str]) -> None:
        """Reads the game's launch and the owner's presence (when connecting, and now and then)."""
        game = self._read_game(door)
        found = self.signed_in()
        seen = self._read_presence(door, found[1]["puuid"]) if found and game["running"] else None
        with self._lock:
            changed = (game, seen) != (self._game, self._seen)
            if game.get("shard") and self._game and game["shard"] != self._game.get("shard"):
                self._server = None
            self._game, self._seen = game, seen
        if changed:
            self._changed_now()

    def handle(self, door: tuple[int, str], text: str) -> None:
        """One message of the websocket: ``[8, event, {"data", "eventType", "uri"}]``."""
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, list) or len(message) < 3 or message[0] != 8 or not isinstance(message[2], dict):
            return
        event, payload = message[1], message[2]
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if event == PRESENCES:
            with self._lock:
                puuid = (self._tokens or {}).get("puuid", "")
            seen = self._mine(data.get("presences"), puuid) if puuid else None
            if seen is None:
                return  # a friend's presence
            with self._lock:
                changed = seen != self._seen
                self._seen = seen
            if changed:
                self._changed_now()
        elif event == SESSIONS:
            game = self._read_game(door)
            with self._lock:
                changed = game != self._game
                self._game = game
                if not game["running"]:
                    self._seen = None
            if changed:
                self._changed_now()
        elif event == MESSAGES:
            said = f"{payload.get('uri') or ''} {data.get('service') or ''} {data.get('resource') or ''}"
            if any(word in said for word in PUSHED):
                self._changed_now()

    def _listen(self) -> None:
        while not self._stop.is_set():
            door = read_lockfile(self.lockfile)
            if door is None:
                self._forget()
                self._stop.wait(CLOSED_WAIT)
                continue
            try:
                socket = self.connect(door)
            except Exception:  # noqa: BLE001 - the client starting, or no websocket library
                self._stop.wait(RETRY_WAIT)
                continue
            try:
                for event in EVENTS:
                    socket.send(json.dumps([5, event]))
                with self._lock:
                    self._listening = True
                self._look_again(door)
                looked = self.clock()
                while not self._stop.is_set():
                    text = socket.recv()
                    if text is None:  # quiet: still there? (and an event missed is caught)
                        socket.ping()
                        if self.clock() - looked >= QUIET:
                            self._look_again(door)
                            looked = self.clock()
                    elif text:
                        self.handle(door, text)
            except Exception:  # noqa: BLE001 - the client closed or restarted: connect again
                pass
            finally:
                socket.close()
                self._forget()
            self._stop.wait(RETRY_WAIT)

    def _forget(self) -> None:
        with self._lock:
            was = self._listening or self._game is not None
            self._listening, self._game, self._seen, self._server = False, None, None, None
        if was:
            self._changed_now()
