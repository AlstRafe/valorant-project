"""The score of a match out of 1000 and "top X%" (score.py), the anonymous lines they are measured against
(activity.py's yardstick), and where the player and match pages show them."""

import os
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from helpers import make_app

from valtracker import score
from valtracker.activity import LINES_ENOUGH, Activity

VISITOR = {"homepage.visitor": True}
NOON = int(datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp())


def match(number: int, tier: int = 16, queue: str = "competitive", rounds: int = 22, score_of=None):
    """A match of ten players; player ``i`` scores ``score_of(i)`` per round (by default 150 + 20 i)."""
    score_of = score_of or (lambda i: 150 + 20 * i)
    return {
        "metadata": {
            "match_id": f"m{number}",
            "region": "ap",
            "cluster": "Tokyo",
            "queue": {"id": queue},
            "started_at": datetime.fromtimestamp(NOON - number * 60, UTC).isoformat().replace("+00:00", "Z"),
            "game_length_in_ms": 1800 * 1000,
        },
        "teams": [
            {"team_id": "Red", "won": True, "rounds": {"won": 13, "lost": rounds - 13}},
            {"team_id": "Blue", "won": False, "rounds": {"won": rounds - 13, "lost": 13}},
        ],
        "players": [
            {
                "puuid": f"p{number}-{i}",
                "team_id": "Red" if i < 5 else "Blue",
                "tier": {"id": tier},
                "stats": {
                    "score": score_of(i) * rounds,
                    "kills": 10 + i,
                    "deaths": 15,
                    "assists": 4,
                    "headshots": 10 + i,
                    "bodyshots": 40,
                    "legshots": 5,
                    "damage": {"dealt": (100 + 10 * i) * rounds, "received": 130 * rounds},
                },
            }
            for i in range(10)
        ],
    }


class ScoreTest(unittest.TestCase):
    def test_standing_counts_ties_half(self):
        values = [1, 2, 2, 3]
        self.assertEqual(score.standing(values, 0), 0)
        self.assertEqual(score.standing(values, 2), 0.5)
        self.assertEqual(score.standing(values, 9), 1)
        self.assertIsNone(score.standing(values, None))
        self.assertIsNone(score.standing([], 2))

    def test_a_match_in_the_middle_that_ends_in_a_draw_is_500(self):
        stick = {"acs": [100, 200, 300], "kast": [60, 70, 80], "dd": [-20, 0, 20]}
        self.assertEqual(score.match_score({"acs": 200, "kast": 70, "dd": 0}, 0.5, stick), 500)
        self.assertEqual(score.match_score({"acs": 999, "kast": 99, "dd": 99}, 1, stick), 1000)
        self.assertEqual(score.match_score({"acs": 0, "kast": 0, "dd": -99}, 0, stick), 0)

    def test_what_is_not_known_does_not_count(self):
        stick = {"acs": [100, 200, 300], "kast": [], "dd": [-20, 0, 20]}
        # KAST is not known (no rounds): ACS and DDΔ (0.55) and the win (0.2) are weighed alone
        self.assertEqual(score.match_score({"acs": 999, "kast": None, "dd": 99}, 0, stick), round(1000 * 0.55 / 0.75))
        self.assertIsNone(score.match_score({"acs": 999}, None, {"acs": [1]}), "less than half the weights")
        self.assertIsNone(score.match_score({"acs": 200}, 1, {}), "no yardstick")

    def test_grades_and_top(self):
        self.assertEqual([score.grade(value) for value in (1000, 800, 799, 650, 500, 350, 349, 0)], list("SSAABCDD"))
        self.assertEqual(score.grade(None), "")
        self.assertEqual(score.top([0.9, 0.8]), 15)
        self.assertEqual(score.top([1.0]), 1, "never top 0%")
        self.assertEqual(score.top([0.0]), 100)
        self.assertIsNone(score.top([None]))


class YardstickTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.now = NOON + 3600
        self.activity = Activity(Path(folder.name), clock=lambda: self.now)
        self.addCleanup(self.activity.close)

    def test_nothing_until_enough_lines(self):
        self.activity.seen([match(number) for number in range(LINES_ENOUGH // 10 - 1)])
        self.assertEqual(self.activity.yardstick(16), ({}, 0, ""))

    def test_the_players_own_band_once_it_has_enough(self):
        gold = [match(number, tier=12) for number in range(LINES_ENOUGH // 10)]
        self.activity.seen(gold + [match(100 + number, tier=24) for number in range(3)])
        stick, lines, band = self.activity.yardstick(12)
        self.assertEqual(lines, LINES_ENOUGH, "only the band's lines")
        self.assertTrue(band)
        self.assertEqual(stick["acs"], sorted(stick["acs"]))
        self.assertEqual(min(stick["acs"]), 150)
        # a band with too few lines of its own: every band together
        stick, lines, band = self.activity.yardstick(24)
        self.assertEqual((lines, band), (LINES_ENOUGH + 30, ""))

    def test_only_full_matches_of_five_against_five(self):
        self.activity.seen([match(1, queue="deathmatch"), match(2, rounds=3), match(3)])
        rows = self.activity._connect().execute("SELECT match, slot, won FROM lines ORDER BY slot").fetchall()
        self.assertEqual({row[0] for row in rows}, {"m3"})
        self.assertEqual([row[2] for row in rows], [1.0] * 5 + [0.0] * 5)
        self.assertEqual(len(rows), 10, "no player is kept, only their place in the list")

    def test_a_new_match_is_in_the_yardstick_at_once(self):
        self.activity.seen([match(number) for number in range(LINES_ENOUGH // 10)])
        self.assertEqual(self.activity.yardstick(16)[1], LINES_ENOUGH)
        self.activity.seen([match(999)])
        self.assertEqual(self.activity.yardstick(16)[1], LINES_ENOUGH + 10)

    def test_old_lines_are_dropped(self):
        self.activity.seen([match(number) for number in range(LINES_ENOUGH // 10)])
        self.now += 40 * 86400
        self.activity._pruned = 0
        self.activity.seen([match(5000)])
        self.assertEqual(self.activity.yardstick(16), ({}, 0, ""))


class PagesTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.app, self.fetch, _ = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str) -> str:
        answer = self.client.get(path, environ_base=VISITOR)
        self.assertEqual(answer.status_code, 200, path)
        return answer.get_data(as_text=True)

    def stick(self, low: int, high: int) -> mock._patch:
        """Every number's yardstick: 400 lines evenly from ``low`` to ``high`` (KAST 0-100, DDΔ -100..100)."""
        spread = [low + (high - low) * n / 399 for n in range(400)]
        stick = {"acs": spread, "adr": spread, "kd": [n / 200 for n in range(400)], "hs": [n / 4 for n in range(400)]}
        stick |= {"kast": [n / 4 for n in range(400)], "dd": [-100 + n / 2 for n in range(400)]}
        return mock.patch.object(self.app.config["VAL_ACTIVITY"], "yardstick", return_value=(stick, 400, "gold"))

    def test_the_player_page_shows_the_score_and_top_shares(self):
        with self.stick(0, 400):
            text = self.page("/p/Tester/JP1?full=1")
        self.assertIn("VAL スコア", text)
        found = re.search(r'class="score-big"><strong>(\d+)<', text)
        self.assertIsNotNone(found, "the average score, big")
        self.assertTrue(0 <= int(found.group(1)) <= 1000)
        self.assertRegex(text, r"(上位|下位) \d+%")
        self.assertIn("スコアの出し方", text, "how it is reckoned is on the page")
        self.assertIn("row-score", text, "each match's score in the list")

    def test_no_score_without_a_yardstick(self):
        with mock.patch.object(self.app.config["VAL_ACTIVITY"], "yardstick", return_value=({}, 0, "")):
            text = self.page("/p/Tester/JP1?full=1")
        self.assertNotIn("VAL スコア", text)
        self.assertNotIn("row-score", text)

    def test_the_match_page_has_a_score_column(self):
        with self.stick(0, 400):
            page = self.page("/p/Tester/JP1?full=1")
            link = re.search(r'href="(/m/[^"]+)"', page)
            self.assertIsNotNone(link)
            text = self.page(link.group(1))
        self.assertIn("<th", text)
        self.assertIn("スコア", text)
        self.assertRegex(text, r"grade-[sabcd]")


if __name__ == "__main__":
    unittest.main()
