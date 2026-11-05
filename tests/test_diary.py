"""The owner's store diary (diary.py): the days before, the days in a row, the wish list, the night market's cards
face down, and the calendar of the daily store's change (for everyone)."""

import json
import os
import re
import unittest
from datetime import date, timedelta
from unittest import mock

from helpers import make_app

from valtracker.diary import store_day

VISITOR = {"homepage.visitor": True}
PRIME = "aa000000-0000-4000-8000-000000000001"
KNIFE = "ac000000-0000-4000-8000-000000000001"


class DiaryTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def token(self) -> str:
        page = self.client.get("/settings").get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def link(self):
        self.client.post("/settings", data={"csrf": self.token(), "do": "riot", "riot": "ssid-1"})

    def page(self, path: str, status: int = 200, **kwargs) -> str:
        answer = self.client.get(path, **kwargs)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def saved(self) -> dict:
        return json.loads((self.folder / "diary.json").read_text(encoding="utf-8"))

    def test_the_store_is_noted_and_shown_later(self):
        self.link()
        self.page("/shop")
        days = self.saved()["days"]
        self.assertEqual(len(days), 1)
        (entry,) = days.values()
        self.assertEqual(entry["daily"], [{"uuid": KNIFE, "price": 3550}], "an unknown item is left out")
        self.assertEqual(entry["night"], [{"uuid": PRIME, "price": 1065, "discount": 40}])
        self.page("/shop")
        self.assertEqual(len(self.saved()["days"]), 1, "one entry a day")
        text = self.page("/shop/history")
        self.assertIn("これまでのストア", text)
        self.assertIn("レイヴァー ナイフ", text)
        self.assertIn("3,550", text)
        self.assertIn("ナイトマーケット", text)
        self.assertIn("−40%", text)
        self.assertIn(f'href="/skins/{KNIFE}"', text, "each skin's page in the skin library")
        self.page("/shop/history", 404, environ_base=VISITOR)
        self.assertIn('href="/shop/history"', self.page("/shop"))

    def test_days_in_a_row(self):
        diary = self.app.config["VAL_DIARY"]
        today = date(2026, 11, 6)
        for back in (0, 1, 2, 4):
            diary.note((today - timedelta(days=back)).isoformat(), [{"uuid": KNIFE, "price": 1}], None)
        self.assertEqual(diary.streak("2026-11-06"), 3)
        self.assertEqual(diary.streak("2026-11-07"), 0)
        self.assertEqual(diary.streak("nonsense"), 0)
        self.assertEqual([day for day, _ in diary.days()][:2], ["2026-11-06", "2026-11-05"])
        self.assertEqual(store_day(86400 * 3, 86400 * 2 + 5), "1970-01-03", "the day the store runs on (UTC)")
        self.assertEqual(store_day(0, 86400 * 2 + 5), "1970-01-03", "no end known: now")

    def test_the_streak_on_the_shop(self):
        self.link()
        self.page("/shop")
        (day,) = self.saved()["days"]
        before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        self.app.config["VAL_DIARY"].note(before, [{"uuid": KNIFE, "price": 1}], None)
        self.assertIn("2 日連続", self.page("/shop"))

    def test_wish_list(self):
        self.link()
        answer = self.client.post(f"/skins/{PRIME}/want", data={"csrf": self.token(), "on": "1"})
        self.assertEqual(answer.status_code, 303)
        self.assertEqual(self.saved()["wants"], [PRIME])
        self.assertIn("欲しいリストから外す", self.page(f"/skins/{PRIME}"))
        self.assertIn("欲しい", self.page("/skins?wants=1"))
        text = self.page("/shop")
        self.assertNotIn("欲しいスキンが並んでいます", text, "one already owned is no longer wanted")
        self.client.post(f"/skins/{KNIFE}/want", data={"csrf": self.token(), "on": "1"})
        self.assertEqual(self.saved()["wants"], [KNIFE, PRIME], "the latest first")
        text = self.page("/shop")
        self.assertIn("欲しいスキンが並んでいます: レイヴァー ナイフ", text, "among today's four")
        self.assertIn("item tier-exclusive want", text)
        self.client.post(f"/skins/{KNIFE}/want", data={"csrf": self.token(), "on": "0"})
        self.client.post(f"/skins/{PRIME}/want", data={"csrf": self.token(), "on": "0"})
        self.assertNotIn("wants", self.saved())
        self.assertNotIn("欲しいスキンが並んでいます", self.page("/shop"))
        answer = self.client.post(f"/skins/{PRIME}/want", data={"csrf": self.token(), "on": "1"}, environ_base=VISITOR)
        self.assertEqual(answer.status_code, 404, "the owner's only")
        self.assertNotIn("欲しいリスト", self.page(f"/skins/{PRIME}", environ_base=VISITOR))

    def test_night_market_cards_lie_face_down(self):
        self.link()
        text = self.page("/shop")
        self.assertRegex(text, r'class="shelf night" data-night="\d{4}-\d\d-\d\d"')
        self.assertIn('data-flip="1"', text, "a card the game has not shown yet")
        self.assertIn("すべてめくる", text)

    def test_calendar_of_the_daily_store(self):
        answer = self.client.get("/shop/reminder.ics", environ_base=VISITOR)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.mimetype, "text/calendar")
        self.assertIn("attachment", answer.headers["Content-Disposition"])
        text = answer.get_data(as_text=True)
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("\r\nRRULE:FREQ=DAILY\r\n", text)
        self.assertRegex(text, r"\r\nDTSTART:\d{8}T000000Z\r\n", "midnight UTC")
        self.assertIn("SUMMARY:VALORANT のストアが入れ替わりました", text)
        self.assertIn("BEGIN:VALARM", text)
        self.client.set_cookie("lang", "en-GB")
        text = self.client.get("/shop/reminder.ics", environ_base=VISITOR).get_data(as_text=True)
        self.assertIn("SUMMARY:The VALORANT store has changed", text)
        self.assertIn('href="/shop/reminder.ics"', self.page("/shop", environ_base=VISITOR))


if __name__ == "__main__":
    unittest.main()
