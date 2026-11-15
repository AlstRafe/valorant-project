import unittest

from valtracker.smurf import guess


class SmurfTest(unittest.TestCase):
    """サブ垢の推測: サインひとつでは足りず、いくつか重なって初めてそう出る。"""

    def test_signs(self):
        new = guess(18, 35, acts=1, games=12, win_rate=78)
        self.assertEqual(new["kind"], "likely")
        self.assertEqual(len(new["why"]), 3)
        self.assertEqual(guess(18, 35, acts=8)["kind"], "maybe", "a low level alone")
        self.assertEqual(guess(18, 80, acts=9, games=20, win_rate=66)["kind"], "maybe")
        main = guess(18, 320, acts=12, games=20, win_rate=52)
        self.assertEqual(main["kind"], "main")
        self.assertEqual(main["why"][0][1], {"level": 320})

    def test_nothing_to_go_on(self):
        self.assertIsNone(guess(0, 50), "no rank")
        self.assertIsNone(guess(18, None), "the level hidden")
