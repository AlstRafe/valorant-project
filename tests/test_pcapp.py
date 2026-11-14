"""What only the PC app does: the friends (friends.py), the checkup (checkup.py), the match-found alert and the
start with Windows (pc.py), and the Riot Client's part of them (localclient.py)."""

import base64
import json
import re
import struct
import tempfile
import threading
import time
import unittest
import wave
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from helpers import PUUID, make_app
from test_desktop import FRIEND, PARTY, FakeClient, event, presence

from valtracker import checkup, friends, pc
from valtracker.localclient import PRESENCES, LocalClient, moment, read_lockfile
from valtracker.settings import Settings

ASCENT = "/Game/Maps/Ascent/Ascent"
OTHER = "0e0e0e0e-1111-4222-8333-555555555555"
THIRD = "0f0f0f0f-1111-4222-8333-666666666666"
CARD = "c0000000-0000-4000-8000-00000000000a"


def packed(data: dict) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


def playing(puuid: str, nested: bool = True, **fields) -> dict:
    """A friend's VALORANT presence, in either shape the game has used."""
    match = {
        key: fields[key] for key in ("sessionLoopState", "queueId", "matchMap", "provisioningFlow") if key in fields
    }
    party = {
        key: fields[key]
        for key in ("partyId", "partyState", "partySize", "maxPartySize", "partyAccessibility")
        if key in fields
    }
    player = {key: fields[key] for key in ("competitiveTier", "accountLevel", "playerCardId") if key in fields}
    rest = {key: value for key, value in fields.items() if key not in {*match, *party, *player, "state"}}
    data = (
        {"isValid": True, "matchPresenceData": match, "partyPresenceData": party, "playerPresenceData": player, **rest}
        if nested
        else {**match, **party, **player, **rest}
    )
    return {"puuid": puuid, "product": "valorant", "state": fields.get("state", "chat"), "private": packed(data)}


LOOK = {
    "tiers": {15: {"tierName": "PLATINUM 1", "smallIcon": "https://media.valorant-api.com/t/15.png"}},
    "maps": {"ascent": {"displayName": "アセント"}},
    "queue_name": {"competitive": "コンペティティブ", "unrated": "アンレート"}.get,
}


def said(moment_: datetime) -> dict:
    return {"iso": moment_.isoformat(), "text": f"{moment_.month}/{moment_.day}"}


