"""The PC app: the Riot Client on the PC (localclient.py) and the app around the pages (desktop.py)."""

import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import ACCESS, ENTITLEMENTS, PUUID, make_app

from valtracker import desktop, localclient, release
from valtracker.localclient import EVENTS, LocalClient, launched, logged_server, read_lockfile, where
from valtracker.paths import ROOT

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
        #: The owner's friends list, as the client's chat gives it.
        self.friends = [{"puuid": FRIEND, "game_name": "Tomo", "game_tag": "JP2", "last_online_ts": 0}]

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
        if path == "/chat/v4/friends":
            return 200, {"friends": self.friends}
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
        self.assertIn("ゲームと連動して、キュー開始とエージェント選択ができます。", text)
        self.assertNotIn("スマホからも", text)
        self.assertIn('data-push="/live/next"', text)
        sent = [headers for _, url, headers, _ in self.riot.asked if url.startswith("https://glz-ap-1.ap.")]
        self.assertTrue(sent)
        self.assertEqual({headers["X-Riot-ClientVersion"] for headers in sent}, {VERSION})
        settings = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Owner#JP1（AP）と連携中 — Riot クライアント経由", settings)
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
        self.assertIn("Riot クライアントが起動していないか、サインインしていません。", text)
        self.assertEqual(self.client.get("/live/state").get_json()["problem"], "nologin")
        self.assertIn("未連携", self.client.get("/settings").get_data(as_text=True))


class AppPagesTest(unittest.TestCase):
    """What the pages do differently in the PC app's window (no browser bar around them)."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": "", "VAL_PUBLIC_SITE": ""})  # the default site
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        lockfile = Path(folder.name) / "lockfile"
        lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.local = LocalClient(lockfile, Path(folder.name) / "none.log", ask=FakeClient())
        self.app, _, _ = make_app(local=self.local)
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")
        self.site, _, _ = make_app()
        self.addCleanup(self.site._test_folder.cleanup)

    def test_the_windows_own_buttons(self):
        text = self.client.get("/").get_data(as_text=True)
        self.assertIn('<body class="app" data-public="https://haruxera.duckdns.org/val">', text)
        for go in ("back", "forward", "reload"):
            self.assertIn(f'data-go="{go}"', text)
        site = self.site.test_client().get("/").get_data(as_text=True)
        self.assertIn("<body>", site)
        self.assertNotIn("data-go=", site)

    def test_links_given_away_lead_to_the_public_site(self):
        """The app's own address (127.0.0.1, a port of the moment) leads nowhere on another device."""
        code = "0;P;c;5"
        text = self.client.get("/crosshair", query_string={"code": code}).get_data(as_text=True)
        self.assertIn('data-copy="https://haruxera.duckdns.org/val/crosshair?code=0', text)
        calendar = self.client.get("/shop/reminder.ics").get_data(as_text=True)
        self.assertIn("https://haruxera.duckdns.org/val/shop", calendar)
        site = self.site.test_client().get("/crosshair", query_string={"code": code}).get_data(as_text=True)
        self.assertIn('data-copy="http://localhost/crosshair?code=0', site)

    def test_the_first_start_says_where_the_key_goes(self):
        self.app.config["VAL_SETTINGS"].save(henrik_key="")
        text = self.client.get("/").get_data(as_text=True)
        self.assertIn("HenrikDev の API キーが未設定です。", text)
        self.assertIn("プレイヤー検索に必要です", text)
        self.app.config["VAL_SETTINGS"].save(henrik_key="HDEV-x")
        self.assertNotIn("API キーが未設定です", self.client.get("/").get_data(as_text=True))
        visitor = self.site.test_client().get("/", environ_base={"homepage.visitor": True})
        self.assertNotIn("API キーが未設定です", visitor.get_data(as_text=True))

    def test_settings_of_the_app(self):
        text = self.client.get("/settings").get_data(as_text=True)
        self.assertNotIn("サイトの持ち主専用", text)
        self.assertIn('data-pref="val.live.switch"', text)
        self.assertIn("ソースから実行中", text)
        self.assertIn(self.app.instance_path, text)
        self.assertIn("キーはこの PC にのみ保存されます", text)
        self.app.config["VAL_BUILD"] = {"commit": "a" * 40, "date": "2026-11-12"}
        self.app.config["VAL_UPDATE_LOOKED"] = 1.0
        self.assertIn("2026-11-12 · aaaaaaa</code> — 最新です", self.client.get("/settings").get_data(as_text=True))
        self.assertNotIn('id="app-title"', self.site.test_client().get("/settings").get_data(as_text=True))

    def test_a_newer_build_is_told_on_every_page(self):
        self.assertNotIn("app-update", self.client.get("/tools").get_data(as_text=True))
        url = "https://github.com/AlstRafe/valorant-project/releases/download/desktop/VALTracker-windows.zip"
        self.app.config["VAL_UPDATE"] = {"commit": "b" * 40, "date": "2026-11-13", "url": url}
        for path in ("/tools", "/live", "/settings"):
            text = self.client.get(path).get_data(as_text=True)
            self.assertIn(f'<a href="{url}">ダウンロード</a>', text, path)

    def test_live_while_the_game_is_closed(self):
        """Nothing to do until the game opens (the app opens on /live): the owner's other pages a click away."""
        self.app.riot.phase = ""
        text = self.client.get("/live").get_data(as_text=True)
        self.assertIn('class="me-box"', text)
        self.assertIn('href="/p/Owner/JP1"', text)
        self.assertIn('href="/shop"', text)

    def test_agent_select_in_one_screen(self):
        """Agent select: the team beside the agents to pick, the page's lead not above them."""
        self.app.riot.phase = "pregame"
        text = self.client.get("/live").get_data(as_text=True)
        self.assertIn('<div class="live-cols pregame-cols">', text)
        self.assertIn('<section class="live-pick"', text)
        self.assertNotIn('<p class="lead">', text)
        self.assertNotIn('class="me-box"', text)
        self.app.riot.phase = "ingame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertIn('<div class="live-cols">', self.client.get("/live").get_data(as_text=True))


