"""Riot's news (/news), the esports schedule (/esports) and their pictures through /pic/ (media.OUTSIDE)."""

import json
import os
import unittest
from unittest import mock

from helpers import FakeFetch, make_app

from valtracker.cache import Budget
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
        self.assertIn('href="https://playvalorant.com/ja-jp/news/game-updates/valorant-patch-notes-11-10"', text)
        self.assertIn('href="https://www.youtube.com/watch?v=abcdefghijk"', text, "a video's own page")
        self.assertNotIn("//example.com/merch", text)
        self.assertTrue(any("valorant_news_page_list?locale=ja_JP" in url for url, _ in self.fetch.asked))
        self.assertFalse(any("api.henrikdev.xyz" in url for url, _ in self.fetch.asked), "Riot's own list first")
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
        self.assertTrue(any("locale=en_GB" in url for url, _ in self.fetch.asked))

    def test_news_from_henrikdev_when_riot_does_not_answer(self):
        self.fetch.riot_web_status = 503
        text = self.page("/news")
        self.assertIn("VALORANT パッチノート 11.10", text)
        self.assertTrue(any("/valorant/v1/website/ja-jp" in url for url, _ in self.fetch.asked))
        riot = sum("riotgames.com" in url for url, _ in self.fetch.asked)
        self.page("/news")
        self.assertEqual(sum("riotgames.com" in url for url, _ in self.fetch.asked), riot, "a failure is kept a while")

    def test_news_without_riot_or_henrikdev(self):
        self.fetch.riot_web_status = 503
        self.fetch.henrik_status = 503
        self.assertIn('class="problem"', self.page("/news", 503))

    def test_the_latest_patch_even_when_the_list_has_none(self):
        original = FakeFetch.__call__

        def answer(fetch, url, headers):
            status, body = original(fetch, url, headers)
            if "valorant_news_page_list" in url and "tags=" not in url:
                news = json.loads(body)
                news["data"] = [item for item in news["data"] if isinstance(item, dict) and not item.get("tags")]
                body = json.dumps(news).encode()
            return status, body

        with mock.patch.object(FakeFetch, "__call__", answer):
            text = self.page("/news")
        self.assertIn("いまのパッチ", text)
        self.assertIn("VALORANT パッチノート 11.10", text)
        self.assertTrue(any("tags=patch_notes" in url for url, _ in self.fetch.asked))

    def failing(self, *parts: str, status: int = 400):
        """HenrikDev answers ``status`` for the addresses that hold any of ``parts``."""
        original = FakeFetch.__call__

        def answer(fetch, url, headers):
            if any(part in url for part in parts):
                fetch.asked.append((url, headers))
                return status, b'{"errors": []}'
            return original(fetch, url, headers)

        patch = mock.patch.object(FakeFetch, "__call__", answer)
        patch.start()
        self.addCleanup(patch.stop)

    def test_news_in_english_when_the_language_has_none(self):
        self.fetch.riot_web_status = 503
        self.failing("/v1/website/zh-tw")
        self.client.set_cookie("lang", "zh-TW")
        text = self.page("/news")
        self.assertNotIn('class="problem"', text)
        self.assertIn('class="note"', text)
        self.assertTrue(any(url.endswith("/v1/website/en-gb") for url, _ in self.fetch.asked))
        asked = len(self.fetch.asked)
        self.page("/news")
        self.assertEqual(len(self.fetch.asked), asked, "neither is asked again for a while")

    def test_esports_league_by_league_when_the_whole_schedule_fails(self):
        self.failing("/v1/esports/schedule", status=500)
        text = self.page("/esports", 503)
        self.assertIn("eスポーツの日程を読み込めませんでした", text)
        self.assertIn('href="https://valorantesports.com/"', text)
        self.assertNotIn("HTTP 500", text, "what came back is for the owner")
        asked = len(self.fetch.asked)
        self.assertIn("HTTP 500", self.client.get("/esports").get_data(as_text=True))
        self.assertEqual(len(self.fetch.asked), asked, "nothing asked again for a while: each visit cost 7 calls")

    def test_esports_the_last_schedule_when_a_new_one_does_not_come(self):
        self.assertIn("Paper Rex", self.page("/esports"))
        henrik = self.app.config["VAL_HENRIK"]
        henrik.cache.put(("esports",), None, -1)  # the schedule is old: a new one is asked for
        henrik.budget = Budget(0, 60)  # and the allowance is used up
        text = self.page("/esports")
        self.assertIn("Paper Rex", text, "the last schedule rather than 混み合っています")
        self.assertIn("に読み込んだものを出しています", text)
        self.assertNotIn('class="problem"', text)
        henrik.budget = Budget(25, 60)
        asked = len(self.fetch.asked)
        self.page("/esports")
        self.assertEqual(len(self.fetch.asked), asked, "not asked again for a while after a failure")

    def test_esports_busy_without_a_last_schedule(self):
        self.app.config["VAL_HENRIK"].budget = Budget(0, 60)
        text = self.page("/esports", 503)
        self.assertIn('href="https://valorantesports.com/"', text, "the official schedule instead")

    def test_esports_from_the_leagues(self):
        whole = self.page("/esports")
        self.app.config["VAL_HENRIK"].cache = type(self.app.config["VAL_HENRIK"].cache)(512)
        original = FakeFetch.__call__

        def answer(fetch, url, headers):
            if url.endswith("/v1/esports/schedule"):
                fetch.asked.append((url, headers))
                return 0, b""  # no answer in time
            return original(fetch, url, headers)

        with mock.patch.object(FakeFetch, "__call__", answer):
            text = self.page("/esports")
        self.assertIn("Paper Rex", text)
        self.assertEqual(text.count("Paper Rex"), whole.count("Paper Rex"), "each match once")
        self.assertTrue(any("league=vct_pacific" in url for url, _ in self.fetch.asked))

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
