"""The PC app: the Riot Client on the PC (localclient.py) and the app around the pages (desktop.py)."""

import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import ACCESS, ENTITLEMENTS, PUUID, make_app

from valtracker import desktop, localclient
from valtracker.localclient import EVENTS, LocalClient, launched, logged_server, read_lockfile, where

PARTY = "9a000000-0000-4000-8000-000000000001"
VERSION = "release-13.09-shipping-7-4012345"
FRIEND = "0d0d0d0d-1111-4222-8333-444444444444"


def private(loop: str, party_state: str = "DEFAULT", nested: bool = True) -> str:
    """A presence's private part, as the game writes it (base64 of JSON): nested since 2024, flat before."""
    if nested:
        data = {
            "isValid": True,
            "matchPresenceData": {"sessionLoopState": loop, "queueId": "competitive"},
            "partyPresenceData": {"partyId": PARTY, "partyState": party_state},
        }
    else:
        data = {"sessionLoopState": loop, "partyId": PARTY, "partyState": party_state, "queueId": "competitive"}
    return base64.b64encode(json.dumps(data).encode()).decode()


def presence(loop: str, party_state: str = "DEFAULT", puuid: str = PUUID, product: str = "valorant") -> dict:
    return {"puuid": puuid, "product": product, "private": private(loop, party_state)}


def event(name: str, data: dict, uri: str = "") -> str:
    return json.dumps([8, name, {"data": data, "eventType": "Update", "uri": uri}])


class FakeClient:
    """Answers like the Riot Client's local API would (``ask`` of localclient). ``signed_in``: an account is
    signed in to the client; ``sessions``: its open games; ``presences``: everyone's presence."""

    def __init__(self):
        self.asked: list[str] = []
        self.signed_in = True
        self.sessions = {
            "host_app": {"productId": "riot_client", "launchConfiguration": {"arguments": []}},
            "5f3a": {
                "productId": "valorant",
                "version": VERSION,
                "launchConfiguration": {
                    "arguments": ["-ares-deployment=ap", "-config-endpoint=https://shared.ap.a.pvp.net"]
                },
            },
        }
        self.presences = [presence("MENUS"), presence("INGAME", puuid=FRIEND)]

    def __call__(self, door, path):
        self.asked.append(path)
        if door != (5555, "secret"):
            return 0, None
        if path == "/entitlements/v1/token":
            if not self.signed_in:
                return 404, {"errorCode": "RESOURCE_NOT_FOUND"}
            return 200, {"accessToken": ACCESS, "token": ENTITLEMENTS, "subject": PUUID}
        if path == "/chat/v1/session":
            return 200, {"game_name": "Owner", "game_tag": "JP1", "puuid": PUUID, "state": "connected"}
        if path == "/product-session/v1/external-sessions":
            return 200, self.sessions
        if path == "/chat/v4/presences":
            return 200, {"presences": self.presences}
        return 404, None


class LocalTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.lockfile = self.folder / "lockfile"
        self.lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.log = self.folder / "ShooterGame.log"
        self.api = FakeClient()
        self.now = 1_800_000_000.0
        self.client = LocalClient(self.lockfile, self.log, ask=self.api, clock=lambda: self.now)

    def listening(self):
        """As the websocket does once connected: the first look at the launch and the presence."""
        with self.client._lock:
            self.client._listening = True
        self.client._look_again(read_lockfile(self.lockfile))

    def test_the_lockfile(self):
        self.assertEqual(read_lockfile(self.lockfile), (5555, "secret"))
        self.lockfile.write_text("Riot Client:1234", encoding="utf-8")
        self.assertIsNone(read_lockfile(self.lockfile))
        self.assertIsNone(read_lockfile(self.folder / "none"))

    def test_where_from_either_presence(self):
        for nested in (True, False):
            with self.subTest(nested=nested):
                found = where({"private": private("PREGAME", "MATCHMADE_GAME_STARTING", nested)})
                self.assertEqual(
                    found,
                    {
                        "loop": "PREGAME",
                        "party": PARTY,
                        "party_state": "MATCHMADE_GAME_STARTING",
                        "queue": "competitive",
                    },
                )
        self.assertEqual(where({"private": "not base64 {"})["loop"], "")

    def test_the_launch(self):
        self.assertEqual(launched(self.api.sessions), {"running": True, "shard": "ap", "version": VERSION})
        self.assertEqual(launched({"host_app": {"productId": "riot_client"}})["running"], False)
        self.assertEqual(launched(None)["running"], False)

    def test_the_server_in_the_log(self):
        self.log.write_bytes(b"x" * 100 + b"https://glz-latam-1.na.a.pvp.net/session/v1 ...")
        self.assertEqual(logged_server(self.log), ("latam", "na"))
        self.assertIsNone(logged_server(self.folder / "none"))

    def test_signed_in_through_the_client(self):
        tokens, account = self.client.signed_in()
        self.assertEqual((tokens["access"], tokens["entitlements"], tokens["version"]), (ACCESS, ENTITLEMENTS, VERSION))
        self.assertEqual(account, {"puuid": PUUID, "name": "Owner", "tag": "JP1", "shard": "ap", "region": "ap"})
        asked = len(self.api.asked)
        self.client.signed_in()
        self.assertEqual(len(self.api.asked), asked, "kept: not asked at every call")
        self.client.signed_in(renew=True)
        self.assertEqual(self.api.asked[asked:], ["/entitlements/v1/token"], "a refused token: asked again")
        self.now += 3600
        asked = len(self.api.asked)
        self.client.signed_in()
        self.assertIn("/entitlements/v1/token", self.api.asked[asked:], "the hour is over")

    def test_the_log_names_the_region(self):
        """Latin America and Brazil play on the na shard with a party server of their own (glz-latam-1.na)."""
        self.log.write_bytes(b"https://glz-latam-1.na.a.pvp.net/")
        self.api.sessions["5f3a"]["launchConfiguration"]["arguments"] = ["-ares-deployment=na"]
        account = self.client.signed_in()[1]
        self.assertEqual((account["region"], account["shard"]), ("latam", "na"))

    def test_nobody_signed_in_or_closed(self):
        self.api.signed_in = False
        self.assertIsNone(self.client.signed_in())
        self.api.signed_in = True
        self.lockfile.unlink()
        self.assertIsNone(self.client.signed_in())

    def test_events_tell_where_the_game_is(self):
        self.assertIsNone(self.client.presence(), "not listening: not known (GLZ is asked)")
        self.listening()
        self.assertEqual(self.client.presence(), {"running": True, **where(presence("MENUS"))})
        door, version = read_lockfile(self.lockfile), self.client.version
        self.client.handle(door, event(EVENTS[0], {"presences": [presence("INGAME", puuid=FRIEND)]}))
        self.assertEqual(self.client.version, version, "a friend's presence changes nothing")
        self.client.handle(door, event(EVENTS[0], {"presences": [presence("MENUS", "MATCHMADE_GAME_STARTING")]}))
        self.assertEqual(self.client.version, version + 1)
        self.assertEqual(self.client.presence()["party_state"], "MATCHMADE_GAME_STARTING")
        self.client.handle(door, event(EVENTS[0], {"presences": [presence("MENUS", "MATCHMADE_GAME_STARTING")]}))
        self.assertEqual(self.client.version, version + 1, "the same presence again changes nothing")
        pushed = "/riot-messaging-service/v1/message/ares-pregame/pregame/v1/matches/abc"
        self.client.handle(door, event(EVENTS[2], {"service": "ares-pregame"}, pushed))
        self.assertEqual(self.client.version, version + 2, "Riot's push about agent select")
        self.client.handle(door, event(EVENTS[2], {"service": "chat"}, "/riot-messaging-service/v1/message/chat"))
        self.client.handle(door, "[0, 1]")
        self.client.handle(door, "not json")
        self.assertEqual(self.client.version, version + 2)
        del self.api.sessions["5f3a"]  # VALORANT closed
        self.client.handle(door, event(EVENTS[1], {}))
        self.assertEqual(self.client.presence(), {"running": False})
        self.assertEqual(self.client.version, version + 3)

    def test_wait_for_a_change(self):
        self.assertEqual(self.client.wait(-1, 5), 0, "already newer: at once")
        started = time.monotonic()
        self.assertEqual(self.client.wait(0, 0.05), 0, "nothing new: after the time")
        self.assertGreaterEqual(time.monotonic() - started, 0.04)
        threading.Timer(0.05, self.client._changed_now).start()
        self.assertEqual(self.client.wait(0, 5), 1)

    def test_pages_left_make_way(self):
        """A page left while it waited still holds a server thread: the oldest waiting is let go."""
        answers = []
        waiting = [threading.Thread(target=lambda: answers.append(self.client.wait(0, 5))) for _ in range(4)]
        started = time.monotonic()
        for thread in waiting:
            thread.start()
            time.sleep(0.02)
        waiting[0].join(2)
        self.assertFalse(waiting[0].is_alive(), "the oldest of four let go")
        self.assertLess(time.monotonic() - started, 2)
        self.client._changed_now()
        for thread in waiting:
            thread.join(2)
        self.assertEqual(sorted(answers), [0, 1, 1, 1])

    def test_listening(self):
        """Connects with the lockfile's password, asks for the events, reads them until the client closes."""
        sent, closed = [], threading.Event()
        messages = [event(EVENTS[0], {"presences": [presence("PREGAME")]}), ""]

        class Socket:
            def __init__(socket, door):
                self.assertEqual(door, (5555, "secret"))

            def send(socket, text):
                sent.append(json.loads(text))

            def recv(socket):
                if messages:
                    return messages.pop(0)
                raise ConnectionError("closed")

            def ping(socket):
                pass

            def close(socket):
                closed.set()

        client = LocalClient(self.lockfile, self.log, ask=self.api, connect=Socket)
        seen = []
        original = client.handle
        client.handle = lambda door, text: (original(door, text), seen.append(client.presence()))
        with mock.patch.object(localclient, "RETRY_WAIT", 0.01), mock.patch.object(localclient, "CLOSED_WAIT", 0.01):
            client.start()
            self.assertTrue(closed.wait(5))
            client.stop()
            client._thread.join(5)
        self.assertEqual(sent, [[5, name] for name in EVENTS])
        self.assertEqual(seen[0]["loop"], "PREGAME")
        self.assertIsNone(client.presence(), "closed: not known any more")


