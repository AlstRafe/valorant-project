import json
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from helpers import FakeFetch, fixture

from valtracker import paths, rounds, stats
from valtracker.cache import Budget, TTLCache
from valtracker.henrik import Henrik, HenrikError, _slim_mmr, _slim_rr, split_riot_id
from valtracker.hosting import site


class RiotIdTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(split_riot_id("Tester#JP1"), ("Tester", "JP1"))
        self.assertEqual(split_riot_id(" 名前 ＃ 日本 "), ("名前", "日本"))
        self.assertEqual(split_riot_id("Ｔｅｓｔｅｒ＃ＪＰ１"), ("Ｔｅｓｔｅｒ", "JP1"))  # only the tag is half-width
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
        self.assertEqual(set(matches[0]), {"metadata", "players", "teams", "digest"})
        self.assertNotIn("ability_casts", matches[0]["players"][0])
        # what the pages use of the rounds and kills is kept, small (rounds.py)
        digest = matches[0]["digest"]
        self.assertEqual(len(digest["rounds"]), 22)
        # where the victim died and where the killer stood (heatmap.py)
        self.assertEqual(digest["kills"][0], [0, 10000, 0, 2, VANDAL_OR_CLASSIC, [1], [2050, -4450], [4339, -3164]])
        self.assertEqual(matches[1]["digest"], {}, "a match without rounds or kills")
        mmr = self.henrik.mmr("ap", "Tester", "JP1")
        self.assertEqual(set(mmr), {"current", "peak", "seasons"})
        latest = mmr["seasons"][0]
        self.assertEqual(latest["short"], "e9a3")
        # the badge keeps the 9 best wins, not the act's every win
        self.assertEqual(latest["best"], [22, 21, 21, 21, 20, 20, 20, 19, 19])
        self.assertEqual((latest["games"], latest["wins"], latest["end_tier"], latest["old"]), (20, 12, 21, False))
        self.assertTrue(mmr["seasons"][-1]["old"], "Episode 4: the ranks before Ascendant")

    def test_acts_latest_first_whatever_the_order(self):
        seasons = [{"season": {"short": short}, "games": 1} for short in ("e9a3", "e10a1", "e2a1")]
        seasons.append({"season": {"short": "e3a1"}, "games": 0})  # not played: not listed
        self.assertEqual(
            [act["short"] for act in _slim_mmr({"seasonal": seasons})["seasons"]], ["e10a1", "e9a3", "e2a1"]
        )
        self.assertEqual(_slim_mmr({"seasonal": None})["seasons"], [])

    def test_rr_changes(self):
        changes = self.henrik.rr_changes("ap", "Tester", "JP1")
        self.assertEqual(
            changes["aaaaaaaa-0000-4000-8000-000000000001"],
            {"change": 18, "tier": 21, "rr": 42, "elo": 1842, "at": 1793761500},
        )
        self.assertEqual(changes["aaaaaaaa-0000-4000-8000-000000000002"]["change"], -15)
        self.assertIn("/v1/mmr-history/ap/Tester/JP1", self.fetch.henrik_calls()[-1])
        # the newer answer's shape too, and nothing from what cannot be read
        newer = {
            "history": [
                {
                    "match_id": "m",
                    "last_change": -3,
                    "tier": {"id": 9},
                    "rr": 7,
                    "elo": 607,
                    "date": "2026-11-04T12:00Z",
                },
                {"match_id": "x"},
                5,
            ]
        }
        self.assertEqual(_slim_rr(newer), {"m": {"change": -3, "tier": 9, "rr": 7, "elo": 607, "at": 1793793600}})

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


CLASSIC = "29a0cfab-485b-f5d5-779a-b59f85e204a8"
VANDAL_OR_CLASSIC = CLASSIC  # the first round is a pistol round


