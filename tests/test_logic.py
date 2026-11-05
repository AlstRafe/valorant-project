import json
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from helpers import FakeFetch, fixture

from valtracker import paths, stats
from valtracker.cache import Budget, TTLCache
from valtracker.henrik import Henrik, HenrikError, _slim_mmr, _slim_rr, split_riot_id
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

    def test_matches_are_kept_without_rounds_and_kills(self):
        matches = self.henrik.matches("ap", "Tester", "JP1")
        self.assertEqual(set(matches[0]), {"metadata", "players", "teams"})
        self.assertNotIn("ability_casts", matches[0]["players"][0])
        mmr = self.henrik.mmr("ap", "Tester", "JP1")
        self.assertEqual(set(mmr), {"current", "peak", "season"})
        self.assertEqual(mmr["season"]["season"]["short"], "e9a3", "the latest act, not the whole history")

    def test_the_latest_act_whatever_the_order(self):
        seasons = [{"season": {"short": short}, "games": 1} for short in ("e10a1", "e9a3", "e2a1")]
        self.assertEqual(_slim_mmr({"seasonal": seasons})["season"]["season"]["short"], "e10a1")
        self.assertIsNone(_slim_mmr({"seasonal": []})["season"])

    def test_rr_changes(self):
        changes = self.henrik.rr_changes("ap", "Tester", "JP1")
        self.assertEqual(changes["aaaaaaaa-0000-4000-8000-000000000001"], {"change": 18, "tier": 21, "rr": 42})
        self.assertEqual(changes["aaaaaaaa-0000-4000-8000-000000000002"]["change"], -15)
        self.assertIn("/v1/mmr-history/ap/Tester/JP1", self.fetch.henrik_calls()[-1])
        # the newer answer's shape too, and nothing from what cannot be read
        newer = {"history": [{"match_id": "m", "last_change": -3, "tier": {"id": 9}, "rr": 7}, {"match_id": "x"}, 5]}
        self.assertEqual(_slim_rr(newer), {"m": {"change": -3, "tier": 9, "rr": 7}})

    def test_the_latest_15_matches_in_two_parts(self):
        # HenrikDev gives at most 10 matches an answer: the newest 10, then 5 more from the 10th
        base = json.loads(fixture("henrik_matches.json"))["data"][0]
        asked = []

        def fetch(url, headers):
            asked.append(url)
            start = int(parse_qs(urlsplit(url).query).get("start", ["0"])[0])
            size = int(parse_qs(urlsplit(url).query)["size"][0])
            data = [_numbered(base, start + n) for n in range(size)]
            return 200, json.dumps({"data": data}).encode()

        matches = Henrik(lambda: "key", fetch).matches("ap", "Tester", "JP1")
        self.assertEqual([m["metadata"]["match_id"][-2:] for m in matches], [f"{n:02}" for n in range(15)])
        self.assertEqual(sorted(urlsplit(url).query for url in asked), ["size=10", "size=5&start=10"])

    def test_the_older_part_is_not_needed(self):
        def fetch(url, headers):
            if "start=" in url:
                return 500, b""
            return 200, fixture("henrik_matches.json")

        self.assertEqual(len(Henrik(lambda: "key", fetch).matches("ap", "Tester", "JP1")), 2)

    def test_a_part_given_twice_is_shown_once(self):
        # an answer that left out the start would give the newest matches again
        self.assertEqual(len(self.henrik.matches("ap", "Tester", "JP1")), 2)

    def test_one_mode(self):
        matches = self.henrik.matches("ap", "Tester", "JP1", "competitive")
        self.assertEqual(len(matches), 2)
        self.assertTrue(all("mode=competitive" in url for url in self.fetch.henrik_calls()))
        # a match of another mode in the answer is left out
        self.assertEqual(self.henrik.matches("ap", "Tester", "JP1", "unrated"), [])
        self.henrik.matches("ap", "Tester", "JP1", "bogus")
        self.assertNotIn("mode=", self.fetch.henrik_calls()[-1])

    def test_unknown_region(self):
        self.henrik.mmr("zz", "A", "B")
        self.assertIn("/v3/mmr/ap/pc/A/B", self.fetch.asked[0][0])


def _numbered(match: dict, number: int) -> dict:
    copy = json.loads(json.dumps(match))
    copy["metadata"]["match_id"] = f"aaaaaaaa-0000-4000-8000-0000000000{number:02}"
    return copy


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
        self.assertEqual(stats.team_result(self.match, "Red"), {"won": True, "us": 13, "them": 9, "rounds": True})
        self.assertEqual(stats.team_result(self.match, "Blue")["won"], False)
        self.assertEqual(stats.started(self.match), datetime.fromisoformat("2026-11-04T21:05:00+09:00"))

    def test_a_mode_without_rounds(self):
        # Team Deathmatch and Escalation: who won, without a score of rounds
        match = json.loads(json.dumps(self.match))
        for team in match["teams"]:
            team["rounds"] = {"won": 0, "lost": 0}
        self.assertEqual(stats.team_result(match, "Red"), {"won": True, "us": 0, "them": 0, "rounds": False})

    def test_placing_and_mvp(self):
        players = {player["name"]: player for player in self.match["players"]}
        self.assertEqual(stats.placing(self.match, players["Tester"]), {"place": 1, "of": 4, "mvp": "match"})
        self.assertEqual(stats.placing(self.match, players["<b>Foe</b>"]), {"place": 2, "of": 4, "mvp": "team"})
        self.assertEqual(stats.placing(self.match, players["Mate"])["mvp"], "")
        # Deathmatch: no teams, placed by kills, no MVP
        deathmatch = {"teams": [], "players": self.match["players"]}
        self.assertEqual(stats.placing(deathmatch, players["<b>Foe</b>"]), {"place": 2, "of": 4, "mvp": ""})

    def test_summary_of_deathmatches(self):
        deathmatch = {"teams": [], "players": self.match["players"]}
        player = self.match["players"][0]
        row = {
            "result": stats.team_result(deathmatch, ""),
            "line": stats.line(player, 0),
            "rounds": 0,
            "standard": False,
            "agent_id": "a",
            "agent_name": "A",
        }
        summary = stats.summary([row, row])
        self.assertEqual((summary["games"], summary["decided"], summary["acs"], summary["adr"]), (2, 0, None, None))
        self.assertEqual(summary["form"], ["draw", "draw"])

    def test_by_name(self):
        self.assertEqual(stats.find_player(self.match, name="mate", tag="0001")["puuid"], "puuid-2")

    def test_summary_counts_the_standard_games(self):
        line = stats.line(stats.find_player(self.match, "puuid-me"), 22)
        row = {"line": line, "result": {"won": True}, "agent_id": "a", "agent_name": "A", "standard": True}
        other = dict(row, result={"won": False}, standard=False)
        self.assertEqual(stats.summary([row, other])["games"], 1)
        self.assertEqual(stats.summary([other])["games"], 1, "only other games: they are counted")

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