class FriendsTest(unittest.TestCase):
    def rows(self, friends_, presences, me=PUUID):
        return friends.rows({"friends": friends_, "presences": presences, "me": me}, LOOK, 1_800_000_000.0, said)

    def test_a_friend_in_a_match_in_either_shape(self):
        listed = [{"puuid": FRIEND, "game_name": "Tomo", "game_tag": "JP2"}]
        for nested in (True, False):
            with self.subTest(nested=nested):
                there = playing(
                    FRIEND,
                    nested,
                    sessionLoopState="INGAME",
                    queueId="competitive",
                    matchMap=ASCENT,
                    partyOwnerMatchScoreAllyTeam=7,
                    partyOwnerMatchScoreEnemyTeam=5,
                    partyId=PARTY,
                    partySize=3,
                    maxPartySize=5,
                    partyAccessibility="CLOSED",
                    competitiveTier=15,
                    accountLevel=120,
                    playerCardId=CARD,
                )
                row = self.rows(listed, [there])["playing"][0]
                self.assertEqual((row["name"], row["tag"], row["where"]), ("Tomo", "JP2", "ingame"))
                self.assertEqual((row["queue"], row["map"], row["score"]), ("コンペティティブ", "アセント", "7–5"))
                self.assertEqual((row["party"], row["open"], row["level"]), (3, False, 120))
                self.assertEqual(row["rank"]["name"], "Platinum 1")
                self.assertTrue(row["card"].endswith(f"/playercards/{CARD}/smallart.png"))

    def test_where_a_presence_says_the_player_is(self):
        for fields, wanted in (
            ({"sessionLoopState": "PREGAME"}, "pregame"),
            ({"sessionLoopState": "INGAME", "provisioningFlow": "ShootingRange"}, "range"),
            ({"sessionLoopState": "MENUS", "partyState": "MATCHMAKING"}, "queue"),
            ({"sessionLoopState": "MENUS", "partyState": "MATCHMADE_GAME_STARTING"}, "found"),
            ({"sessionLoopState": "MENUS", "partyState": "CUSTOM_GAME_SETUP"}, "custom"),
            ({"sessionLoopState": "MENUS", "isIdle": True}, "away"),
            ({"sessionLoopState": "MENUS"}, "menus"),
        ):
            with self.subTest(wanted=wanted):
                self.assertEqual(friends.place(fields, False), wanted)
        self.assertEqual(friends.place({"sessionLoopState": "MENUS"}, True), "away")

    def test_the_menus_do_not_name_a_queue_or_a_map(self):
        listed = [{"puuid": FRIEND, "game_name": "Tomo", "game_tag": "JP2"}]
        there = playing(FRIEND, sessionLoopState="MENUS", queueId="unrated", matchMap=ASCENT, partySize=1)
        row = self.rows(listed, [there])["playing"][0]
        self.assertEqual((row["queue"], row["map"], row["score"], row["party"]), ("", "", "", 0))

    def test_the_owners_party_first_then_matches_then_the_rest(self):
        listed = [
            {"puuid": FRIEND, "game_name": "Aki", "game_tag": "1"},
            {"puuid": OTHER, "game_name": "Bo", "game_tag": "2"},
            {"puuid": THIRD, "game_name": "Chi", "game_tag": "3"},
        ]
        presences = [
            playing(PUUID, sessionLoopState="MENUS", partyId=PARTY),
            playing(FRIEND, sessionLoopState="MENUS"),
            playing(OTHER, sessionLoopState="INGAME"),
            playing(THIRD, sessionLoopState="MENUS", partyId=PARTY, partySize=2),
        ]
        found = self.rows(listed, presences)
        self.assertEqual([row["name"] for row in found["playing"]], ["Chi", "Bo", "Aki"])
        self.assertEqual([row["mine"] for row in found["playing"]], [True, False, False])

    def test_other_games_and_the_ones_away(self):
        listed = [
            {"puuid": FRIEND, "game_name": "Lol", "game_tag": "1"},
            {"puuid": OTHER, "game_name": "Gone", "game_tag": "2", "last_online_ts": 1_799_990_000_000},
            {"puuid": THIRD, "game_name": "Never", "game_tag": "3", "last_online_ts": 0},
            {"puuid": PUUID, "game_name": "Owner", "game_tag": "JP1"},
            {"puuid": "", "game_name": "Nobody", "game_tag": "0"},
        ]
        found = self.rows(listed, [{"puuid": FRIEND, "product": "league_of_legends", "state": "away"}])
        self.assertEqual(found["playing"], [])
        self.assertEqual(
            found["online"][0] | {"note": ""},
            {"name": "Lol", "tag": "1", "note": "", "product": "League of Legends", "away": True},
        )
        self.assertEqual([row["name"] for row in found["offline"]], ["Gone", "Never"])
        self.assertEqual(found["offline"][0]["last"]["iso"], datetime.fromtimestamp(1_799_990_000, UTC).isoformat())
        self.assertIsNone(found["offline"][1]["last"])
        self.assertEqual(found["total"], 3)  # the owner and the entry without an id are not friends


