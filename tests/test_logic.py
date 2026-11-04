import json
import unittest
from datetime import datetime

from helpers import FakeFetch, fixture

from valtracker import paths, stats
from valtracker.cache import Budget, TTLCache
from valtracker.henrik import Henrik, HenrikError, split_riot_id
from valtracker.hosting import site


class RiotIdTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(split_riot_id("Tester#JP1"), ("Tester", "JP1"))
        self.assertEqual(split_riot_id(" 名前 ＃ 日本 "), ("名前", "日本"))
        self.assertIsNone(split_riot_id("a#b#KR1"))
        self.assertIsNone(split_riot_id("Tester"))
        self.assertIsNone(split_riot_id("#JP1"))
        self.assertIsNone(split_riot_id("Tester#J"))
        self.assertIsNone(split_riot_id("../x#JP1"))


class HenrikTest(unittest.TestCase):
    def setUp(self):
        self.fetch = FakeFetch()
        self.henrik = Henrik(lambda: "key", self.fetch)

    def test_errors(self):
        for status, kind in ((401, "badkey"), (403, "badkey"), (404, "notfound"), (429, "busy"), (500, "down")):
            with self.subTest(status=status):
                self.fetch.henrik_status = status
                henrik = Henrik(lambda: "key", self.fetch)
                with self.assertRaises(HenrikError) as caught:
                    henrik.account("A", "B")
                self.assertEqual(caught.exception.kind, kind)

    def test_no_answer(self):
        self.fetch.fail = 0
        with self.assertRaises(HenrikError) as caught:
            self.henrik.account("A", "B")
        self.assertEqual(caught.exception.kind, "down")

    def test_budget(self):
        henrik = Henrik(lambda: "key", self.fetch, Budget(1, 60))
        henrik.account("A", "B")
        with self.assertRaises(HenrikError) as caught:
            henrik.account("C", "D")
        self.assertEqual(caught.exception.kind, "busy")
        henrik.account("a", "b")  # kept: no call

    def test_names_are_quoted(self):
        self.henrik.account("名前 x", "JP1")
        self.assertIn("/v2/account/%E5%90%8D%E5%89%8D%20x/JP1", self.fetch.asked[0][0])

    def test_unknown_region(self):
        self.henrik.mmr("zz", "A", "B")
        self.assertIn("/v3/mmr/ap/pc/A/B", self.fetch.asked[0][0])


class CacheTest(unittest.TestCase):
    def test_expiry_and_size(self):
        now = [0.0]
        cache = TTLCache(2, clock=lambda: now[0])
        cache.put("a", 1, 10)
        cache.put("b", 2, 10)
        cache.put("c", 3, 10)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        now[0] = 11
        self.assertIsNone(cache.get("b"))


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.match = json.loads(fixture("henrik_matches.json"))["data"][0]

    def test_line(self):
        me = stats.find_player(self.match, "puuid-me")
        line = stats.line(me, stats.rounds_played(self.match))
        self.assertEqual(stats.rounds_played(self.match), 22)
        self.assertEqual(line["acs"], 255)
        self.assertEqual(line["adr"], 136)
        self.assertEqual(line["hs"], 25)
        self.assertEqual(line["diff"], 5)

    def test_result_and_time(self):
        self.assertEqual(stats.team_result(self.match, "Red"), {"won": True, "us": 13, "them": 9})
        self.assertEqual(stats.team_result(self.match, "Blue")["won"], False)
        self.assertEqual(stats.started(self.match), datetime.fromisoformat("2026-11-04T21:05:00+09:00"))

    def test_by_name(self):
        self.assertEqual(stats.find_player(self.match, name="mate", tag="0001")["puuid"], "puuid-2")

    def test_nothing_to_sum(self):
        self.assertEqual(stats.summary([]), {})
        self.assertEqual(stats.line({}, 0)["acs"], 0)


class HostingTest(unittest.TestCase):
    def test_site(self):
        made = site(mount="/val", host={"port": 5000})
        self.assertTrue(callable(made["app"]))
        self.assertEqual(made["name"], "VAL Tracker")
        self.assertIsInstance(made["version"](), str)

    def test_version_is_the_commit(self):
        version = paths.deployed_version()
        if (paths.ROOT / ".git").is_dir():
            self.assertRegex(version, r"^[0-9a-f]{40}$|^$")


if __name__ == "__main__":
    unittest.main()
