"""The skin library (/skins, /skins/<uuid>), its videos (CSP media-src) and the visitors' ratings (ratings.py)."""

import os
import unittest
from unittest import mock

from helpers import make_app

from valtracker import content, ratings, security

VISITOR = {"homepage.visitor": True}
PRIME = "aa000000-0000-4000-8000-000000000001"
REAVER = "aa000000-0000-4000-8000-000000000002"
STANDARD = "aa000000-0000-4000-8000-000000000009"


class SkinLibraryTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200) -> str:
        answer = self.client.get(path, environ_base=VISITOR)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def rate(self, uuid: str, stars, status: int = 303):
        self.page(f"/skins/{PRIME}")  # the session's CSRF token
        with self.client.session_transaction() as session:
            token = session["csrf"]
        answer = self.client.post(
            f"/skins/{uuid}/rate", data={"csrf": token, "stars": str(stars)}, environ_base=VISITOR
        )
        self.assertEqual(answer.status_code, status)
        return answer

    def test_list_by_weapon_tier_and_name(self):
        text = self.page("/skins")
        self.assertIn("プライム ヴァンダル", text, "the Vandal's skins first")
        self.assertIn("レイヴァー ヴァンダル", text)
        self.assertNotIn("スタンダード ヴァンダル", text, "the weapon's own look is no skin")
        self.assertNotIn("レイヴァー ナイフ", text)
        self.assertIn(f'href="/skins/{PRIME}"', text)
        self.assertLess(text.index("レイヴァー ヴァンダル"), text.index("プライム ヴァンダル"), "the best tier first")
        text = self.page("/skins?weapon=all")
        self.assertIn("レイヴァー ナイフ", text)
        self.assertIn("プライム クラシック", text)
        self.assertLess(text.index("プライム クラシック"), text.index("プライム ヴァンダル"), "the game's order")
        text = self.page("/skins?q=プライム")
        self.assertIn("プライム クラシック", text, "a name is looked for in every weapon")
        self.assertNotIn("レイヴァー ヴァンダル", text)
        text = self.page("/skins?tier=exclusive")
        self.assertIn("レイヴァー ナイフ", text)
        self.assertNotIn("プライム ヴァンダル", text)
        self.assertIn("合うスキンはありませんでした", self.page("/skins?q=zzz"))
        self.assertIn("プライム ヴァンダル", self.page("/skins?weapon=nonsense&tier=nonsense"))
        self.client.set_cookie("lang", "en-GB")
        self.assertIn("Skin library", self.page("/skins"))

    def test_skin_page(self):
        text = self.page(f"/skins/{PRIME}")
        self.assertIn("<h1>プライム ヴァンダル</h1>", text)
        self.assertIn("シリーズ: プライム", text)
        self.assertIn("バリエーション 2 ゴールド", text, "a variant by its own name, without the line break")
        self.assertIn("レベル 2", text)
        self.assertIn("エフェクト", text, "what a level adds")
        self.assertIn("キルするたびに光が強まる", text, "and what it does")
        self.assertIn('src="https://valorant.dyn.riotcdn.net/x/videos/release-13.09/aa12_default_universal.mp4"', text)
        self.assertNotIn("example.org", text, "videos only from the known places")
        self.assertIn('preload="none"', text)
        self.assertIn('href="/skins?weapon=9c82e19d-4575-0200-1a81-3eacf00cf872"', text, "the weapon's own list")
        self.assertIn("プライム クラシック", text, "the same series on other weapons")
        self.assertNotIn("レイヴァー ヴァンダル", text.split('id="series-title"')[1])
        text = self.page(f"/skins/{PRIME}?chroma=aa000000-0000-4000-8000-000000000022")
        self.assertIn("aa22_default_universal.mp4", text, "the chosen variant's video")
        self.assertRegex(text, r'href="[^"]*chroma=aa000000-0000-4000-8000-000000000022[^"]*" aria-current="true"')
        self.page(f"/skins/{STANDARD}", 404)
        self.page("/skins/aa000000-0000-4000-8000-000000000011", 404)  # a level's uuid
        self.page("/skins/not-a-uuid", 404)
        self.page("/skins/00000000-0000-4000-8000-000000000000", 404)

    def test_videos_may_play_from_riot_only(self):
        self.assertIn("media-src 'self' https://valorant.dyn.riotcdn.net", security.CSP)
        answer = self.client.get("/skins", environ_base=VISITOR)
        self.assertIn("media-src", answer.headers["Content-Security-Policy"])
        self.assertTrue(all(address.startswith("https://") for address in content.VIDEOS))

    def test_rating(self):
        self.assertIn("まだ評価はありません", self.page(f"/skins/{PRIME}"))
        answer = self.rate(PRIME, 5)
        self.assertIn("rated=1", answer.headers["Location"])
        cookie = answer.headers.get("Set-Cookie", "")
        self.assertIn("val_voter=", cookie)
        self.assertIn("HttpOnly", cookie)
        text = self.page(f"/skins/{PRIME}?rated=1")
        self.assertIn("評価を受け付けました", text)
        self.assertIn("1 人が評価しました", text, "no average from one rating")
        self.assertRegex(text, r'value="5" required checked')
        self.rate(PRIME, 3)  # the same browser changes its rating
        self.assertIn("1 人が評価しました", self.page(f"/skins/{PRIME}"))
        self.rate(PRIME, 0, 400)
        self.rate(PRIME, 6, 400)
        self.rate(STANDARD, 4, 404)
        store = self.app.config["VAL_RATINGS"]
        store.rate(PRIME, "another-browser-0000000", 5)
        store.rate(PRIME, "a-third-browser-0000000", 4)
        text = self.page(f"/skins/{PRIME}")
        self.assertIn("★ 4.0", text)
        self.assertIn("3 人の評価", text)
        self.assertIn("評価の高いスキン", self.page("/skins"))
        self.assertNotIn("評価の高いスキン", self.page("/skins?q=レイヴァー"))

    def test_rating_needs_the_token(self):
        answer = self.client.post(f"/skins/{PRIME}/rate", data={"stars": "5"}, environ_base=VISITOR)
        self.assertEqual(answer.status_code, 400)

    def test_rating_limits(self):
        store = self.app.config["VAL_RATINGS"]
        with mock.patch.object(ratings, "VOTER_DAY", 2):
            self.assertTrue(store.rate("a", "browser-one-000000000", 5))
            self.assertTrue(store.rate("b", "browser-one-000000000", 5))
            self.assertFalse(store.rate("c", "browser-one-000000000", 5), "one browser's day is full")
            self.assertTrue(store.rate("c", "browser-two-000000000", 5))
        with mock.patch.object(ratings, "SITE_TEN_MINUTES", 3):
            self.assertFalse(store.rate("d", "browser-three-0000000", 5), "the whole site's ten minutes are full")
            self.assertIn("rated=busy", self.rate(PRIME, 4).headers["Location"])
        row = store._connect().execute("SELECT voter FROM ratings LIMIT 1").fetchone()
        self.assertNotIn("browser", row[0], "a browser's value is kept only as a keyed hash")

    def test_shop_and_tools_lead_to_it(self):
        self.assertIn('href="/skins"', self.page("/tools"))
        self.assertIn('href="/skins"', self.page("/shop"))
        self.assertIn('href="/shop" aria-current="page"', self.page("/skins"), "under the shop's tab")

    def test_old_saved_weapons_are_asked_again(self):
        store = self.app.config["VAL_CONTENT"]
        store._write("weapons", "ja-JP", 10**10, [])
        path = store._file("weapons", "ja-JP")
        path.write_text(path.read_text(encoding="utf-8").replace('"shape": 2, ', ""), encoding="utf-8")
        self.assertEqual(store._read("weapons", "ja-JP")[0], 0.0, "an older shape is stale at once")
        store._write("weapons", "ja-JP", 123.0, [])
        self.assertEqual(store._read("weapons", "ja-JP")[0], 123.0)


if __name__ == "__main__":
    unittest.main()
