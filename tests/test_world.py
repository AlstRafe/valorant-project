"""Riot's news (/news), the esports schedule (/esports) and their pictures through /pic/ (media.OUTSIDE)."""

import os
import unittest
from unittest import mock

from helpers import make_app

from valtracker.hosting import DISALLOW
from valtracker.media import outside_path

VISITOR = {"homepage.visitor": True}


class WorldTest(unittest.TestCase):
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

    def test_news_by_kind_and_word(self):
        text = self.page("/news")
        self.assertIn("VALORANT パッチノート 11.10", text)
        self.assertIn("いまのパッチ", text)
        self.assertIn("&lt;b&gt;開発&lt;/b&gt;の話", text, "titles are text, never HTML")
        self.assertNotIn("javascript:", text, "only https links")
        self.assertIn('href="https://valorantesports.com/news/champions"', text, "the article's own link first")
        self.assertIn('src="/pic/rgpub/sanity/images/', text)
        self.assertNotIn("example.com", text, "pictures only from the known addresses")
        self.assertTrue(any("/valorant/v1/website/ja-jp" in url for url, _ in self.fetch.asked))
        text = self.page("/news?category=esports")
        self.assertIn("Champions の結果", text)
        self.assertNotIn("新しいエージェント", text)
        self.assertNotIn("いまのパッチ", text)
        text = self.page("/news?q=エージェント")
        self.assertIn("新しいエージェント", text)
        self.assertNotIn("Champions の結果", text)
        self.assertIn("合うニュースはありませんでした", self.page("/news?q=zzz"))
        self.client.set_cookie("lang", "en-GB")
        self.assertIn("Patch notes", self.page("/news"))
        self.assertTrue(any("/valorant/v1/website/en-gb" in url for url, _ in self.fetch.asked))

    def test_news_without_henrikdev(self):
        self.fetch.henrik_status = 503
        self.assertIn('class="problem"', self.page("/news", 503))

    def test_esports_schedule_and_results(self):
        text = self.page("/esports")
        self.assertIn("1 試合中", text)
        self.assertIn("Paper Rex", text)
        self.assertIn("Sentinels", text)
        self.assertNotIn("ZETA DIVISION", text, "a result is not in the schedule")
        self.assertNotIn("Only one", text, "a match needs two teams")
        self.assertIn("&lt;i&gt;Gen.G&lt;/i&gt;", text)
        self.assertLess(text.index("Paper Rex"), text.index("Sentinels"), "live matches first")
        self.assertIn('src="/pic/lolesports/teams/prx.png"', text)
        text = self.page("/esports?show=results")
        self.assertIn("ZETA DIVISION", text)
        self.assertIn('href="https://www.youtube.com/watch?v=abc"', text)
        self.assertNotIn("Sentinels", text)
        text = self.page("/esports?league=vct_americas")
        self.assertIn("Sentinels", text)
        self.assertNotIn("Paper Rex", text)
        self.assertIn("Sentinels", self.page("/esports?league=nonsense"), "an unknown league: every one")
        self.assertIn("Sentinels", self.page("/esports?show=nonsense"))

    def test_tools_lead_to_them(self):
        text = self.page("/tools")
        self.assertIn('href="/news"', text)
        self.assertIn('href="/esports"', text)

    def test_outside_pictures(self):
        self.assertEqual(outside_path("https://static.lolesports.com/teams/zeta.png"), "lolesports/teams/zeta.png")
        self.assertIsNone(outside_path("https://static.lolesports.com/../etc/passwd"))
        self.assertIsNone(outside_path("https://evil.example/teams/zeta.png"))
        self.assertIsNone(outside_path("http://static.lolesports.com/teams/zeta.png"))
        answer = self.client.get("/pic/lolesports/teams/zeta.png")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.mimetype, "image/png")
        self.assertEqual(self.client.get("/pic/lolesports/../x.png").status_code, 404)
        self.assertEqual(self.client.get("/pic/other/teams/zeta.png").status_code, 404)
        answer.close()
        self.assertIn("/pic/", DISALLOW, "for crawlers to leave alone, as /media/")


if __name__ == "__main__":
    unittest.main()
