"""How busy the servers are (activity.py), the game's queue settings (queues.py) and the pages that show them, the
sensitivity converter, and the countdowns (the act's end, the daily store)."""

import json
import os
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from helpers import fixture, make_app

from valtracker import queues
from valtracker.activity import FEW, Activity, Collector

VISITOR = {"homepage.visitor": True}
#: 2026-11-04 12:00 UTC (21:00 in Japan)
NOON = int(datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp())


def match(number: int, started: int, queue: str = "competitive", cluster: str = "Tokyo", tier: int = 16, length=1800):
    return {
        "metadata": {
            "match_id": f"m{number}",
            "region": "ap",
            "cluster": cluster,
            "queue": {"id": queue},
            "started_at": datetime.fromtimestamp(started, UTC).isoformat().replace("+00:00", "Z"),
            "game_length_in_ms": length * 1000,
        },
        "players": [{"puuid": f"p{number}-{i}", "tier": {"id": tier}} for i in range(10)],
    }


class ActivityTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.now = NOON + 3600
        self.activity = Activity(Path(folder.name), clock=lambda: self.now)
        self.addCleanup(self.activity.close)

    def test_a_match_is_kept_once(self):
        self.assertEqual(self.activity.seen([match(1, NOON)]), 1)
        self.assertEqual(self.activity.seen([match(1, NOON)]), 0)
        self.assertEqual(self.activity.seen([{"metadata": {}}, "x", None]), 0)
        self.assertEqual(self.activity.count(), 1)

    def test_waits_between_one_players_matches(self):
        # 30 min match, then 4 min until the next; a 2 h break; a deathmatch in between of another queue
        listed = [
            match(1, NOON),
            match(2, NOON + 1800 + 240),
            match(3, NOON + 4 * 3600),
            match(4, NOON + 7200, "deathmatch"),
        ]
        self.activity.seen(listed, "me")
        rows = self.activity._connect().execute("SELECT before, after, seconds FROM gaps").fetchall()
        self.assertEqual(rows, [("m1", "m2", 240)])
        self.activity.seen(listed)  # without whose list it is: no waits
        self.assertEqual(self.activity._connect().execute("SELECT COUNT(*) FROM gaps").fetchone()[0], 1)

    def test_servers_by_hour_in_the_visitors_time(self):
        listed = [match(i, NOON + i * 60) for i in range(8)] + [match(100, NOON - 6 * 3600, cluster="Hong Kong")]
        self.activity.seen(listed)
        found = self.activity.servers("ap", 9)
        self.assertEqual([server["name"] for server in found["servers"]], ["Tokyo", "Hong Kong"])
        tokyo = found["servers"][0]
        self.assertEqual(tokyo["matches"], 8)
        self.assertEqual(tokyo["hours"][21], 100.0, "12:00 UTC is 21:00 in Japan")
        self.assertEqual(found["hour_now"], 22)
        self.assertEqual(tokyo["level"], "quiet", "none at 22:00")
        self.assertEqual(self.activity.servers("ap", 0)["servers"][0]["hours"][12], 100.0)
        self.assertEqual(self.activity.servers("eu", 0)["servers"], [])

    def test_busy_at_its_usual_hour(self):
        self.now = NOON + 600
        self.activity.seen([match(i, NOON - 86400 * (i % 3) + i) for i in range(9)])
        self.assertEqual(self.activity.servers("ap", 9)["servers"][0]["level"], "busy")

    def test_waits_need_a_few_samples(self):
        listed = []
        for i in range(FEW):
            start = NOON - 86400 * i
            listed += [match(2 * i, start, tier=25), match(2 * i + 1, start + 1800 + 120 + i)]
        self.activity.seen(listed, "me")
        waits = self.activity.servers("ap", 9)["waits"]
        self.assertEqual(
            {(item["queue"], item["band"]) for item in waits}, {("competitive", ""), ("competitive", "plat-diamond")}
        )
        self.assertEqual(waits[0]["samples"], FEW)
        self.assertEqual(waits[0]["seconds"], 122)

    def test_quiet_hours_go_round_midnight(self):
        from valtracker.activity import quiet_hours

        day = [5.0] * 24
        day[23] = day[0] = day[1] = 0.5
        self.assertEqual(quiet_hours(day), (23, 2))
        self.assertIsNone(quiet_hours([100 / 24] * 24), "no hour stands out")

    def test_old_rows_go(self):
        self.activity.seen([match(1, NOON - 40 * 86400)])
        self.assertEqual(self.activity.count(), 0, "older than KEEP_DAYS: dropped at once")

    def test_a_broken_file_does_not_break_pages(self):
        self.activity._db = mock.Mock()
        self.activity._db.execute.side_effect = __import__("sqlite3").OperationalError("disk I/O error")
        self.activity._db.executemany.side_effect = __import__("sqlite3").OperationalError("disk I/O error")
        self.assertEqual(self.activity.seen([match(1, NOON)]), 0)
        self.assertEqual(self.activity.servers("ap")["servers"], [])
        self.activity._db = None