class ClientTest(unittest.TestCase):
    """The Riot Client's part: the friends, the checkup's look, and the listeners of a match found."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lockfile = Path(folder.name) / "lockfile"
        self.lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.api = FakeClient()
        self.now = 1_800_000_000.0
        self.client = LocalClient(self.lockfile, Path(folder.name) / "none.log", ask=self.api, clock=lambda: self.now)
        self.door = read_lockfile(self.lockfile)

    def listening(self):
        with self.client._lock:
            self.client._listening = True
        self.client._look_again(self.door)

    def test_the_moments_of_a_presence(self):
        for loop, party, wanted in (
            ("MENUS", "DEFAULT", "menus"),
            ("MENUS", "MATCHMAKING", "queue"),
            ("MENUS", "MATCHMADE_GAME_STARTING", "found"),
            ("PREGAME", "MATCHMADE_GAME_STARTING", "pregame"),
            ("INGAME", "DEFAULT", "ingame"),
            ("", "", ""),
        ):
            self.assertEqual(moment({"loop": loop, "party_state": party}), wanted)
        self.assertEqual(moment(None), "")

    def test_the_friends_are_two_local_reads_kept_a_few_seconds(self):
        self.client.signed_in()
        del self.api.asked[:]
        found = self.client.friends()
        self.assertEqual(self.api.asked, ["/chat/v4/friends", "/chat/v4/presences"])
        self.assertEqual((found["friends"][0]["game_name"], found["me"], len(found["presences"])), ("Tomo", PUUID, 2))
        self.client.friends()
        self.assertEqual(len(self.api.asked), 2)  # kept
        self.now += 6
        self.client.friends()
        self.assertEqual(len(self.api.asked), 4)

    def test_a_friends_presence_event_reads_the_friends_again(self):
        self.listening()
        self.client.friends()
        asked = len(self.api.asked)
        self.client.handle(self.door, event(PRESENCES, {"presences": [presence("PREGAME", puuid=FRIEND)]}))
        self.client.friends()
        self.assertEqual(len(self.api.asked), asked + 2)
        self.assertEqual(self.client.heard["presences"], 1)

    def test_no_friends_while_the_client_is_closed(self):
        self.lockfile.unlink()
        self.assertIsNone(self.client.friends())

    def test_a_match_found_is_told_once(self):
        told = []
        self.client.listen(told.append)
        self.listening()  # the menus: the app only now learns where the game is
        self.assertEqual(told, [])
        for loop, party in (
            ("MENUS", "MATCHMAKING"),
            ("MENUS", "MATCHMADE_GAME_STARTING"),
            ("PREGAME", "MATCHMADE_GAME_STARTING"),
            ("INGAME", "DEFAULT"),
            ("MENUS", "DEFAULT"),
        ):
            self.client.handle(self.door, event(PRESENCES, {"presences": [presence(loop, party)]}))
        self.assertEqual(told, ["found"])

    def test_agent_select_is_told_when_the_count_was_missed(self):
        told = []
        self.client.listen(told.append)
        self.listening()
        self.client.handle(self.door, event(PRESENCES, {"presences": [presence("MENUS", "MATCHMAKING")]}))
        self.client.handle(self.door, event(PRESENCES, {"presences": [presence("PREGAME", "DEFAULT")]}))
        self.assertEqual(told, ["pregame"])

    def test_a_match_already_on_when_the_app_starts_is_not_told(self):
        told = []
        self.client.listen(told.append)
        self.api.presences = [presence("PREGAME", "MATCHMADE_GAME_STARTING")]
        self.listening()
        self.assertEqual(told, [])

    def test_a_listener_that_fails_does_not_end_the_listening(self):
        told = []
        self.client.listen(lambda moment_: 1 / 0)
        self.client.listen(told.append)
        self.listening()
        self.client.handle(self.door, event(PRESENCES, {"presences": [presence("MENUS", "MATCHMADE_GAME_STARTING")]}))
        self.assertEqual(told, ["found"])

    def test_the_checkups_look_holds_nothing_personal(self):
        self.listening()
        seen = self.client.examine()
        self.assertEqual(seen["token"], {"status": 200, "access": True, "entitlements": True, "subject": True})
        self.assertEqual(seen["chat"], {"status": 200, "name": True, "mine": True, "state": "connected"})
        self.assertEqual((seen["sessions"]["running"], seen["sessions"]["shard"]), (True, "ap"))
        self.assertEqual(seen["sessions"]["products"], ["riot_client", "valorant"])
        presence_ = seen["presence"]
        self.assertEqual(
            (presence_["mine"], presence_["shape"], presence_["loop"], presence_["count"]), (True, "nested", "MENUS", 2)
        )
        self.assertTrue(seen["listening"])
        text = json.dumps(seen)
        for secret in (PUUID, PARTY, "Owner", "JP1", "secret"):
            self.assertNotIn(secret, text)

    def test_the_checkups_look_at_a_closed_client(self):
        self.lockfile.unlink()
        self.assertEqual(self.client.examine()["lockfile"], False)
        self.assertNotIn("token", self.client.examine())


class FakeWindows:
    """pc.Windows, made up: the window in front, and what was asked."""

    def __init__(self, front=(7, "Browser", "Chrome_WidgetWin_1")):
        self.now, self.did = front, []

    def front(self):
        return self.now

    def flash(self, handle, on):
        self.did.append(("flash", handle, on))

    def play(self, path):
        self.did.append(("play", Path(path).name))


class AlertTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.settings = Settings(self.folder)
        self.windows = FakeWindows()
        self.alert = pc.Alert(self.settings, self.folder, lambda: 42, self.windows, wait=lambda seconds: True)

    def settle(self):
        for thread in list(threading.enumerate()):
            if thread.name == "alert":
                thread.join(5)

    def test_another_program_in_front_lights_the_button(self):
        self.assertEqual(self.alert("found"), {"flash": True, "sound": False})
        self.settle()
        self.assertEqual(self.windows.did, [("flash", 42, True), ("flash", 42, False)])  # lit, then put out

    def test_nothing_while_the_game_is_in_front(self):
        for title, kind in (("VALORANT  ", "VALORANTUnrealWindow"), ("VALORANT", "Whatever")):
            self.windows.now = (9, title, kind)
            self.settings.save(alert_sound=True)
            self.assertEqual(self.alert("found"), {"flash": False, "sound": False})
        self.assertEqual(self.windows.did, [])

    def test_the_app_in_front_needs_no_light(self):
        self.windows.now = (42, "VAL Tracker", "WindowsForms10.Window.8")
        self.assertEqual(self.alert("found"), {"flash": False, "sound": False})

    def test_the_sound_only_when_turned_on(self):
        self.settings.save(alert_flash=False, alert_sound=True)
        self.assertEqual(self.alert("found"), {"flash": False, "sound": True})
        self.assertEqual(self.windows.did, [("play", pc.SOUND)])
        with (self.folder / pc.SOUND).open("rb") as file:
            self.assertEqual(file.read(4), b"RIFF")
        self.assertLess((self.folder / pc.SOUND).stat().st_size, 80_000)

    def test_the_sound_rings_out_instead_of_stopping_short(self):
        """It begins and ends in silence, and what is heard fades: nothing is cut while it still rings."""
        (self.folder / pc.OLD_SOUNDS[0]).write_bytes(b"the sound as it was")
        path = pc.chime(self.folder / pc.SOUND)
        with wave.open(str(path), "rb") as file:
            rate, count = file.getframerate(), file.getnframes()
            heard = [abs(value) for (value,) in struct.iter_unpack("<h", file.readframes(count))]
        loudest = max(heard)
        self.assertGreater(count / rate, 1.2, "long enough for both notes to ring out")
        self.assertLess(loudest, 0.5 * 32767, "quiet beside a game")
        self.assertGreater(loudest, 0.15 * 32767, "and still heard")
        self.assertEqual(max(heard[: int(rate * 0.03)]), 0, "silence before the first note")
        self.assertEqual(max(heard[-int(rate * 0.1) :]), 0, "silence at the end")
        last = heard[-int(rate * 0.2) : -int(rate * 0.1)]
        self.assertLess(max(last), loudest / 50, "the last tenth of a second of the notes is all but silent")
        steps = max(abs(a - b) for a, b in zip(heard, heard[1:], strict=False))
        self.assertLess(steps, loudest * 0.6, "no jump from one moment to the next (a click)")
        self.assertFalse((self.folder / pc.OLD_SOUNDS[0]).exists(), "the sound it had before is removed")
        made = path.stat().st_mtime_ns
        self.assertEqual(pc.chime(path).stat().st_mtime_ns, made, "made once")

    def test_the_settings_button_tries_it_whatever_is_in_front(self):
        self.windows.now = (42, "VAL Tracker", "WindowsForms10.Window.8")
        self.assertEqual(self.alert("found", always=True)["flash"], True)
        self.settle()

    def test_nothing_without_windows_or_a_window(self):
        self.assertEqual(pc.Alert(self.settings, self.folder, lambda: 42, None)(), {"flash": False, "sound": False})
        self.assertEqual(pc.Alert(self.settings, self.folder, lambda: 0, self.windows)()["flash"], False)

    def test_trouble_with_windows_is_swallowed(self):
        self.windows.flash = mock.Mock(side_effect=OSError("no"))
        with mock.patch("builtins.print"):
            self.assertEqual(self.alert("found"), {"flash": False, "sound": False})

    def test_what_counts_as_the_game(self):
        self.assertTrue(pc.is_game("VALORANT  ", "VALORANTUnrealWindow"))
        self.assertTrue(pc.is_game("valorant", ""))
        self.assertFalse(pc.is_game("VAL Tracker", "WindowsForms10.Window.8"))
        self.assertFalse(pc.is_game("VALORANT - Google Chrome", "Chrome_WidgetWin_1"))


class FakeRegistry:
    """winreg, made up: one key's values."""

    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self):
        self.values, self.broken = {}, False

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802 - winreg's names
        if self.broken:
            raise OSError("denied")
        return mock.MagicMock()

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class AutostartTest(unittest.TestCase):
    def test_on_and_off(self):
        registry = FakeRegistry()
        start = pc.Autostart(r'"C:\Apps\VALTracker.exe" --minimized', registry)
        self.assertTrue(start.possible)
        self.assertFalse(start.on())
        self.assertTrue(start.set(True))
        self.assertEqual(registry.values, {pc.RUN_NAME: r'"C:\Apps\VALTracker.exe" --minimized'})
        self.assertTrue(start.on())
        self.assertTrue(start.set(False))
        self.assertTrue(start.set(False))  # already off
        self.assertEqual(registry.values, {})

    def test_another_copy_of_the_app_is_not_this_one(self):
        registry = FakeRegistry()
        registry.values[pc.RUN_NAME] = r'"D:\Old\VALTracker.exe" --minimized'
        self.assertFalse(pc.Autostart(r'"C:\Apps\VALTracker.exe" --minimized', registry).on())

    def test_a_registry_that_refuses(self):
        registry = FakeRegistry()
        registry.broken = True
        start = pc.Autostart("x", registry)
        self.assertFalse(start.on())
        self.assertFalse(start.set(True))

    def test_from_the_source_there_is_nothing_to_start(self):
        start = pc.Autostart.here()
        self.assertFalse(start.possible)
        self.assertFalse(start.on())
        self.assertFalse(start.set(True))
        self.assertTrue(start.set(False))


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"VAL_COLLECT_SECONDS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lockfile = Path(folder.name) / "lockfile"
        self.lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.api = FakeClient()
        self.local = LocalClient(self.lockfile, Path(folder.name) / "none.log", ask=self.api)
        self.app, _, _ = make_app(local=self.local)
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def token(self, path: str) -> str:
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def test_the_friends_page(self):
        self.api.friends.append(
            {"puuid": OTHER, "game_name": "Gone", "game_tag": "JP3", "last_online_ts": int((time.time() - 3600) * 1000)}
        )
        self.api.presences[1] = playing(FRIEND, sessionLoopState="INGAME", queueId="competitive", matchMap=ASCENT)
        answer = self.client.get("/friends")
        text = answer.get_data(as_text=True)
        self.assertEqual(answer.status_code, 200)
        self.assertIn('<a href="/p/Tomo/JP2">Tomo<span class="tag">#JP2</span></a>', text)
        self.assertIn("対戦中 · コンペティティブ", text)
        self.assertIn("最終オンライン", text)
        self.assertIn('data-refresh="10"', text)
        self.assertIn('<a href="/friends" aria-current="true">フレンド</a>', text)

    def test_the_friends_page_while_the_client_is_closed(self):
        self.lockfile.unlink()
        text = self.client.get("/friends").get_data(as_text=True)
        self.assertIn("Riot クライアントが起動していません", text)
        self.assertIn('href="/checkup"', text)

    def test_the_site_has_neither_page(self):
        site, _, _ = make_app()
        self.addCleanup(site._test_folder.cleanup)
        client = site.test_client()
        for path in ("/friends", "/checkup", "/friends/count"):
            self.assertEqual(client.get(path).status_code, 404)
        self.assertNotIn("/friends", client.get("/live").get_data(as_text=True))
        tools = client.get("/tools").get_data(as_text=True)
        self.assertNotIn("/friends", tools)
        self.assertNotIn("/checkup", tools)

    def test_the_tools_page_has_the_apps_own_pages(self):
        text = self.client.get("/tools").get_data(as_text=True)
        self.assertIn('href="/friends"', text)
        self.assertIn('href="/checkup"', text)
        self.assertIn("<strong>診断</strong><small>動作チェックとレポート</small>", text)

    def test_the_top_page_counts_the_friends_who_are_on(self):
        text = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-friends="/friends/count"', text, "asked once the page is drawn")
        self.assertIn('data-playing="{count} 人がプレイ中" data-online="オンライン {count} 人"', text)
        self.api.friends.append({"puuid": OTHER, "game_name": "Gone", "game_tag": "JP3"})
        self.api.presences[1] = playing(FRIEND, sessionLoopState="INGAME", queueId="competitive", matchMap=ASCENT)
        answer = self.client.get("/friends/count")
        self.assertEqual(answer.headers["Cache-Control"], "no-store")
        counted = answer.get_json()
        self.assertTrue(counted["open"])
        self.assertEqual(counted["playing"], 1, "the friend in a match; the one who is off is not counted")
        self.assertGreaterEqual(counted["online"], counted["playing"])
        self.lockfile.unlink()
        self.assertEqual(self.client.get("/friends/count").get_json(), {"open": False})

    def test_the_checkup_reads_the_client_and_asks_no_server_until_asked(self):
        before = len(self.app.riot.asked)
        text = self.client.get("/checkup").get_data(as_text=True)
        self.assertEqual(len(self.app.riot.asked), before)
        self.assertIn("サインイン済み", text)
        self.assertIn("メニュー（現行形式）", text)
        self.assertIn("presence: ok 200 count=2 mine=1 shape=nested loop=MENUS", text)
        self.assertNotIn("[riot]", text)
        for secret in (PUUID, PARTY, "Owner", "JP1"):
            self.assertNotIn(secret, text.split('<pre class="report"')[1])

    def test_the_whole_checkup(self):
        token = self.token("/checkup")
        with mock.patch.object(checkup, "_site", return_value={"commit": "a" * 40, "time": 1}):
            text = self.client.post("/checkup", data={"csrf": token}).get_data(as_text=True)
        report = text.split('<pre class="report"')[1].split("</pre>")[0]
        for line in (
            "[riot]",
            "live: ok",
            "collection: ok",
            "shop: ok",
            "contracts: ok",
            "agents: ok",
            "history: ",
            "rr: ",
            "content: ok",
            "henrik: ok",
            "site: ok",
            "version=release-13.09-shipping-7-4012345",
        ):
            self.assertIn(line, report)
        self.assertNotIn(PUUID, report)

    def test_what_a_server_refused_is_said_without_ids(self):
        self.app.riot.broken.add("wallet")
        token = self.token("/checkup")
        with mock.patch.object(checkup, "_site", side_effect=OSError("down")):
            text = self.client.post("/checkup", data={"csrf": token}).get_data(as_text=True)
        self.assertIn("site: bad OSError", text)
        self.assertEqual(
            checkup.scrub(f"HTTP 404 /store/v1/wallet/{PUUID} " + "x" * 50 + "." + "y" * 44),
            "HTTP 404 /store/v1/wallet/<id> <…>.<…>",
        )

    def test_the_apps_own_settings(self):
        text = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('name="alert_flash" checked', text)
        self.assertIn('name="alert_sound">', text)
        self.assertIn('name="autostart" disabled', text)  # from the source: nothing to start
        token = self.token("/settings")
        text = self.client.post("/settings", data={"csrf": token, "do": "app", "alert_sound": "on"}).get_data(
            as_text=True
        )
        self.assertIn("設定を保存しました。", text)
        self.assertEqual(pc.chosen(self.app.config["VAL_SETTINGS"]), {"alert_flash": False, "alert_sound": True})
        self.assertIn('name="alert_sound" checked', text)

    def test_the_start_with_windows_is_set_from_the_settings(self):
        registry = FakeRegistry()
        self.app.config["VAL_AUTOSTART"] = pc.Autostart('"C:\\Apps\\VALTracker.exe" --minimized', registry)
        token = self.token("/settings")
        text = self.client.post(
            "/settings", data={"csrf": token, "do": "app", "alert_flash": "on", "autostart": "on"}
        ).get_data(as_text=True)
        self.assertIn('name="autostart" checked', text)
        self.assertIn(pc.RUN_NAME, registry.values)
        self.client.post("/settings", data={"csrf": token, "do": "app"})
        self.assertEqual(registry.values, {})

    def test_trying_the_alert(self):
        windows = FakeWindows((42, "VAL Tracker", "WindowsForms10.Window.8"))
        self.app.config["VAL_ALERT"] = pc.Alert(
            self.app.config["VAL_SETTINGS"],
            Path(self.app.instance_path),
            lambda: 42,
            windows,
            wait=lambda seconds: True,
        )
        token = self.token("/settings")
        text = self.client.post(
            "/settings", data={"csrf": token, "do": "alert_test", "alert_flash": "on", "alert_sound": "on"}
        ).get_data(as_text=True)
        self.assertIn("通知をテストしました", text)
        self.assertIn(("flash", 42, True), windows.did)
        self.assertIn(("play", pc.SOUND), windows.did)

    def test_the_site_ignores_the_apps_settings(self):
        site, _, _ = make_app()
        self.addCleanup(site._test_folder.cleanup)
        client = site.test_client()
        page = client.get("/settings").get_data(as_text=True)
        self.assertNotIn("alert_flash", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        client.post("/settings", data={"csrf": token, "do": "app", "alert_sound": "on"})
        self.assertNotIn("alert_sound", site.config["VAL_SETTINGS"].load())


if __name__ == "__main__":
    unittest.main()