class RoundsTest(unittest.TestCase):
    """The fixture's first match is told round by round (2 v 2, 13-9): Tester and Mate are Red, Foe and Other Blue."""

    def setUp(self):
        self.match = json.loads(fixture("henrik_matches.json"))["data"][0]
        self.digest = rounds.digest(self.match)
        self.me, self.mate, self.foe, self.other = self.digest["players"]

    def test_first_kills_and_deaths(self):
        self.assertEqual((self.me["fk"], self.me["fd"]), (13, 1))
        self.assertEqual((self.foe["fk"], self.foe["fd"]), (9, 12))

    def test_kast(self):
        # Mate's round 1: an assist, then killed and traded by Tester 3 s later; round 2: killed, no trade
        self.assertEqual((self.me["kast"], self.mate["kast"], self.me["rounds"]), (13, 13, 22))

    def test_clutches_multi_kills_plants(self):
        self.assertEqual((self.me["clutches"], self.me["clutch_best"]), (1, 1))  # round 1: last alive, 1 v 1, won
        self.assertEqual(self.mate["clutches"], 1)  # round 15
        self.assertEqual(self.other["clutches"], 0, "a clutch lost is not one")
        self.assertEqual(self.me["mk"], [1, 0, 0, 0])
        self.assertEqual((self.me["plants"], self.foe["plants"], self.mate["defuses"]), (1, 1, 1))

    def test_rounds(self):
        first, defused = self.digest["rounds"][0], self.digest["rounds"][14]
        self.assertEqual(
            (first["w"], first["e"], first["c"], first["s"], first["a"]), ("Red", "detonate", "clutch", "A", "Red")
        )
        self.assertEqual(first["l"], {"Red": 1600, "Blue": 1600})
        self.assertEqual((defused["e"], defused["a"], defused["p"], defused["d"]), ("defuse", "Blue", 2, 1))
        self.assertEqual(self.digest["sides"]["Red"], {"atk": [7, 12], "def": [6, 10]})
        self.assertEqual(self.digest["weapons"][CLASSIC], "Classic")

    def test_sides_follow_the_plants(self):
        # a match whose blue team attacked first: every plant says so, and the guess turns round
        for item in self.match["rounds"]:
            if item.get("plant"):
                item["plant"]["player"]["puuid"] = "puuid-3" if item["id"] < 12 else "puuid-me"
        found = rounds.digest(self.match)
        self.assertEqual(found["rounds"][0]["a"], "Blue")
        self.assertEqual(found["rounds"][14]["a"], "Red")

    def test_the_older_names(self):
        # HenrikDev's v2 matches: other names for the same things
        older = {
            "metadata": {"queue": {"id": "competitive"}},
            "players": [{"puuid": "a", "team_id": "Red"}, {"puuid": "b", "team_id": "Blue"}],
            "rounds": [
                {
                    "winning_team": "Blue",
                    "end_type": "Bomb defused",
                    "bomb_planted": True,
                    "plant_events": {"planted_by": {"puuid": "a"}, "plant_site": "C"},
                    "bomb_defused": True,
                    "defuse_events": {"defused_by": {"puuid": "b"}},
                    "player_stats": [{"player_puuid": "a", "economy": {"loadout_value": 4000}}],
                }
            ],
            "kills": [
                {
                    "round": 0,
                    "kill_time_in_round": 900,
                    "killer_puuid": "b",
                    "victim_puuid": "a",
                    "damage_weapon_name": "Ghost",
                    "victim_death_location": {"x": 100, "y": -200},
                    "player_locations_on_kill": [
                        {"player_puuid": "a", "location": {"x": 1, "y": 1}},
                        {"player_puuid": "b", "location": {"x": 300.4, "y": 400}},
                    ],
                }
            ],
        }
        found = rounds.digest(older)
        self.assertEqual(
            found["rounds"][0] | {"l": None},
            {"w": "Blue", "e": "defuse", "c": "", "s": "C", "p": 0, "d": 1, "a": "Red", "l": None},
        )
        self.assertEqual(found["kills"], [[0, 900, 1, 0, "Ghost", [], [100, -200], [300, 400]]])
        self.assertEqual(found["players"][1]["weapons"], {"Ghost": 1})

    def test_nothing_to_tell(self):
        self.assertEqual(rounds.digest({"players": [], "rounds": [{"round": 0}], "kills": [{"kill": 0}]}), {})
        self.assertEqual(rounds.digest({}), {})