class StreakTest(unittest.TestCase):
    def test_streak(self):
        from valtracker.views import _streak

        def rows(*results, standard=True):
            return [{"result": {"won": won}, "standard": standard} for won in results]

        self.assertEqual(_streak(rows(True, True, True, False)), {"won": True, "count": 3})
        self.assertEqual(_streak(rows(False, False)), {"won": False, "count": 2})
        self.assertIsNone(_streak(rows(True, False, True)), "one is not a streak")
        self.assertIsNone(_streak(rows(None, True, True)), "a draw ends it")
        self.assertIsNone(_streak(rows(True, True, standard=False)), "deathmatch and the like are left out")


class CollectorTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.activity = Activity(Path(folder.name), clock=lambda: NOON + 3600)
        self.addCleanup(self.activity.close)
        self.henrik = mock.Mock()
        self.henrik.key.return_value = "HDEV-x"
        self.henrik.matches_by_puuid.side_effect = lambda region, puuid: [match(1, NOON), match(2, NOON + 2000)]
        self.henrik.leaderboard.return_value = {
            "players": [{"puuid": "top", "hidden": False}, {"puuid": "", "hidden": True}]
        }

    def test_starts_from_a_leaderboard(self):
        collector = Collector(self.activity, self.henrik, 0)
        self.assertEqual(collector.once(), 2)
        self.assertEqual(self.henrik.matches_by_puuid.call_args.args[1], "top")
        self.assertIn("top", collector.looked)

    def test_then_the_players_it_has_seen_fewest_of(self):
        collector = Collector(self.activity, self.henrik, 0)
        self.activity.seen([match(5, NOON, tier=25)])
        self.activity.candidates.append(("ap", "low", 5))
        collector.looked.update(f"p5-{i}" for i in range(9))
        region, puuid = collector.pick()
        self.assertEqual((region, puuid), ("ap", "low"), "no low rank looked at yet")

    def test_now_and_then_a_region_not_looked_at_yet(self):
        collector = Collector(self.activity, self.henrik, 0)
        collector.regions = {"ap": 9, "kr": 3, "eu": 3, "na": 3, "latam": 3}
        self.activity.candidates.append(("ap", "someone", 16))
        with mock.patch("valtracker.activity.random.random", return_value=0.05):
            collector.once()
        self.assertEqual(self.henrik.leaderboard.call_args.args[0], "br", "the region looked at least")
        self.assertEqual(collector.regions["br"], 1)

    def test_does_nothing_without_a_key(self):
        self.henrik.key.return_value = ""
        self.assertEqual(Collector(self.activity, self.henrik, 0).once(), 0)
        self.henrik.matches_by_puuid.assert_not_called()

    def test_off_with_zero_seconds(self):
        collector = Collector(self.activity, self.henrik, 0)
        collector.start()
        self.assertFalse(collector._started)


CONFIG = json.loads(fixture("riot_pd.json"))["customgameconfigs"]