class DesktopAppTest(unittest.TestCase):
    """The pages with the Riot Client on the PC (the desktop app)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lockfile = Path(folder.name) / "lockfile"
        self.lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.api = FakeClient()
        self.local = LocalClient(self.lockfile, Path(folder.name) / "none.log", ask=self.api)
        self.app, self.fetch, self.folder = make_app(local=self.local)
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def listening(self):
        with self.local._lock:
            self.local._listening = True
        self.local._look_again(read_lockfile(self.lockfile))

    def look(self) -> list[str]:
        """The Riot servers' addresses one look at the game asked, anew."""
        riot = self.app.config["VAL_RIOT"]
        riot.cache.put("live", None, 0)
        before = len(self.riot.asked)
        riot.live()
        return [url for _, url, _, _ in self.riot.asked[before:]]

    def test_signed_in_without_pasting(self):
        """No ssid: the Riot Client's account, its tokens and the client's version go to Riot."""
        self.riot.phase = "menus"
        text = self.client.get("/live").get_data(as_text=True)
        self.assertIn('class="seats"', text)
        self.assertIn("この PC の Riot クライアントから直接読むので", text)
        self.assertIn('data-push="/live/next"', text)
        sent = [headers for _, url, headers, _ in self.riot.asked if url.startswith("https://glz-ap-1.ap.")]
        self.assertTrue(sent)
        self.assertEqual({headers["X-Riot-ClientVersion"] for headers in sent}, {VERSION})
        settings = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Owner#JP1（AP）と連携中 — Riot クライアントから", settings)
        self.assertIn('<details class="ssid-way">', settings)
        self.assertNotIn('value="riot_off"', settings, "nothing to unlink: it is the client's")
        self.assertIn('data-live-push="/live/next"', self.client.get("/").get_data(as_text=True))

    def test_the_site_has_no_push(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        client = app.test_client()
        self.assertEqual(client.get("/live/next").status_code, 404)
        self.assertNotIn("data-push", client.get("/live").get_data(as_text=True))
        self.assertNotIn("data-live-push", client.get("/").get_data(as_text=True))

    def test_the_events_save_riots_calls(self):
        """The client says where the game is: GLZ's session is never asked, and nothing at all while it is
        closed."""
        self.listening()
        self.riot.phase = "menus"
        asked = self.look()
        self.assertFalse([url for url in asked if "/session/" in url], asked)
        self.assertFalse([url for url in asked if "/parties/v1/players/" in url], "the party's id came with it")
        self.assertEqual(len(self.look()), 1, "the party")
        del self.api.sessions["5f3a"]
        self.local.handle(read_lockfile(self.lockfile), event(EVENTS[1], {}))
        self.assertEqual(self.look(), [], "VALORANT closed: Riot is not asked")
        state = self.client.get("/live/state").get_json()
        self.assertEqual((state["phase"], state["running"]), ("", False))

    def test_an_event_reads_the_game_at_once(self):
        self.listening()
        self.riot.phase = "menus"
        riot = self.app.config["VAL_RIOT"]
        riot.live()
        before = len(self.riot.asked)
        riot.live()
        self.assertEqual(len(self.riot.asked), before, "kept a second")
        self.riot.phase = "pregame"
        self.local.handle(read_lockfile(self.lockfile), event(EVENTS[0], {"presences": [presence("PREGAME")]}))
        self.assertEqual(riot.live()["phase"], "pregame", "the event: read again at once")

    def test_the_next_change(self):
        version = self.local.version
        answer = self.client.get(f"/live/next?after={version - 1}")
        self.assertEqual(answer.get_json(), {"version": version})
        self.assertEqual(answer.headers["Cache-Control"], "no-store")
        threading.Timer(0.05, self.local._changed_now).start()
        self.assertEqual(self.client.get(f"/live/next?after={version}").get_json(), {"version": version + 1})
        self.assertEqual(self.client.get("/live/next", environ_base={"homepage.visitor": True}).status_code, 404)

    def test_client_closed(self):
        """Nobody signed in to the client: the pages say to open it, not to paste an ssid."""
        self.lockfile.unlink()
        text = self.client.get("/live").get_data(as_text=True)
        self.assertIn("Riot クライアントが開いていないか、サインインしていません。", text)
        self.assertEqual(self.client.get("/live/state").get_json()["problem"], "nologin")
        self.assertIn("未連携", self.client.get("/settings").get_data(as_text=True))


class ServerTest(unittest.TestCase):
    def test_only_this_pcs_address(self):
        def app(environ, start_response):
            start_response("200 OK", [])
            return [b"ok"]

        gate = desktop.OnlyHere(app, {"127.0.0.1:5000"})
        said = []
        self.assertEqual(gate({"HTTP_HOST": "127.0.0.1:5000"}, lambda *a: said.append(a[0])), [b"ok"])
        self.assertEqual(gate({"HTTP_HOST": "rebound.example"}, lambda *a: said.append(a[0])), [b"Forbidden"])
        self.assertEqual(said, ["200 OK", "403 Forbidden"])

    def test_the_builds_check(self):
        """What the .exe's build runs (--check), on a real server with the tests' answers."""
        with mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""}):
            app, _, _ = make_app(local=LocalClient(Path("/nonexistent/lockfile"), Path("/nonexistent/log")))
            self.addCleanup(app._test_folder.cleanup)
            server, base = desktop.serve(app)
            self.addCleanup(server.close)
            with mock.patch("sys.stdout"):
                self.assertEqual(desktop.check(base), 0)

    def test_the_data_folder(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/me/AppData/Local", "VAL_DESKTOP_HOME": ""}):
            self.assertEqual(desktop.data_folder(), Path("C:/Users/me/AppData/Local") / "VAL Tracker")
        with mock.patch.dict(os.environ, {"VAL_DESKTOP_HOME": "/tmp/x"}):
            self.assertEqual(desktop.data_folder(), Path("/tmp/x"))


if __name__ == "__main__":
    unittest.main()