class MoreStatsTest(unittest.TestCase):
    def setUp(self):
        self.match = json.loads(fixture("henrik_matches.json"))["data"][0]
        self.match["digest"] = rounds.digest(self.match)
        self.me = stats.find_player(self.match, "puuid-me")

    def test_the_line_with_the_rounds(self):
        line = stats.line(self.me, 22, stats.advanced(self.match, self.me))
        self.assertEqual((line["kast"], line["fk"], line["fd"], line["mk"], line["clutches"]), (59, 13, 1, 0, 1))
        self.assertEqual(line["dd"], 91)  # (3000 - 1000) / 22
        self.assertEqual(line["econ"], 50)  # 3000 damage for 60000 credits
        self.assertIsNone(stats.line(self.me, 22)["kast"], "without the rounds: not known")

    def test_lobby_and_mates(self):
        lobby = stats.lobby(self.match, self.me, 22)
        self.assertEqual(round(lobby["acs"]), 182)  # (182 + 227 + 136) / 3, team-mates and opponents alike
        self.assertAlmostEqual(lobby["fk"], 9 / 3 / 22)
        self.assertEqual([mate["name"] for mate in stats.mates(self.match, self.me)], ["Mate"])
        self.assertIsNone(stats.lobby(self.match, self.me, 0))

    def test_summary_by_agent_map_side(self):
        row = {
            "line": stats.line(self.me, 22, stats.advanced(self.match, self.me)),
            "adv": stats.advanced(self.match, self.me),
            "result": {"won": True, "rounds": True},
            "rounds": 22,
            "agent_id": "a",
            "agent_name": "A",
            "map": {"name": "Ascent"},
            "side": self.match["digest"]["sides"]["Red"],
            "length": 2400000,
            "weapons": {"v": 3, "c": 1},
            "lobby": stats.lobby(self.match, self.me, 22),
            "mates": stats.mates(self.match, self.me),
        }
        lost = dict(row, result={"won": False, "rounds": True}, agent_id="b", agent_name="B")
        summary = stats.summary([row, lost])
        self.assertEqual((summary["kast"], summary["fk"], summary["clutches"], summary["minutes"]), (59, 26, 2, 80))
        self.assertEqual([agent["name"] for agent in summary["agent_table"]], ["A", "B"])
        self.assertEqual(summary["maps"][0] | {"image": None}, summary["maps"][0] | {"image": None})
        self.assertEqual((summary["maps"][0]["games"], summary["maps"][0]["atk_rate"]), (2, 58))
        self.assertEqual(summary["sides"]["def"], [12, 20])
        self.assertEqual(summary["weapons"][0], {"key": "v", "kills": 6, "share": 75, "far": None, "longest": None})
        self.assertEqual(
            summary["aim"], {"head": 25, "body": 62, "leg": 13, "hits": {"head": 20, "body": 50, "leg": 10}}
        )
        self.assertEqual(
            summary["mates"][0] | {"puuid": ""},
            {"puuid": "", "name": "Mate", "tag": "0001", "games": 2, "wins": 1, "losses": 1, "win_rate": 50},
        )
        acs = next(item for item in summary["lobby"] if item["key"] == "acs")
        self.assertTrue(acs["better"])
        fd = next(item for item in summary["lobby"] if item["key"] == "fd")
        self.assertTrue(fd["better"], "fewer first deaths than the lobby is better")


