"""The tools: the leaderboard, the server status, crosshair codes and the store."""

import os
import re
import unittest
from unittest import mock

from helpers import make_app

from valtracker import crosshair

VISITOR = {"homepage.visitor": True}


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200, **kwargs) -> str:
        answer = self.client.get(path, **kwargs)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def test_tools_page_leads_to_each(self):
        text = self.page("/tools", environ_base=VISITOR)
        for path in ("/crosshair", "/status", "/leaderboard", "/shop"):
            self.assertIn(f'href="{path}"', text)
        self.assertNotIn('href="/settings"', text)

    # -- the leaderboard ------------------------------------------------------------------------------

    def test_leaderboard_comes_later_then_at_once(self):
        text = self.page("/leaderboard")
        self.assertIn('data-load="/leaderboard/part?region=ap"', text, "Japanese visitors start in Asia Pacific")
        self.assertEqual(self.fetch.henrik_calls(), [])
        part = self.page("/leaderboard/part?region=ap")
        self.assertIn("/v3/leaderboard/ap/pc?size=50&start_index=0", self.fetch.henrik_calls()[0])
        self.assertIn('href="/p/TopFrag/JP1"', part)
        self.assertIn("1,234", part)
        self.assertIn("非公開のプレイヤー", part)
        self.assertIn("620 RR から", part, "Radiant's threshold")
        self.assertIn("501 位から", part, "Immortal 3 starts after the Radiants")
        text = self.page("/leaderboard")
        self.assertNotIn("data-load", text, "kept: shown at once")
        self.assertIn("TopFrag", text)

    def test_leaderboard_region_by_language(self):
        self.client.set_cookie("lang", "ko")
        self.assertIn('data-load="/leaderboard/part?region=kr"', self.page("/leaderboard"))
        self.assertIn("리더보드", self.page("/leaderboard"))
        self.assertIn('data-load="/leaderboard/part?region=eu"', self.page("/leaderboard?region=eu"))
        self.assertIn('data-load="/leaderboard/part?region=kr"', self.page("/leaderboard?region=xx"))

    def test_leaderboard_pages(self):
        self.page("/leaderboard/part?region=na&page=3")
        self.assertIn("/v3/leaderboard/na/pc?size=50&start_index=100", self.fetch.henrik_calls()[0])
        self.page("/leaderboard/part?region=na&page=999")
        self.assertIn("start_index=9950", self.fetch.henrik_calls()[1], "at most 200 pages")
        self.page("/leaderboard/part?page=x")

    def test_leaderboard_without_a_key(self):
        self.app.config["VAL_SETTINGS"].save(henrik_key="")
        text = self.page("/leaderboard", 503, environ_base=VISITOR)
        self.assertIn("準備中", text)

    # -- the server status ----------------------------------------------------------------------------

    def test_status_in_the_visitors_language(self):
        text = self.page("/status")
        self.assertIn("定期メンテナンス", text)
        self.assertIn("メンテナンスのためサーバーを停止しています。", text, "the latest update")
        self.assertIn("実施中", text)
        self.assertNotIn("Console sign-in issue", text, "a console-only notice is not the PC game's")
        self.client.set_cookie("lang", "ko")
        self.assertIn("Scheduled maintenance", self.page("/status?region=ap"), "English when Riot wrote no Korean")

    def test_top_page_notice(self):
        self.assertIn('data-load="/status/notice"', self.page("/"))
        notice = self.page("/status/notice")
        self.assertIn('href="/status?region=ap"', notice)
        self.assertIn("定期メンテナンス", notice)
        app, _, _ = make_app(key="")
        self.addCleanup(app._test_folder.cleanup)
        notice = app.test_client().get("/status/notice").get_data(as_text=True)
        self.assertIn("定期メンテナンス", notice, "Riot's own file needs no HenrikDev key")
        self.assertFalse(any("/valorant/v1/status/" in url for url, _ in self.fetch.asked))

    def test_status_from_henrikdev_when_riot_does_not_answer(self):
        self.fetch.riot_web_status = 503
        self.assertIn("定期メンテナンス", self.page("/status"))
        self.assertTrue(any("/valorant/v1/status/ap" in url for url, _ in self.fetch.asked))
        self.fetch.henrik_status = 503
        self.assertIn('class="problem"', self.page("/status?region=eu", 503))
        self.assertEqual(self.page("/status/notice?region=eu").strip(), "", "nothing to say when neither answers")

    def test_no_notices(self):
        found = {"maintenances": [], "incidents": []}
        with mock.patch.object(self.app.config["VAL_RIOT_WEB"], "status", return_value=found):
            self.assertIn("いまメンテナンスも障害もありません", self.page("/status?region=eu"))
            self.assertEqual(self.page("/status/notice").strip(), "")

    # -- crosshairs -----------------------------------------------------------------------------------

    def test_crosshair_code(self):
        text = self.page("/crosshair?code=0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0")
        self.assertIn('fill="#00ffff"', text, "cyan")
        self.assertIn("シアン", text)
        self.assertIn('data-copy="0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0"', text)
        self.assertNotIn('style="', text)
        self.assertIn('id="xh-save" data-code="0;P;c;5;h;0;0l;4;0o;2;0a;1;1b;0"', text, "the one shown can be kept")
        self.assertIn('data-copy="http://localhost/crosshair?code=0;P;c;5;h;0', text, "and its link sent")
        self.assertIn('id="xh-saved"', text)

    def test_bad_crosshair_code(self):
        text = self.page("/crosshair?code=hello%20world", 400)
        self.assertIn("クロスヘアのコードではありませんでした", text)
        self.assertNotIn('id="xh-save"', text, "nothing to keep")
        self.assertIn('id="xh-saved"', self.page("/crosshair"), "the ones kept are listed on every visit")

    def test_parse(self):
        found = crosshair.parse("0;s;1;P;c;8;u;FF8800FF;h;0;d;1;z;3;0l;0;1b;0;zz;9;A;c;1;S;c;0;d;1")
        self.assertEqual(crosshair.colour(found["primary"]), "#ff8800")
        self.assertEqual(found["unknown"], ["P:zz"])
        self.assertIsNotNone(found["ads"], "an A section: the ADS crosshair is its own")
        self.assertEqual(crosshair.colour(found["ads"]), "#00ff00")
        self.assertEqual(crosshair.colour(found["sniper"]), "#ffffff")
        boxes = crosshair.shapes(found["primary"])
        self.assertEqual(len(boxes), 1, "only the dot: no outlines, no lines")
        self.assertEqual((boxes[0]["w"], boxes[0]["h"]), (3, 3))
        self.assertIsNone(crosshair.parse("0;P;c"), "a key without its value")
        self.assertIsNone(crosshair.parse("<script>"))
        self.assertIsNone(crosshair.parse("P;c;1"), "no version")
        default = crosshair.parse("0")
        self.assertIsNone(default["ads"])
        self.assertEqual(len(crosshair.shapes(default["primary"])), 16, "8 lines and their outlines")

    def test_presets_read_back(self):
        for name, settings in crosshair.PRESETS.items():
            with self.subTest(name=name):
                code = crosshair.code(crosshair.PRIMARY | settings)
                self.assertEqual(crosshair.parse(code)["primary"], crosshair.PRIMARY | settings)

    # -- the store ------------------------------------------------------------------------------------

    def test_featured_for_everyone(self):
        text = self.page("/shop", environ_base=VISITOR)
        self.assertIn("プライム", text, "the bundle's name from valorant-api.com")
        self.assertIn("プライム ヴァンダル", text, "a skin in the visitor's language")
        self.assertIn("New Spray", text, "HenrikDev's name when valorant-api.com has none yet")
        self.assertIn("2,000", text)
        self.assertIn("<s>1,775</s>", text, "the price before the discount")
        self.assertIn("残り 5 日", text)
        self.assertRegex(text, r'data-until="\d+"')
        self.assertNotIn("今日のスキン", text)
        self.assertNotIn('href="/collection"', text)

    def test_owner_before_linking(self):
        text = self.page("/shop")
        self.assertIn("まだ連携されていません", text)
        self.assertIn("注目のバンドル", text, "the featured bundles, as everyone sees them")

    def test_owner_store(self):
        page = self.page("/settings")
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.client.post("/settings", data={"csrf": token, "do": "riot", "riot": "ssid-1"})
        text = self.page("/shop")
        posted = [(method, url) for method, url, _, _ in self.app.riot.asked if "/storefront/" in url]
        self.assertEqual(posted[0][0], "POST")
        self.assertIn("Owner さんの今日のスキン", text)
        self.assertIn("レイヴァー ナイフ", text)
        self.assertIn("3,550", text)
        self.assertIn("残り 5 時間 3 分", text)
        self.assertIn("ナイトマーケット", text)
        self.assertIn("−40%", text)
        self.assertIn("<s>1,775</s> <strong>1,065</strong>", text)
        self.assertIn("1,900", text, "the bundle's price")
        self.assertIn("ルーキー", text, "a title among the accessories")
        self.assertIn("4,000", text)
        self.assertIn("所持", text, "what is owned is marked")
        self.assertNotIn("注目のバンドル", text, "the owner's own bundles instead")
        self.assertIn("1,250", text, "the wallet")


if __name__ == "__main__":
    unittest.main()
