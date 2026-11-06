"""A player's seasons (seasons.py) from HenrikDev's stored matches (henrik._slim_stored)."""

import json
import os
import unittest
from unittest import mock

from helpers import fixture, make_app

from valtracker import henrik, seasons
from valtracker.henrik import HenrikError

VISITOR = {"homepage.visitor": True}


def stored() -> list:
    return henrik._slim_stored(json.loads(fixture("henrik_stored.json"))["data"])


class SlimTest(unittest.TestCase):
    def test_what_is_kept_of_a_match(self):
        first, second = stored()[:2]
        self.assertEqual(first["season"], "e9a3")
        self.assertEqual((first["won"], first["rounds"], first["kills"], first["head"]), (True, 22, 22, 22))
        self.assertTrue(second["won"], "on the blue team: blue's rounds are the player's")
        self.assertEqual(first["agent"], "11111111-1111-4111-8111-111111111111")

    def test_a_draw_and_a_broken_match(self):
        draw = {"meta": {}, "stats": {"team": "Red"}, "teams": {"red": 12, "blue": 12}}
        self.assertIsNone(henrik._slim_stored([draw, "x"])[0]["won"])
        self.assertIsNone(henrik._slim_stored([{"stats": {"team": "?"}, "teams": {"red": 13}}])[0]["won"])
        with self.assertRaises(HenrikError):
            henrik._slim_stored({"not": "a list"})


class SeasonsTest(unittest.TestCase):
    def test_each_act_the_latest_first_with_the_change_from_the_one_before(self):
        acts = seasons.seasons(stored())
        self.assertEqual([act["name"] for act in acts], ["E9 A3", "E9 A2"])
        latest, before = acts
        self.assertEqual((latest["games"], latest["wins"], latest["losses"], latest["win_rate"]), (4, 3, 1, 75))
        self.assertEqual(latest["kd"], round(78 / 59, 2))
        self.assertEqual(latest["peak"], 22)
        self.assertEqual(latest["change"]["win_rate"], 50)
        self.assertEqual(latest["change"]["kd"], round(latest["kd"] - before["kd"], 2))
        self.assertNotIn("change", before, "the oldest act has nothing before it")
        self.assertEqual([(agent["games"], agent["win_rate"]) for agent in latest["agents"]], [(3, 100), (1, 0)])

    def test_nothing_known(self):
        self.assertEqual(seasons.seasons([]), [])
        empty = seasons.numbers([])
        self.assertEqual((empty["games"], empty["hs"], empty["adr"]), (0, None, None))
        self.assertEqual(seasons.act_name("e10a1"), "E10 A1")


class PageTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.app, self.fetch, _ = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200) -> str:
        answer = self.client.get(path, environ_base=VISITOR)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def test_the_acts_and_everything_stored(self):
        text = self.page("/p/Tester/JP1/seasons")
        self.assertIn("シーズン別の成績", text)
        self.assertLess(text.index("E9 A3"), text.index("E9 A2"))
        self.assertIn("保存されている全部", text)
        self.assertIn("ここでは 8 試合", text, "how many matches were counted")
        self.assertIn('class="change up"', text)
        self.assertIn("ジェット", text, "the agents by name")
        self.assertIn("Tier 22", text, "the act's best rank")
        calls = [url for url in self.fetch.henrik_calls() if "/stored-matches/" in url]
        self.assertEqual(len(calls), 1)
        self.assertIn("mode=competitive", calls[0])
        self.page("/p/Tester/JP1/seasons")
        self.assertEqual(len([url for url in self.fetch.henrik_calls() if "/stored-matches/" in url]), 1, "kept")

    def test_another_mode(self):
        text = self.page("/p/Tester/JP1/seasons?mode=unrated")
        self.assertIn("mode=unrated", [url for url in self.fetch.henrik_calls() if "/stored-matches/" in url][0])
        self.assertNotIn("Tier 22", text, "no rank outside competitive")
        self.page("/p/Tester/JP1/seasons?mode=nonsense")
        self.assertIn("mode=competitive", [url for url in self.fetch.henrik_calls() if "/stored-matches/" in url][-1])

    def test_none_stored_and_failures(self):
        henrik_api = self.app.config["VAL_HENRIK"]
        with mock.patch.object(henrik_api, "stored", side_effect=HenrikError("notfound")):
            self.assertIn("まだ保存されていません", self.page("/p/Tester/JP1/seasons"))
        with mock.patch.object(henrik_api, "stored", side_effect=HenrikError("busy")):
            self.assertIn('role="alert"', self.page("/p/Tester/JP1/seasons", 503))
        self.page("/p/bad%23name/JP1/seasons", 404)

    def test_the_player_page_links_to_it(self):
        self.assertIn('href="/p/Tester/JP1/seasons"', self.page("/p/Tester/JP1?full=1"))


if __name__ == "__main__":
    unittest.main()
