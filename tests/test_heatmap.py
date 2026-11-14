"""Where a player's kills and deaths happened (heatmap.py) and the pages that draw them."""

import json
import os
import re
import unittest
from unittest import mock

from helpers import fixture, make_app

from valtracker import heatmap

VISITOR = {"homepage.visitor": True}
ASCENT = json.loads(fixture("content_maps.json"))["data"][0]
TREE, LOBBY = [3981, -5939], [4489, -3014]


def digest_match(kills: list, rounds: list | None = None) -> dict:
    """A match of four players (0 and 1 Red, 2 and 3 Blue) with ``kills`` as rounds.py keeps them."""
    players = [{"team_id": team} for team in ("Red", "Red", "Blue", "Blue")]
    rounds = rounds or [{"a": "Red"}, {"a": "Blue"}]
    return {"players": players, "digest": {"rounds": rounds, "kills": kills}}


class PlanTest(unittest.TestCase):
    def test_a_map_with_its_numbers(self):
        drawn = heatmap.plan(ASCENT)
        self.assertEqual(drawn["numbers"], (7e-05, -7e-05, 0.813895, 0.573242))
        self.assertEqual(
            [name for name, _, _ in drawn["callouts"]], ["A ツリー", "A ロビー", "アタック側 メインエントランス"]
        )
        # across from the game's y, down from its x
        self.assertEqual(heatmap.onto(drawn, TREE), (39.8, 29.5))
        self.assertIsNone(heatmap.onto(drawn, [99999, 0]), "off the picture")
        self.assertEqual(heatmap.callout(drawn, [4000, -5900]), "A ツリー")

    def test_maps_without_them(self):
        self.assertIsNone(heatmap.plan(ASCENT | {"xMultiplier": 0}), "the Range: all 0")
        self.assertIsNone(heatmap.plan(ASCENT | {"displayIcon": None}))
        self.assertIsNone(heatmap.plan({"uuid": "x"}))
        self.assertEqual(heatmap.callout(heatmap.plan(ASCENT | {"callouts": None}), TREE), "")


class PointsTest(unittest.TestCase):
    def test_kills_where_the_player_stood_and_deaths_where_they_died(self):
        kills = [
            [0, 100, 0, 2, "v", [], TREE, LOBBY],  # the round's first: a kill
            [0, 900, 3, 0, "v", [], LOBBY, TREE],  # a death
            [0, 950, 0, 1, "v", [], TREE, TREE],  # a team-mate: not counted
            [1, 100, 3, 0, "v", [], TREE, None],  # the next round's first: a death, on defence
            [1, 200, 0, 2, "v", []],  # kept before the places were
            [1, 300, 0, 3, "v", [], TREE, None],  # where the player stood is not known
        ]
        found = heatmap.points(digest_match(kills), 0)
        self.assertEqual(
            [(spot["kind"], spot["at"], spot["other"], spot["side"], spot["round"], spot["first"]) for spot in found],
            [
                ("k", LOBBY, TREE, "atk", 1, True),
                ("d", LOBBY, TREE, "atk", 1, False),
                ("d", TREE, None, "def", 2, True),
            ],
        )
        self.assertEqual(heatmap.points(digest_match(kills), 7), [], "nobody at that place")
        self.assertEqual(heatmap.points({"players": []}, 0), [])

    def test_the_drawing_and_its_places(self):
        drawn = heatmap.plan(ASCENT)
        spots = [{"kind": "d", "at": TREE, "other": LOBBY, "side": "def", "round": n, "first": n < 3} for n in range(4)]
        spots.append({"kind": "k", "at": LOBBY, "other": TREE, "side": "atk", "round": 9, "first": False})
        self.assertIsNone(heatmap.drawing(drawn, spots[:4]), "too few")
        drawing = heatmap.drawing(drawn, spots, lines=True)
        self.assertEqual((drawing["kills"], drawing["deaths"], drawing["sides"]), (1, 4, True))
        self.assertEqual(drawing["dots"][0]["line"], heatmap.onto(drawn, LOBBY))
        self.assertEqual(
            drawing["places"],
            [
                {"name": "A ツリー", "kills": 0, "deaths": 4, "first_deaths": 3, "diff": -4},
                {"name": "A ロビー", "kills": 1, "deaths": 0, "first_deaths": 0, "diff": 1},
            ],
        )
        self.assertEqual(drawing["worst"]["name"], "A ツリー")
        self.assertIsNone(heatmap.drawing(drawn, spots)["dots"][0]["line"], "lines only when asked")


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

    def test_the_player_page_draws_each_map(self):
        text = self.page("/p/Tester/JP1?full=1")
        self.assertIn("キルとデスの場所", text)
        self.assertIn('<details class="heat-panel" name="heat-map" open>', text)
        self.assertIn("/media/maps/7eaecc1b-4337-bbf6-6ab9-04b8f06b3319/displayicon.png", text)
        self.assertGreater(text.count('<circle cx="'), 5)
        self.assertIn('<th scope="row">A ツリー</th>', text, "where the player died, by name")
        self.assertIn("A ツリー でデスが多い", text)
        self.assertNotIn("<line", text, "no lines over many matches")

    def test_the_match_page_draws_the_players_with_lines(self):
        page = self.page("/p/Tester/JP1?full=1")
        link = re.search(r'href="(/m/[^"]+)"', page).group(1).replace("&amp;", "&")
        text = self.page(link)
        self.assertIn("Tester のキルとデスの場所", text)
        self.assertIn("<line", text)
        self.assertIn('value="atk"', text, "the attack and defence filter")
        self.assertNotIn(
            "のキルとデスの場所", self.page(link.split("?")[0]), "only for the player the visitor came from"
        )

    def test_nothing_for_a_map_without_its_numbers(self):
        flat = {"status": 200, "data": [ASCENT | {"xMultiplier": 0, "yMultiplier": 0}]}
        original = self.fetch.__class__.__call__

        def answer(fetch, url, headers=None, *rest, **named):
            if "/maps?" in url:
                return 200, json.dumps(flat).encode()
            return original(fetch, url, headers, *rest, **named)

        with mock.patch.object(self.fetch.__class__, "__call__", answer):
            self.assertNotIn("キルとデスの場所", self.page("/p/Tester/JP1?full=1"))


if __name__ == "__main__":
    unittest.main()