class UpdateTest(unittest.TestCase):
    """The repository is private (GitHub says 404 to the app): the site says which build is the newest."""

    BUILD = {"commit": "a" * 40, "time": 1_794_463_677, "date": "2026-11-12 15:07"}
    LATER = {"commit": "b" * 40, "time": 1_794_470_000}
    NOW = 1_794_480_000.0

    def test_a_newer_build(self):
        found = release.newer(self.BUILD, self.LATER, self.NOW)
        self.assertEqual((found["commit"], found["url"], found["ready"]), ("b" * 40, release.PAGE, True))
        self.assertTrue(found["url"].startswith("https://github.com/AlstRafe/valorant-project/releases/"))
        self.assertIsNone(release.newer(self.BUILD, {"commit": "a" * 40, "time": 1_794_470_000}, self.NOW), "this one")
        self.assertIsNone(release.newer(None, self.LATER, self.NOW), "run from the source: nothing to compare")
        older = {"commit": "c" * 40, "time": self.BUILD["time"] - 60}
        self.assertIsNone(release.newer(self.BUILD, older, self.NOW), "the site is behind this build")

    def test_a_change_minutes_old_is_still_being_built(self):
        soon = self.LATER["time"] + 5 * 60
        self.assertFalse(release.newer(self.BUILD, self.LATER, soon)["ready"])
        self.assertTrue(release.newer(self.BUILD, self.LATER, self.LATER["time"] + release.BUILT_AFTER)["ready"])

    def test_an_answer_that_is_not_one(self):
        for told in (None, [], "x", {}, {"commit": "master", "time": 1}, {"commit": "b" * 40, "time": "soon"}):
            self.assertIsNone(release.newer(self.BUILD, told, self.NOW), told)
        self.assertIsNone(release.newer(self.BUILD, {"commit": "b" * 40, "time": True}, self.NOW))
        # nothing the answer says is opened: the link is the release's page
        told = self.LATER | {"url": "https://evil.example/", "page": "https://evil.example/"}
        self.assertEqual(release.newer(self.BUILD, told, self.NOW)["url"], release.PAGE)

    def test_the_builds_stamp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        stamp = Path(folder.name) / "BUILD"
        with mock.patch.object(desktop, "__file__", str(Path(folder.name) / "desktop.py")):
            self.assertIsNone(desktop.read_build(), "no stamp: run from the source")
            stamp.write_text("c" * 40 + " 1794463677", encoding="ascii")
            found = desktop.read_build()
            self.assertEqual((found["commit"], found["time"]), ("c" * 40, 1_794_463_677))
            self.assertRegex(found["date"], r"^2026-11-1[12] \d\d:\d\d$")
            for wrong in ("not-a-commit 1794463677", "c" * 40 + " 2026-11-12", "c" * 40, ""):
                stamp.write_text(wrong, encoding="ascii")
                self.assertIsNone(desktop.read_build(), wrong)

    def test_the_stamp_is_the_last_commit_that_changed_the_app(self):
        """The same answer in the build (desktop.yml) and on the site: here, this repository's own history."""
        if not (ROOT / ".git").exists():
            self.skipTest("not a clone")
        found = release.source(ROOT)
        self.assertRegex(found["commit"], "^[0-9a-f]{40}$")
        self.assertGreater(found["time"], 1_700_000_000)
        self.assertIsNone(release.source(Path(tempfile.gettempdir()) / "no-such-clone"))

    def test_the_site_says_the_newest_build(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        with mock.patch.object(release, "latest", return_value=self.LATER):
            answer = app.test_client().get("/app/latest")
        self.assertEqual(answer.get_json(), self.LATER)
        self.assertIn("max-age=300", answer.headers["Cache-Control"])
        with mock.patch.object(release, "latest", return_value=None):
            self.assertEqual(app.test_client().get("/app/latest").status_code, 404, "not a clone")

    def app(self):
        """The PC app (the default public site, a Riot Client that is not running)."""
        env = mock.patch.dict(os.environ, {"VAL_PUBLIC_SITE": ""})
        env.start()
        self.addCleanup(env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        local = LocalClient(Path(folder.name) / "lockfile", Path(folder.name) / "none.log", ask=FakeClient())
        app, _, _ = make_app(local=local)
        self.addCleanup(app._test_folder.cleanup)
        return app

    def test_the_app_itself_does_not_answer_it(self):
        app = self.app()
        with mock.patch.object(release, "latest", return_value=self.LATER) as asked:
            self.assertEqual(app.test_client().get("/app/latest").status_code, 404)
        asked.assert_not_called()

    def test_read_again_only_now_and_then(self):
        with (
            mock.patch.object(release, "source", side_effect=[self.LATER, None]) as read,
            mock.patch.dict(release._kept, {"at": 0.0, "found": None}),
        ):
            self.assertEqual(release.latest(ROOT, now=1000.0), self.LATER)
            self.assertEqual(release.latest(ROOT, now=1000.0 + release.KEPT - 1), self.LATER)
            self.assertEqual(read.call_count, 1)
            self.assertIsNone(release.latest(ROOT, now=1000.0 + release.KEPT))

    def looked(self, told, now: float):
        app = self.app()
        app.config["VAL_BUILD"] = self.BUILD
        stop = threading.Event()
        self.addCleanup(stop.set)
        asked = []
        looked = threading.Event()

        def read(site):
            asked.append(site)
            looked.set()
            if isinstance(told, Exception):
                raise told
            return told

        with (
            mock.patch.multiple(desktop, LOOK_FIRST=0.01, LOOK_EVERY=60, LOOK_AGAIN=60),
            mock.patch.object(desktop.time, "time", return_value=now),
        ):
            desktop.look_for_updates(app, stop, read)
            self.assertTrue(looked.wait(5))
            for _ in range(100):
                if app.config["VAL_UPDATE_LOOKED"]:
                    break
                time.sleep(0.01)
        self.assertEqual(asked, ["https://haruxera.duckdns.org/val"])
        return app

    def test_looked_for_while_the_app_runs(self):
        app = self.looked(self.LATER, self.NOW)
        self.assertEqual(app.config["VAL_UPDATE"]["url"], release.PAGE)
        self.assertTrue(app.config["VAL_UPDATE_LOOKED"])

    def test_not_told_while_it_is_being_built_or_the_site_is_down(self):
        app = self.looked(self.LATER, self.LATER["time"] + 60)
        self.assertIsNone(app.config["VAL_UPDATE"])
        self.assertFalse(app.config["VAL_UPDATE_LOOKED"], "not yet 「最新です」: looked at again soon")
        app = self.looked(OSError("no network"), self.NOW)
        self.assertIsNone(app.config["VAL_UPDATE"])

    def test_only_the_site_is_asked(self):
        with self.assertRaises(ValueError):
            desktop._latest("http://127.0.0.1:1")
        with self.assertRaises(ValueError):
            desktop._latest("")

    def test_not_looked_for_from_the_source(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        with mock.patch.object(threading, "Thread") as thread:
            desktop.look_for_updates(app, threading.Event(), lambda site: {})
        thread.assert_not_called()


class NoWindowTest(unittest.TestCase):
    """The app as it reaches a PC (unpacked from a downloaded zip), and what it does when it has no window."""

    def test_the_mark_of_a_download_is_taken_off_the_apps_own_files(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "_internal" / "pythonnet").mkdir(parents=True)
            marked = [home / "_internal" / "pythonnet" / "Python.Runtime.dll", home / "_internal" / "base.zip"]
            for path in (*marked, home / "VALTracker.exe"):
                path.write_bytes(b"")
            removed = []

            def remove(name):
                if not any(name == f"{path}:Zone.Identifier" for path in marked):
                    raise FileNotFoundError(name)  # the .exe ("run anyway" took its mark off) and the folders
                removed.append(name)

            self.assertEqual(desktop.unmark(home, remove), 2)
            self.assertEqual(sorted(removed), sorted(f"{path}:Zone.Identifier" for path in marked))
            self.assertTrue(all(path.exists() for path in marked), "the files themselves stay")
            self.assertEqual(desktop.unmark(home), 0, "nothing marked: nothing done")
        self.assertEqual(desktop.unmark(None), 0, "the source: no folder of its own")

    def test_a_folder_that_cannot_be_written_is_left(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "VALTracker.exe").write_bytes(b"")

            def remove(name):
                raise PermissionError(name)

            self.assertEqual(desktop.unmark(Path(folder), remove), 0)

    def test_the_built_app_asks_before_it_opens_a_browser(self):
        log = Path("desktop.log")
        for why in (desktop.NO_PART, desktop.NO_RUNTIME):
            with (
                self.subTest(why=why),
                mock.patch.object(desktop, "ask", return_value=False) as asked,
                mock.patch.object(desktop.webbrowser, "open") as opened,
            ):
                self.assertFalse(desktop.in_browser("http://127.0.0.1:1/live", why, log, built=True))
                opened.assert_not_called()
                (words,), _ = asked.call_args
                self.assertIn("ウィンドウを開けませんでした", words)
                self.assertIn("ブラウザーで開きますか", words)
        with (
            mock.patch.object(desktop, "ask", return_value=True),
            mock.patch.object(desktop.webbrowser, "open") as opened,
        ):
            self.assertTrue(desktop.in_browser("http://127.0.0.1:1/live", desktop.NO_PART, log, built=True))
            opened.assert_called_once_with("http://127.0.0.1:1/live")

    def test_a_browser_that_was_asked_for_opens_without_a_question(self):
        with (
            mock.patch.object(desktop, "ask") as asked,
            mock.patch.object(desktop.webbrowser, "open") as opened,
        ):
            self.assertTrue(desktop.in_browser("http://127.0.0.1:1/live", "", Path("desktop.log"), built=True))
            self.assertTrue(desktop.in_browser("http://127.0.0.1:1/live", desktop.NO_PART, Path("x"), built=False))
            asked.assert_not_called()
            self.assertEqual(opened.call_count, 2)

    def test_no_window_says_why(self):
        """Without pywebview (as here), the reason is printed (the app's log) and told, not a wrong guess."""
        out = io.StringIO()
        with (
            mock.patch.dict(sys.modules, {"webview": None}),
            contextlib.redirect_stdout(out),
            tempfile.TemporaryDirectory() as folder,
        ):
            self.assertEqual(desktop.window("http://127.0.0.1:1/live", Path(folder)), desktop.NO_PART)
        self.assertIn("no window: ", out.getvalue())
        self.assertNotIn("WebView2", desktop.IN_BROWSER, "the box after the browser opened guesses nothing")


class OpenedTwiceTest(unittest.TestCase):
    """A second start brings the running app's window back instead of only saying that it is open."""

    def test_which_window_is_the_apps(self):
        self.assertTrue(desktop.is_app_window("VAL Tracker", "WindowsForms10.Window.8.app.0.141b42a_r6_ad1"))
        self.assertFalse(desktop.is_app_window("VAL Tracker", "CabinetWClass"), "the data folder open in Explorer")
        self.assertFalse(desktop.is_app_window("VAL Tracker - Google Chrome", "WindowsForms10.Window.8.app.0.1"))

    def test_the_window_comes_back(self):
        with (
            mock.patch.object(desktop, "first", return_value=False),
            mock.patch.object(desktop, "show_running", return_value=True) as shown,
            mock.patch.object(desktop, "tell") as told,
        ):
            self.assertEqual(desktop.main([]), 0)
        shown.assert_called_once()
        told.assert_not_called()

    def test_said_when_the_window_is_not_there_yet(self):
        with (
            mock.patch.object(desktop, "first", return_value=False),
            mock.patch.object(desktop, "show_running", return_value=False),
            mock.patch.object(desktop, "tell") as told,
        ):
            self.assertEqual(desktop.main([]), 0)
        told.assert_called_once_with(desktop.ALREADY_OPEN)

    @unittest.skipIf(os.name == "nt", "another system's answer")
    def test_no_windows_to_look_through_elsewhere(self):
        self.assertFalse(desktop.show_running())


class PlaceTest(unittest.TestCase):
    """The window opens where it was left (window.json), on a screen that is still there."""

    SCREENS = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def save(self, **values):
        (self.folder / desktop.PLACE).write_text(json.dumps(values), encoding="utf-8")

    def test_first_start(self):
        self.assertEqual(desktop.placed(self.folder, self.SCREENS), {"width": 1180, "height": 860, "maximized": False})

    def test_where_it_was_left(self):
        self.save(x=2100, y=40, width=1400, height=1000, maximized=False)
        self.assertEqual(
            desktop.placed(self.folder, self.SCREENS),
            {"x": 2100, "y": 40, "width": 1400, "height": 1000, "maximized": False},
        )

    def test_never_larger_than_its_screen(self):
        small = [(0, 0, 1366, 768)]
        self.assertEqual(desktop.placed(self.folder, small), {"width": 1180, "height": 720, "maximized": False})
        enlarged = [(0, 0, 1280, 720)]  # 1920 x 1080 at 150 %
        self.assertEqual(desktop.placed(self.folder, enlarged), {"width": 1180, "height": 672, "maximized": False})
        # left large on the second screen, which is a smaller one today
        self.save(x=1940, y=20, width=2400, height=1300, maximized=False)
        found = desktop.placed(self.folder, [(0, 0, 1920, 1080), (1920, 0, 1600, 900)])
        self.assertEqual(found, {"x": 1940, "y": 20, "width": 1600, "height": 852, "maximized": False})
        tiny = [(0, 0, 320, 480)]
        self.assertEqual(desktop.placed(self.folder, tiny)["width"], desktop.SMALLEST[0], "never under the smallest")

    def test_a_screen_unplugged_since(self):
        self.save(x=5000, y=40, width=1400, height=1000, maximized=True)
        found = desktop.placed(self.folder, self.SCREENS)
        self.assertNotIn("x", found)
        self.assertEqual((found["width"], found["maximized"]), (1400, True))
        self.assertNotIn("x", desktop.placed(self.folder, []), "the screens not known: in the middle")

    def test_nonsense_is_ignored(self):
        self.save(x="a", y=None, width=50, height=99999, maximized="yes")
        self.assertEqual(desktop.placed(self.folder, self.SCREENS), {"width": 1180, "height": 860, "maximized": False})
        (self.folder / desktop.PLACE).write_text("[1, 2", encoding="utf-8")
        self.assertEqual(desktop.placed(self.folder, self.SCREENS)["width"], 1180)

    def test_read_as_the_window_closes(self):
        class Box:
            def __init__(self, x, y, width, height):
                self.X, self.Y, self.Width, self.Height = x, y, width, height

        class Form:
            _scale = 1.5
            WindowState = "FormWindowState.Maximized"
            Bounds = Box(-12, -12, 3864, 2184)
            RestoreBounds = Box(150, 75, 1770, 1290)

        self.assertEqual(desktop.left(Form()), {"x": 100, "y": 50, "width": 1180, "height": 860, "maximized": True})
        Form.WindowState = "Normal"
        Form.Bounds = Box(300, 150, 1800, 1500)
        self.assertEqual(desktop.left(Form())["width"], 1200)
        self.assertIsNone(desktop.left(None), "no form (not Windows)")
        Form.Bounds = Box(0, 0, 30, 30)
        self.assertIsNone(desktop.left(Form()), "too small to be the window")

        class Handlers(list):  # pywebview's events: += adds a handler
            def __iadd__(self, handler):
                self.append(handler)
                return self

        window = SimpleNamespace(native=Form(), events=SimpleNamespace(closing=Handlers()))
        Form.Bounds = Box(300, 150, 1800, 1500)
        desktop.remember(window, self.folder)
        (closing,) = window.events.closing
        closing()
        saved = json.loads((self.folder / desktop.PLACE).read_text(encoding="utf-8"))
        self.assertEqual(saved, {"x": 200, "y": 100, "width": 1200, "height": 1000, "maximized": False})


class TitleBarTest(unittest.TestCase):
    """The window's title bar takes the page's look (desktop.follow_look)."""

    class Handlers(list):  # pywebview's events: += adds a handler
        def __iadd__(self, handler):
            self.append(handler)
            return self

    def window(self, answer):
        asked = []

        def evaluate_js(script):
            asked.append(script)
            if isinstance(answer, Exception):
                raise answer
            return answer

        handle = SimpleNamespace(ToInt64=lambda: 4242)
        events = SimpleNamespace(loaded=self.Handlers())
        window = SimpleNamespace(evaluate_js=evaluate_js, native=SimpleNamespace(Handle=handle), events=events)
        put = []
        desktop.follow_look(window, lambda handle, look: put.append((handle, look)))
        return window, asked, put

    def test_dark_page_dark_bar(self):
        for dark in (True, False):
            window, asked, put = self.window(dark)
            self.assertEqual(put, [], "nothing before a page has loaded")
            (loaded,) = window.events.loaded
            loaded()
            loaded()
            self.assertEqual(put, [(4242, dark)] * 2, "at every page")
            self.assertEqual(asked, [desktop.IS_DARK] * 2, "one question a page")

    def test_the_owners_choice_before_windows_own(self):
        self.assertIn("data-theme", desktop.IS_DARK)
        self.assertIn("prefers-color-scheme: dark", desktop.IS_DARK)
        self.assertLess(desktop.IS_DARK.index("data-theme"), desktop.IS_DARK.index("prefers-color-scheme"))

    def test_a_page_that_does_not_answer(self):
        for answer in (None, "dark", RuntimeError("the window is closing")):
            window, _, put = self.window(answer)
            window.events.loaded[0]()
            self.assertEqual(put, [])

    def test_no_bar_to_colour_elsewhere(self):
        if sys.platform != "win32":
            self.assertFalse(desktop.title_bar(4242, True))
        self.assertFalse(desktop.title_bar(0, True), "no window")


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

    def test_the_window_processes(self):
        """The processes the app started, at any depth (WebView2's browser, GPU and page processes), not others."""
        pairs = [(10, 1), (20, 10), (21, 20), (22, 20), (30, 21), (40, 2), (10, 10)]
        self.assertEqual(desktop.descendants(pairs, 10), {20, 21, 22, 30})
        self.assertEqual(desktop.descendants(pairs, 99), set())

    def test_the_data_folder(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:/Users/me/AppData/Local", "VAL_DESKTOP_HOME": ""}):
            self.assertEqual(desktop.data_folder(), Path("C:/Users/me/AppData/Local") / "VAL Tracker")
        with mock.patch.dict(os.environ, {"VAL_DESKTOP_HOME": "/tmp/x"}):
            self.assertEqual(desktop.data_folder(), Path("/tmp/x"))


if __name__ == "__main__":
    unittest.main()