class QueuesTest(unittest.TestCase):
    def test_settings(self):
        found = queues.settings(CONFIG, {"ascent": {"displayName": "アセント", "listViewIcon": "x.png"}})
        self.assertEqual(
            [queue["id"] for queue in found["queues"]], ["competitive", "unrated"], "the game's order, no disabled"
        )
        competitive = found["queues"][0]
        self.assertEqual(
            [item["name"] for item in competitive["maps"]], ["Duality", "アセント"], "weight 0 is not played"
        )
        self.assertEqual(competitive["invalid"], [4])
        rules = found["competitive"]
        self.assertEqual(rules["ceilings"][14], 16)
        self.assertNotIn(0, rules["ceilings"])
        self.assertEqual((rules["high_tier"], rules["high_size"], rules["bypass"]), (24, 3, True))

    def test_check(self):
        rules = queues.settings(CONFIG, {})["competitive"]
        self.assertEqual(queues.check([14, 16], rules)["why"], "gap_ok")
        self.assertEqual(queues.check([14, 17], rules)["why"], "gap")
        self.assertEqual(queues.check([14, 17], rules)["values"], {"low": 14, "high": 17, "top": 16})
        self.assertEqual(queues.check([3, 3, 3, 3], rules)["why"], "size", "4 is not allowed")
        self.assertEqual(queues.check([24, 24, 24, 24, 24][:4], rules)["why"], "size")
        self.assertEqual(queues.check([24, 25, 24], rules)["ok"], True)
        self.assertEqual(queues.check([24, 25, 24, 26, 3], rules)["why"], "full")
        self.assertEqual(queues.check([0, 20], rules)["why"], "unranked")
        self.assertEqual(queues.check([20], rules)["ok"], None)
        strict = rules | {"bypass": False, "invalid": []}
        self.assertEqual(queues.check([24, 24, 24, 24], strict)["why"], "high")
        self.assertEqual(queues.check([3, 3, 3, 3, 27], strict)["why"], "high")


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200, **kwargs) -> str:
        answer = self.client.get(path, **kwargs)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def sign_in_to_riot(self):
        page = self.client.get("/settings").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.client.post("/settings", data={"csrf": token, "do": "riot", "riot": "ssid-1"})

    def test_tools_lead_to_the_new_pages(self):
        text = self.page("/tools", environ_base=VISITOR)
        for path in ("/servers", "/queues", "/sensitivity"):
            self.assertIn(f'href="{path}"', text)
        self.assertIn('id="china"', text)
        self.assertIn('href="/tools#china"', self.page("/"))

    def test_servers_from_the_matches_seen(self):
        text = self.page("/servers")
        self.assertIn("まだ集まっていません", text)
        self.assertIn("Riot は接続人数も待ち時間も公開していません", text)
        self.page("/p/Player/JP1?full=1")  # a player's page keeps their matches
        text = self.page("/servers?region=ap")
        self.assertIn("東京", text)
        self.assertIn('class="hours"', text)
        self.assertIn("日本時間", text)
        self.client.set_cookie("lang", "en-GB")
        self.assertIn("Tokyo", self.page("/servers?region=ap"))
        self.assertIn("UTC", self.page("/servers?region=ap"))

    def test_player_rows_show_their_server_and_today(self):
        text = self.page("/p/Player/JP1?full=1")
        self.assertIn("東京", text)
        self.assertIn("ACT III", text, "the act and the time left in it")

    def test_a_pasted_profile_address_finds_the_player(self):
        for pasted in (
            "https://tracker.gg/valorant/profile/riot/Tester%23JP1/overview",
            "https://haruxera.duckdns.org/val/p/Tester/JP1?mode=competitive",
        ):
            answer = self.client.get("/search", query_string={"q": pasted})
            self.assertEqual(answer.status_code, 303, pasted)
            self.assertTrue(answer.headers["Location"].endswith("/p/Tester/JP1"), pasted)

    def test_today_on_a_players_page(self):
        import valtracker.views as views

        class Then(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 4, 23, 30, tzinfo=tz)

        with mock.patch.object(views, "datetime", Then):
            text = self.page("/p/Tester/JP1?full=1")
        today = text.split("今日</dt>")[1].split("</dd>")[0]
        self.assertIn("2 試合", today)
        self.assertRegex(today, r"\d勝 \d敗")
        self.assertIn(" RR", today, "the RR of today's matches")

    def test_player_page_extras(self):
        text = self.page("/p/Tester/JP1?full=1")
        self.assertIn('data-copy="Tester#JP1"', text, "the Riot ID to copy for a friend request")
        self.assertIn('<meta name="description" content="Tester#JP1 のランク', text)
        self.assertIn('<meta property="og:description" content="Tester#JP1 のランク', text)

    def test_top_page_leads_a_first_visit_on(self):
        text = self.page("/")
        for path in ("/leaderboard", "/servers", "/queues", "/crosshair", "/sensitivity"):
            self.assertIn(f'href="{path}"', text.split('class="quick"')[1].split("</nav>")[0])

    def test_agents_by_role(self):
        text = self.page("/agents")
        self.assertIn("ジェット", text)
        self.assertIn("ソーヴァ", text)
        self.assertIn('href="/agents?role=dbe8757e-9e92-4ed4-b39f-9dfc589691d4"', text)
        text = self.page("/agents?role=dbe8757e-9e92-4ed4-b39f-9dfc589691d4")
        grid = text.split('class="agent-grid"')[1]
        self.assertIn("ジェット", grid)
        self.assertNotIn("ソーヴァ", grid)
        self.assertIn("ソーヴァ", self.page("/agents?role=nothing").split('class="agent-grid"')[1])

    def test_queues_for_visitors_without_riot(self):
        text = self.page("/queues", 200, environ_base=VISITOR)
        self.assertIn("いまはゲームの設定を読めません", text)
        self.assertNotIn("/settings", text.split("<main")[1])

    def test_queues_and_party_check(self):
        self.sign_in_to_riot()
        text = self.page("/queues", environ_base=VISITOR)
        self.assertIn("アセント", text)
        self.assertIn("Duality", text)
        competitive = text.split('class="pool"')[1]
        self.assertNotIn("Bonsai", competitive, "weight 0: not played in competitive")
        self.assertIn("Bonsai", text.split('class="pool"')[2])
        self.assertIn("4 人は不可", text)
        text = self.page("/queues?rank=14&rank=17&rank=", environ_base=VISITOR)
        self.assertIn('class="verdict no"', text)
        text = self.page("/queues?rank=14&rank=16", environ_base=VISITOR)
        self.assertIn('class="verdict ok"', text)
        text = self.page("/queues?rank=24&rank=24&rank=24&rank=25&rank=x", environ_base=VISITOR)
        self.assertIn("4 人のパーティー", text)
        calls = self.app.riot.pd_calls()
        self.page("/queues", environ_base=VISITOR)
        self.assertEqual(self.app.riot.pd_calls(), calls, "kept for a while")

    def test_sensitivity(self):
        self.assertNotIn("同じ振り向き", self.page("/sensitivity"))
        text = self.page("/sensitivity?game=valorant&sens=0.35&dpi=800&new_dpi=1600")
        self.assertIn("46.7 cm", text)  # 360 * 2.54 / (800 * 0.35 * 0.07)
        self.assertIn("1.1136", text, "CS2: 0.35 * 0.07 / 0.022")
        self.assertIn("0.175", text, "twice the DPI, half the sensitivity")
        self.assertIn("280.0", text, "eDPI")
        text = self.page("/sensitivity?game=cs2&sens=1.1136&dpi=800")
        self.assertIn("0.35", text)
        self.page("/sensitivity?sens=abc&dpi=800", 400)
        self.page("/sensitivity?sens=1&dpi=10", 400)
        self.assertIn("VALORANT", self.page("/sensitivity?game=nothing"))

    def test_shop_tells_when_the_daily_store_changes(self):
        text = self.page("/shop", environ_base=VISITOR)
        self.assertIn("毎日のスキンの次の入れ替わり", text)
        self.assertIn("9:00", text)

    def test_leaderboard_tells_when_the_act_ends(self):
        self.assertIn("ACT III の終わりまであと", self.page("/leaderboard"))


if __name__ == "__main__":
    unittest.main()
