import unittest

from valtracker import smurf
from valtracker.smurf import guess


class SmurfTest(unittest.TestCase):
    """サブ垢の確率の推定: 材料ひとつでは決まらず、いくつか重なって初めて高くなる。根拠を足すと確率になる。"""

    def sums(self, found: dict) -> None:
        self.assertEqual(found["base"] + sum(sign["points"] for sign in found["signs"]), found["percent"])

    def test_signs(self):
        new = guess(18, 35, acts=1, games=12, win_rate=78)
        self.assertEqual(new["kind"], "likely")
        self.assertEqual((new["side"], new["shown"]), ("smurf", new["percent"]))
        self.assertGreaterEqual(new["percent"], 75)
        self.assertEqual(len(new["signs"]), 3)
        self.assertEqual(new["signs"][0]["words"], (smurf.LEVEL_NEW, {"level": 35}), "the strongest sign first")
        self.assertEqual(new["signs"][0]["share"], min(100, round(100 * new["signs"][0]["points"] / smurf.FULL)))
        self.assertTrue(all(sign["points"] > 0 for sign in new["signs"]))
        self.sums(new)
        self.assertEqual(guess(18, 35, acts=8)["kind"], "maybe", "a low level alone")
        main = guess(18, 320, acts=12, games=20, win_rate=52)
        self.assertEqual((main["kind"], main["side"], main["sure"]), ("main", "main", "high"))
        self.assertEqual(main["shown"], 100 - main["percent"])
        self.assertLess(main["percent"], 5)
        self.assertEqual(main["signs"][0]["words"], (smurf.LEVEL_HIGH, {"level": 320}))
        self.assertTrue(all(sign["points"] <= 0 for sign in main["signs"]))
        self.sums(main)

    def test_more_signs_more_sure(self):
        """VAL スコアが飛び抜けていれば上がり、帯の真ん中なら下がる。"""
        plain = guess(18, 80, acts=4, games=30, win_rate=60)
        high = guess(18, 80, acts=4, games=30, win_rate=60, score=820, score_games=15)
        middle = guess(18, 80, acts=4, games=30, win_rate=60, score=480, score_games=15)
        self.assertGreater(high["percent"], plain["percent"] + 10)
        self.assertLess(middle["percent"], plain["percent"])
        self.assertIsNone(
            next((s for s in guess(18, 80, score=820, score_games=3)["signs"] if s["words"][0] == smurf.SCORE), None),
            "3 scored matches are not enough",
        )
        for found in (plain, high, middle):
            self.sums(found)

    def test_win_rate_needs_matches(self):
        """少ない試合の高い勝率は偶然でも起きる: 10 試合未満は使わず、そう書く。"""
        few = guess(18, 120, acts=6, games=6, win_rate=100)
        self.assertIn((smurf.FEW_GAMES, {"games": 6}), few["unused"])
        self.assertEqual(len(few["signs"]), 2)
        self.assertGreater(
            guess(18, 120, acts=6, games=60, win_rate=70)["percent"],
            guess(18, 120, acts=6, games=10, win_rate=70)["percent"],
            "the same rate over more matches says more",
        )
        act = guess(18, 120, acts=6, games=20, win_rate=70, act_record=True)
        self.assertIn((smurf.WIN_ACT, {"rate": 70, "games": 20}), [sign["words"] for sign in act["signs"]])

    def test_nothing_to_go_on(self):
        self.assertIsNone(guess(0, 50), "no rank")
        self.assertIsNone(guess(18, None), "the level hidden and nothing else")
        self.assertIsNone(guess(18, None, acts=0), "0 acts with a rank: the list was not read")

    def test_hidden_level(self):
        """A hidden level leaves the acts and the win rate (and says the level was not used)."""
        hidden = guess(18, None, acts=1, games=12, win_rate=80)
        self.assertEqual(hidden["side"], "smurf")
        self.assertIn((smurf.NO_LEVEL, {}), hidden["unused"])
        self.sums(hidden)
        alone = guess(18, None, acts=10)
        self.assertEqual((alone["kind"], alone["sure"]), ("main", "low"))
        self.assertEqual(alone["signs"][0]["words"], (smurf.ACTS_SOME, {"count": 10}))

    def test_points_always_add_up(self):
        for tier in (3, 9, 12, 15, 21, 25):
            for level in (None, 20, 45, 90, 180, 400):
                for acts in (None, 1, 3, 12):
                    for games, rate in ((0, None), (15, 40), (25, 85)):
                        found = guess(tier, level, acts, games, rate, 700 if level == 45 else None, 8)
                        if found:
                            with self.subTest(tier=tier, level=level, acts=acts, games=games):
                                self.sums(found)
                                self.assertTrue(1 <= found["percent"] <= 99)


if __name__ == "__main__":
    unittest.main()
