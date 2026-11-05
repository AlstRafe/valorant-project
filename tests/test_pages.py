import base64
import hashlib
import os
import re
import unittest
from unittest import mock

from helpers import make_app

from valtracker import theme

VISITOR = {"homepage.visitor": True}


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()

    def get(self, path, lang="ja", **kwargs):
        self.client.set_cookie("lang", lang)
        return self.client.get(path, **kwargs)

    def test_top_page(self):
        page = self.get("/")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn('<html lang="ja">', text)
        self.assertIn('id="favorites"', text)
        self.assertIn('href="?lang=ko"', text)
        self.assertIn('id="theme"', text)

    def test_every_language(self):
        for lang, words in {"ko": "플레이어 찾기", "en-GB": "Find a player", "zh-TW": "搜尋玩家"}.items():
            with self.subTest(lang=lang):
                text = self.get("/", lang=lang).get_data(as_text=True)
                self.assertIn(f'<html lang="{lang}">', text)
                self.assertIn(words, text)

    def test_lang_query_sets_the_cookie_for_the_whole_host(self):
        answer = self.client.get("/agents?lang=ko")
        self.assertEqual(answer.status_code, 303)
        self.assertTrue(answer.headers["Location"].endswith("/agents"))
        self.assertIn("lang=ko; Path=/", answer.headers["Set-Cookie"])

    def test_browser_language_without_a_cookie(self):
        text = self.client.get("/", headers={"Accept-Language": "zh-HK,zh;q=0.9"}).get_data(as_text=True)
        self.assertIn('<html lang="zh-TW">', text)

    def test_security_headers_and_the_theme_script_hash(self):
        answer = self.get("/")
        csp = answer.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertEqual(answer.headers["X-Frame-Options"], "DENY")
        self.assertEqual(answer.headers["X-Content-Type-Options"], "nosniff")
        scripts = re.findall(r"<script>(.*?)</script>", answer.get_data(as_text=True), re.S)
        self.assertEqual(len(scripts), 1, "the only inline script is the theme's")
        digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", csp)
        self.assertEqual(scripts[0], theme.THEME_SCRIPT)

    def test_no_outside_addresses_in_pages(self):
        for path in ("/", "/p/Tester/JP1", "/p/Tester/JP1?full=1", "/agents", f"/m/ap/{MATCH}"):
            with self.subTest(path=path):
                text = self.get(path).get_data(as_text=True)
                sources = re.findall(r'(?:src|href)="([^"]+)"', text)
                outside = [s for s in sources if s.startswith(("http:", "https:", "//"))]
                self.assertEqual(outside, [], "pictures and files come from this site")

    def test_link_previews_have_full_addresses(self):
        # LINE, X and Discord show no picture for a relative og:image (Home Page's server marks the public name https)
        text = self.client.get("/p/Tester/JP1?x=1", base_url="https://example.org/val").get_data(as_text=True)
        image = re.search(r'<meta property="og:image" content="([^"]+)"', text).group(1)
        self.assertRegex(image, r"^https://example\.org/val/static/og\.png\?v=\w+$")
        self.assertIn('<meta property="og:url" content="https://example.org/val/p/Tester/JP1">', text)

    def test_search_goes_to_the_player(self):
        answer = self.get("/search?q=Tester%EF%BC%83JP1")  # a full-width ＃ too
        self.assertEqual(answer.status_code, 303)
        self.assertTrue(answer.headers["Location"].endswith("/p/Tester/JP1"))

    def test_search_without_a_tag(self):
        answer = self.get("/search?q=Tester")
        self.assertEqual(answer.status_code, 400)
        self.assertIn("名前#タグ", answer.get_data(as_text=True))

    def test_player(self):
        answer = self.get("/p/Tester/JP1?full=1")
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertIn("Tier 21", text)  # the rank as valorant-api.com names it
        self.assertIn("アセント", text)  # the map too
        self.assertIn("13–9", text)
        self.assertIn("7–13", text)
        self.assertIn("ACS 255", text)  # 5600 / 22 rounds
        self.assertIn("50%", text)  # one win, one loss
        self.assertIn('data-name="Tester"', text)
        self.assertIn(f"/m/ap/{MATCH}", text)
        self.assertIn("Authorization", self.fetch.asked[0][1])

    def test_player_page_comes_at_once(self):
        # HenrikDev takes seconds: the page comes first, and its script asks for the rank and matches
        shell = self.get("/p/Tester/JP1")
        self.assertEqual(shell.status_code, 200)
        text = shell.get_data(as_text=True)
        self.assertIn('data-part="/p/Tester/JP1/part"', text)
        self.assertIn("ランクと最近の試合を読み込んでいます", text)
        self.assertIn('href="?full=1"', text)  # without JavaScript
        self.assertEqual(self.fetch.henrik_calls(), [])

        part = self.get("/p/Tester/JP1/part")
        self.assertEqual(part.status_code, 200)
        text = part.get_data(as_text=True)
        self.assertIn("Tier 21", text)
        self.assertIn('data-name="Tester"', text)
        self.assertNotIn("<html", text)
        self.assertEqual(part.headers["Cache-Control"], "no-store")
        self.assertRegex(part.headers["Server-Timing"], r"^henrik;dur=\d+$")
        self.assertEqual(len(self.fetch.henrik_calls()), 3)  # account, then rank and matches side by side

    def test_player_is_kept_a_while(self):
        self.get("/p/Tester/JP1/part")
        calls = len(self.fetch.henrik_calls())
        again = self.get("/p/tester/jp1").get_data(as_text=True)
        self.assertIn("Tier 21", again, "kept: the whole page at once")
        self.assertNotIn("data-part", again)
        self.assertEqual(len(self.fetch.henrik_calls()), calls)

    def test_player_not_found(self):
        self.fetch.henrik_status = 404
        self.assertEqual(self.get("/p/Nobody/0000").status_code, 200)  # the page, asking for the rest
        for path in ("/p/Nobody/0000/part", "/p/Nobody/0000?full=1"):
            with self.subTest(path=path):
                answer = self.get(path)
                self.assertEqual(answer.status_code, 404)
                self.assertIn("プレイヤーが見つかりませんでした", answer.get_data(as_text=True))

    def test_part_of_a_bad_riot_id(self):
        self.assertEqual(self.get("/p/a/b/part").status_code, 404)

    def test_no_key(self):
        self.app.config["VAL_SETTINGS"].save(henrik_key="")
        owner = self.get("/p/Tester/JP1")
        self.assertEqual(owner.status_code, 503)
        self.assertIn("API キーがまだ設定されていません", owner.get_data(as_text=True))
        visitor = self.get("/p/Tester/JP1", environ_base=VISITOR)
        self.assertIn("まだ準備中", visitor.get_data(as_text=True))
        self.assertNotIn("API キー", visitor.get_data(as_text=True))
        self.assertEqual(self.fetch.henrik_calls(), [])

    def test_names_are_escaped(self):
        text = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        self.assertIn("&lt;b&gt;Foe&lt;/b&gt;", text)
        self.assertNotIn("<b>Foe</b>", text)

    def test_match(self):
        self.get("/p/Tester/JP1/part")
        calls = len(self.fetch.henrik_calls())
        answer = self.get(f"/m/ap/{MATCH}")
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertIn("アセント", text)
        self.assertIn("レッド", text)
        self.assertIn("Mate", text)
        self.assertEqual(len(self.fetch.henrik_calls()), calls, "the match list had it already")

    def test_match_with_a_bad_id(self):
        self.assertEqual(self.get("/m/ap/not-an-id").status_code, 404)
        self.assertEqual(self.get(f"/m/xx/{MATCH}").status_code, 404)

    def test_agents(self):
        text = self.get("/agents").get_data(as_text=True)
        self.assertIn("ジェット", text)
        self.assertIn("ソーヴァ", text)
        page = self.get("/agents/11111111-1111-4111-8111-111111111111")
        self.assertEqual(page.status_code, 200)
        self.assertIn("ジェット の説明", page.get_data(as_text=True))
        self.assertEqual(self.get("/agents/nope").status_code, 404)

    def test_agents_in_korean_ask_for_korean(self):
        self.get("/agents", lang="ko")
        self.assertTrue(any("language=ko-KR" in url for url, _ in self.fetch.asked))

    def test_media(self):
        path = "/media/agents/11111111-1111-4111-8111-111111111111/displayicon.png"
        answer = self.get(path)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.mimetype, "image/png")
        answer.close()
        asked = len(self.fetch.asked)
        self.get(path).close()
        self.assertEqual(len(self.fetch.asked), asked, "kept on disk")
        for bad in ("/media/../settings.json", "/media/agents/x/displayicon.png", "/media/agents/a.png"):
            with self.subTest(path=bad):
                self.assertEqual(self.get(bad).status_code, 404)

    def test_not_found_page(self):
        answer = self.get("/nowhere", lang="en-GB", environ_base={"homepage.home": "/"})
        self.assertEqual(answer.status_code, 404)
        text = answer.get_data(as_text=True)
        self.assertIn("Page not found", text)
        self.assertIn("Back to all sites", text)

    def test_settings_are_the_owners(self):
        self.assertEqual(self.get("/settings").status_code, 200)
        self.assertEqual(self.get("/settings", environ_base=VISITOR).status_code, 404)
        self.assertNotIn('href="/settings"', self.get("/", environ_base=VISITOR).get_data(as_text=True))

    def test_settings_save_needs_the_token(self):
        refused = self.client.post("/settings", data={"henrik_key": "HDEV-new"})
        self.assertEqual(refused.status_code, 400)
        page = self.get("/settings").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        saved = self.client.post("/settings", data={"csrf": token, "henrik_key": "HDEV-new-key-1234", "do": "save"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.app.config["VAL_SETTINGS"].henrik_key(), "HDEV-new-key-1234")
        self.assertNotIn("HDEV-new-key-1234", saved.get_data(as_text=True))
        cross = self.client.post(
            "/settings", data={"csrf": token, "henrik_key": "evil"}, headers={"Sec-Fetch-Site": "cross-site"}
        )
        self.assertEqual(cross.status_code, 403)
        self.assertEqual(self.app.config["VAL_SETTINGS"].henrik_key(), "HDEV-new-key-1234")

    def test_session_cookie_is_this_sites_own(self):
        app, _, _ = make_app(mount="/val")
        self.addCleanup(app._test_folder.cleanup)
        client = app.test_client()
        answer = client.get("/settings")
        cookie = answer.headers.get("Set-Cookie", "")
        self.assertTrue(cookie.startswith("val_session="))
        self.assertIn("Path=/val/", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_static_files_at_their_version(self):
        text = self.get("/").get_data(as_text=True)
        css = re.search(r'href="(/static/css/app\.css\?v=[0-9a-f]+)"', text).group(1)
        answer = self.get(css)
        self.assertEqual(answer.status_code, 200)
        self.assertIn("immutable", answer.headers["Cache-Control"])
        answer.close()


MATCH = "aaaaaaaa-0000-4000-8000-000000000001"

if __name__ == "__main__":
    unittest.main()
