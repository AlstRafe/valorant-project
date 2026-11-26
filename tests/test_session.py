"""/live の「今回のセッション」（valtracker/session.py と owner._session）。"""

import os
import time
import unittest
from unittest import mock

from helpers import PUUID, make_app

from valtracker import owner, session

NOW = 1_800_000_000


def made(*changes, gap=2400, newest=NOW - 1800, tier=15, rr=62):
    """新しい順の RR の動き（``changes``）の試合: 40 分おき、最後のランクは ``tier`` の ``rr``。"""
    found = {}
    for index, change in enumerate(changes):
        found[f"m{index}"] = {"change": change, "tier": tier, "rr": rr, "at": newest - index * gap}
        rr -= change
    return found


class SummaryTest(unittest.TestCase):
    def test_wins_losses_and_rr(self):
        found = session.summary(made(18, -14, 21, 0, 23, -9), NOW)
        self.assertEqual((found["wins"], found["losses"], found["even"], found["net"]), (3, 2, 1, 39))
        self.assertEqual([row["id"] for row in found["matches"]], ["m5", "m4", "m3", "m2", "m1", "m0"], "oldest first")
        self.assertEqual((found["tier"], found["rr"], found["to_next"]), (15, 62, 38))

    def test_a_long_break_starts_a_new_session(self):
        changes = made(18, -14) | {"old": {"change": 30, "tier": 14, "rr": 10, "at": NOW - 1800 - 5 * 3600}}
        found = session.summary(changes, NOW)
        self.assertEqual(len(found["matches"]), 2)
        self.assertEqual(found["net"], 4)

    def test_nothing_recent(self):
        self.assertIsNone(session.summary(made(18, newest=NOW - session.SHOWN - 60), NOW))
        self.assertIsNone(session.summary({}, NOW))
        self.assertIsNone(session.summary(None, NOW))

    def test_odd_answers_are_left_out(self):
        changes = made(18) | {"a": None, "b": {"change": "x"}, "c": {"at": NOW, "tier": 0}, "d": {"at": "soon"}}
        found = session.summary(changes, NOW)
        self.assertEqual(len(found["matches"]), 1)

    def test_no_bar_above_ascendant(self):
        self.assertIsNone(session.summary(made(18, tier=25, rr=240), NOW)["to_next"])


class LivePageTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")
        page = self.client.get("/settings").get_data(as_text=True)
        token = page.split('name="csrf" value="', 1)[1].split('"', 1)[0]
        self.client.post("/settings", data={"csrf": token, "do": "riot", "riot": "ssid-1"})
        self.riot.pd["player"] = PUUID
        rows = self.riot.pd["updates"]["Matches"]
        template = rows[0]
        rows[:] = [
            template
            | {
                "MatchID": f"bbbbbbbb-0000-4000-8000-00000000010{index}",
                "MatchStartTime": int(time.time() - 1800 - index * 2400) * 1000,
                "TierBeforeUpdate": 15,
                "TierAfterUpdate": 15,
                "RankedRatingAfterUpdate": 62,
                "RankedRatingEarned": earned,
            }
            for index, earned in enumerate((18, -14, 21))
        ]

    def asked(self) -> int:
        return len([url for _, url, _, _ in self.riot.asked if "competitiveupdates" in url])

    def page(self) -> str:
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        answer = self.client.get("/live")
        self.assertEqual(answer.status_code, 200)
        return answer.get_data(as_text=True)

    def test_shown_when_the_game_is_closed_and_in_the_menus(self):
        for phase in ("", "menus"):
            self.riot.phase = phase
            text = self.page()
            self.assertIn('<h2 id="session-title">今回のセッション', text, phase)
            self.assertIn("2勝 1敗", text)
            self.assertIn(">+25</strong><span>RR</span>", text)
            self.assertIn("次のランクまで 38 RR", text)
            self.assertIn('href="/m/ap/bbbbbbbb-0000-4000-8000-000000000100?me=Owner%23JP1"', text)

    def test_not_during_a_match(self):
        for phase in ("pregame", "ingame"):
            self.riot.phase = phase
            self.assertNotIn("session-title", self.page(), phase)

    def test_riot_is_asked_now_and_then(self):
        self.riot.phase = "menus"
        for _ in range(4):
            self.page()
        self.assertEqual(self.asked(), 1)
        self.app.config["VAL_SESSION"]["asked"] -= owner.SESSION_EVERY
        self.page()
        self.assertEqual(self.asked(), 2)

    def test_an_old_session_is_not_shown(self):
        for row in self.riot.pd["updates"]["Matches"]:
            row["MatchStartTime"] -= (session.SHOWN + 3600) * 1000
        self.riot.phase = "menus"
        self.assertNotIn("session-title", self.page())

    def test_riot_not_answering(self):
        self.riot.phase = "menus"
        self.riot.pd["updates"] = {"httpStatus": 500}
        self.assertNotIn("session-title", self.page())


if __name__ == "__main__":
    unittest.main()