class TrackerNumbersTest(unittest.TestCase):
    """What tracker.gg shows beyond the digest's sums: flawless rounds, clutches tried, how far the kills were, and
    the summary's totals, rates, roles and best match."""

    #: Red: the player (0) and a team-mate (1); Blue: three.
    MATCH = {
        "players": [{"team_id": team} for team in ("Red", "Red", "Blue", "Blue", "Blue")],
        "digest": {
            "rounds": [{"w": "Red"}, {"w": "Red"}, {"w": "Blue"}],
            "kills": [
                # round 1: won without losing anyone; the player's kills 10 m and 30 m away
                [0, 1000, 0, 2, "vandal", [], [1000, 0], [0, 0]],
                [0, 2000, 0, 3, "vandal", [], [0, 3000], [0, 0]],
                [0, 3000, 1, 4, "phantom", [], None, None],
                # round 2: the team-mate falls first, the player wins alone against three
                [1, 1000, 2, 1, "x", [], None, None],
                [1, 2000, 0, 2, "vandal", [], [5000, 0], [0, 0]],
                [1, 3000, 0, 3, "classic", [], [300, 400], [0, 0]],
                [1, 4000, 0, 4, "classic", [], None, [0, 0]],
                # round 3: alone against three again, and lost
                [2, 1000, 3, 1, "x", [], None, None],
                [2, 2000, 4, 0, "x", [], None, None],
            ],
        },
    }

    def test_extras(self):
        found = rounds.extras(self.MATCH, 0)
        self.assertEqual(found["flawless"], 1)
        self.assertEqual(found["clutch"], {3: [1, 2]})
        self.assertEqual(found["far"]["vandal"], [90.0, 3, 50.0])
        self.assertEqual(found["far"]["classic"], [5.0, 1, 5.0], "a kill without both places is not measured")
        self.assertNotIn("x", found["far"])
        self.assertEqual(rounds.extras(self.MATCH, 1)["clutch"], {}, "never the last of the team")
        self.assertEqual(rounds.extras({"players": [{}], "digest": {}}, 0), {})
        self.assertEqual(rounds.extras(self.MATCH, 9), {})

    def test_old_kills_without_places(self):
        match = json.loads(json.dumps(self.MATCH))
        match["digest"]["kills"] = [kill[:6] for kill in match["digest"]["kills"]]
        self.assertEqual(rounds.extras(match, 0)["far"], {})
        self.assertEqual(rounds.extras(match, 0)["clutch"], {3: [1, 2]})

    def test_summary(self):
        fixture_match = json.loads(fixture("henrik_matches.json"))["data"][0]
        fixture_match["digest"] = rounds.digest(fixture_match)
        me = stats.find_player(fixture_match, "puuid-me")
        adv = stats.advanced(fixture_match, me)
        row = {
            "id": "m1",
            "region": "ap",
            "line": stats.line(me, 22, adv),
            "adv": adv,
            "extra": rounds.extras(self.MATCH, 0),
            "result": {"won": True, "rounds": True},
            "rounds": 22,
            "agent_id": "a",
            "agent_name": "A",
            "role": {"name": "Duelist", "icon": None},
            "map": {"name": "Ascent"},
            "length": 2400000,
            "weapons": {"vandal": 3},
            "when": "11月4日 12:05",
        }
        other = dict(row, id="m2", result={"won": False, "rounds": True}, agent_id="b", agent_name="B")
        other["role"] = {"name": "Sentinel", "icon": None}
        other["line"] = dict(row["line"], kills=25)
        summary = stats.summary([row, other])
        self.assertEqual((summary["kills_total"], summary["deaths_total"], summary["assists_total"]), (45, 30, 10))
        self.assertEqual(summary["kad"], round(55 / 30, 2))
        self.assertEqual(summary["kpr"], round(45 / 44, 2))
        self.assertEqual((summary["fk_rate"], summary["fd_rate"]), (59, 5))  # 26 and 2 of 44 rounds
        self.assertEqual(summary["clutch_table"], [{"against": 3, "won": 2, "tried": 4, "rate": 50}])
        self.assertEqual((summary["clutch_tried"], summary["clutch_rate"]), (4, 50))
        self.assertEqual(summary["flawless"], 2)
        self.assertEqual(summary["econ"], 50)
        self.assertEqual((summary["best"]["kills"], summary["best"]["id"]), (25, "m2"))
        self.assertEqual([role["name"] for role in summary["roles"]], ["Duelist", "Sentinel"])
        self.assertEqual((summary["roles"][0]["games"], summary["roles"][0]["win_rate"]), (1, 100))
        self.assertEqual(summary["agent_table"][0]["role"], "Duelist")
        self.assertEqual((summary["agent_table"][0]["kast"], summary["agent_table"][0]["dd"]), (59, 91))
        self.assertEqual(summary["agent_table"][0]["minutes"], 40)
        self.assertEqual((summary["weapons"][0]["far"], summary["weapons"][0]["longest"]), (30, 50))


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


class BestAgentTest(unittest.TestCase):
    @staticmethod
    def row(agent: str, won: bool, acs: int = 200) -> dict:
        line = {"kills": 10, "deaths": 10, "shots": 10, "headshots": 2, "acs": acs, "adr": 120}
        return {"agent_id": agent, "agent_name": agent.title(), "agent_icon": "", "rounds": 20, "line": line} | {
            "result": {"won": won}
        }

    def test_the_best_of_those_played_more_than_once(self):
        rows = [self.row("jett", True), self.row("jett", False), self.row("sova", True), self.row("sova", True)]
        rows.append(self.row("neon", True))  # once: not enough to say
        best = stats._best_agent(rows)
        self.assertEqual((best["name"], best["games"], best["win_rate"]), ("Sova", 2, 100))

    def test_each_played_once_the_best_of_them(self):
        best = stats._best_agent([self.row("jett", False), self.row("sova", True, acs=150)])
        self.assertEqual(best["name"], "Sova")
        self.assertIsNone(stats._best_agent([]))
