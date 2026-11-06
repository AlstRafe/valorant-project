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
        text = answer.get_data(as_text=True)
        self.assertIn("タグ（# のあと）も入れてください。例: Tester#JP1", text)  # what is missing, with their name
        self.assertIn('href="/help#riot-id"', text)
        self.assertIn('id="same-name" data-name="Tester"', text)  # the kept players of that name (app.js)
        self.assertNotIn('class="guess"', text)
        # a space where the # goes: the likely Riot ID is offered, not opened (a name may hold spaces)
        text = self.get("/search?q=Tester+JP1").get_data(as_text=True)
        self.assertIn('<a class="button quiet" href="/p/Tester/JP1">「Tester#JP1」を探す</a>', text)
        self.assertIn("例: Tester#JP1", text)
        # not a name at all: the general words
        text = self.get("/search?q=%3Cb%3E").get_data(as_text=True)
        self.assertIn("「名前#タグ」の形で入れてください", text)
        self.assertNotIn('id="same-name"', text)
        self.assertIn('list="known"', self.get("/").get_data(as_text=True))  # the kept players suggested

    def test_help_page(self):
        for lang, words in {"ja": "使い方・よくある質問", "ko": "자주 묻는 질문", "en-GB": "Help and FAQ"}.items():
            with self.subTest(lang=lang):
                answer = self.get("/help", lang=lang)
                self.assertEqual(answer.status_code, 200)
                text = answer.get_data(as_text=True)
                self.assertIn(words, text)
                self.assertIn('id="riot-id"', text)
                self.assertIn('<details class="glossary" open>', text)
                self.assertEqual(text.count("<dt>"), 13, "every word of the numbers")
        self.assertIn('href="/help"', self.get("/agents").get_data(as_text=True))  # from every page's foot

    def test_the_numbers_explained_where_they_are(self):
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        glossary = text[text.index('<details class="glossary">') : text.index("</details>", text.index("glossary"))]
        self.assertIn("<dt>ACS</dt>", glossary)
        self.assertIn("<dt>RR</dt>", glossary)
        self.assertNotIn("<dt>+/−</dt>", glossary, "the player page has no +/− column")
        match = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        self.assertIn("<dt>+/−</dt>", match)
        self.assertNotIn("<dt>RR</dt>", match)

    def test_when_the_numbers_were_read_and_reading_again(self):
        os.environ["VAL_HENRIK_KEY"] = "HDEV-test"
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        self.assertIn('<span class="read-at" id="read-at">いま読んだデータ</span>', text)  # no press for a minute
        self.assertNotIn('class="refresh"', text)
        calls = len(self.fetch.henrik_calls())
        henrik = self.app.config["VAL_HENRIK"]
        for key in henrik.player_keys("ap", "Tester", "JP1"):  # read three minutes ago
            at = henrik.read_times.get(key)
            if at:
                henrik.read_times.put(key, at - 180, 60)
        text = self.get("/p/Tester/JP1").get_data(as_text=True)  # at once: still kept
        self.assertIn("3 分前のデータ", text)
        self.assertIn('href="/p/Tester/JP1?fresh=1&amp;full=1"', text)  # without JavaScript, the whole page
        self.assertIn('data-part="/p/Tester/JP1/part?fresh=1"', text)
        self.assertEqual(len(self.fetch.henrik_calls()), calls, "nothing asked again yet")

        shell = self.get("/p/Tester/JP1?fresh=1").get_data(as_text=True)  # pressed: the page asks again
        self.assertIn('data-part="/p/Tester/JP1/part?fresh=1"', shell)
        part = self.get("/p/Tester/JP1/part?fresh=1").get_data(as_text=True)
        self.assertIn("いま読んだデータ", part)
        self.assertEqual(len(self.fetch.henrik_calls()), calls + 4, "the rank, the RR and both parts of the matches")
        self.get("/p/Tester/JP1/part?fresh=1")  # pressed again at once: a minute must pass
        self.assertEqual(len(self.fetch.henrik_calls()), calls + 4)

    def test_reading_again_keeps_the_numbers_when_henrikdev_is_busy(self):
        os.environ["VAL_HENRIK_KEY"] = "HDEV-test"
        self.get("/p/Tester/JP1?full=1")
        henrik = self.app.config["VAL_HENRIK"]
        for key in henrik.player_keys("ap", "Tester", "JP1"):
            at = henrik.read_times.get(key)
            if at:
                henrik.read_times.put(key, at - 120, 60)
        self.fetch.henrik_status = 429
        answer = self.get("/p/Tester/JP1/part?fresh=1")
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertIn("Tier 21", text)  # what it showed before
        self.assertIn("2 分前のデータ", text)

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
        self.assertIn('href="/p/Tester/JP1?full=1"', text)  # without JavaScript
        self.assertEqual(self.fetch.henrik_calls(), [])

        part = self.get("/p/Tester/JP1/part")
        self.assertEqual(part.status_code, 200)
        text = part.get_data(as_text=True)
        self.assertIn("Tier 21", text)
        self.assertIn('data-name="Tester"', text)
        self.assertNotIn("<html", text)
        self.assertEqual(part.headers["Cache-Control"], "no-store")
        self.assertRegex(part.headers["Server-Timing"], r"^henrik;dur=\d+$")
        # the account, then side by side the rank, each match's RR and the matches (15: in two parts, as an answer
        # gives 10 at most)
        self.assertEqual(len(self.fetch.henrik_calls()), 5)

    def test_what_a_player_page_tells(self):
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        self.assertIn('<progress class="rr-bar" max="100" value="42" aria-label="RR 42 / 100"></progress>', text)
        self.assertIn('<option value="e9a3">E9 A3</option>', text)  # the act rank: the latest act first
        self.assertIn('<div class="act" data-act="e9a3">', text)
        self.assertIn('<div class="act" data-act="e9a2" hidden>', text)
        self.assertIn("<span>60%</span>", text)  # 12 of 20
        # the badge: the 9 best wins from the top, triangles pointing up and down in turn
        badge = text[text.index('data-act="e9a3"') : text.index('data-act="e9a2"')]
        triangles = re.findall(r"competitivetiers/[^/]+/(\d+)/ranktriangle(up|down)icon", badge)
        self.assertEqual(
            triangles,
            [
                ("22", "up"),
                ("21", "up"),
                ("21", "down"),
                ("21", "up"),
                ("20", "up"),
                ("20", "down"),
                ("20", "up"),
                ("19", "down"),
                ("19", "up"),
            ],
        )
        old = text[text.index('data-act="e4a3"') :]
        old = old[: old.index("</dl>")]
        self.assertIn("Old 21", old, "Episode 4's 21 was Immortal 1")
        self.assertEqual(old.count(' won"'), 5, "wins without a picture still fill their place")
        self.assertEqual(old.count('"></span>') - old.count(' won"'), 4, "5 wins: 4 places left")
        self.assertIn('<span class="up">+18 RR</span>', text)  # each competitive match's RR
        self.assertIn('<span class="down">-15 RR</span>', text)
        self.assertIn('<span class="mvp match" title="マッチ MVP">MVP', text)  # the best combat score of the match
        self.assertEqual(text.count('class="mvp '), 1)
        self.assertIn('<li class="win">勝</li><li class="loss">敗</li>', text)  # newest first
        self.assertIn("ADR 143", text)  # (3000 / 22 -> 136, 3000 / 20 -> 150) / 2
        self.assertIn(f"/m/ap/{MATCH}?me=Tester%23JP1", text)
        self.assertNotIn("デスマッチなどは除く", text)
        self.assertIn("勝利", text)  # the game's words
        self.assertIn("敗北", text)
        self.assertNotIn("勝ち", text)

        english = self.get("/p/Tester/JP1?full=1", lang="en-GB").get_data(as_text=True)
        self.assertIn("E9 A3 act rank: the ranks of the best 9 wins", english)
        self.assertIn("Victory", english)
        self.assertIn('<li class="win">W</li><li class="loss">L</li>', english)

    def test_a_player_page_in_depth(self):
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        self.assertIn("Tier 22 まであと 58 RR · 平均 +18 なら約 4 勝", text)  # 42 RR of 100
        self.assertIn('<path class="line" d="M14.0,93.3 L786.0,56.7"/>', text)  # the RR trend: two competitive matches
        self.assertIn("+3 RR", text)  # +18 - 15
        self.assertIn("<dd>59%", text)  # KAST over the match told round by round
        self.assertIn("13 / 1", text)  # first kills / deaths
        self.assertIn("最高 1v1", text)
        self.assertIn("1時間20分", text)  # two matches of 40 minutes
        self.assertIn('class="versus"', text)
        self.assertIn("強み", text)
        self.assertIn("頭 25%・体 62%・脚 13%", text)
        self.assertIn("攻撃", text)
        self.assertIn('id="ag-title"', text)
        self.assertIn('id="map-title"', text)
        self.assertIn("ヴァンダル", text)  # the weapons by valorant-api.com's name
        self.assertIn("/media/weapons/9c82e19d-4575-0200-1a81-3eacf00cf872/killstreamicon.png", text)
        self.assertIn('href="/p/Mate/0001"', text)  # the party
        self.assertIn('data-rank="Tier 21"', text)  # kept with the favourites
        self.assertIn('data-day="{wins}勝 {losses}敗"', text)
        self.assertIn('data-outcome="win" data-rr="18"', text)

        english = self.get("/p/Tester/JP1?full=1", lang="en-GB").get_data(as_text=True)
        self.assertIn("58 RR to Tier 22", english)
        self.assertIn("Against the same lobbies", english)

    def test_rr_is_asked_only_where_it_shows(self):
        self.get("/p/Tester/JP1/part?mode=unrated")
        self.assertFalse([url for url in self.fetch.henrik_calls() if "mmr-history" in url])
        self.get("/p/Tester/JP1/part?mode=competitive")
        self.assertTrue([url for url in self.fetch.henrik_calls() if "mmr-history" in url])

    def test_the_rr_is_not_needed(self):
        real = self.fetch.__call__

        def without_history(url, headers):
            return (404, b"{}") if "mmr-history" in url else real(url, headers)

        self.app.config["VAL_HENRIK"].fetch = without_history
        text = self.get("/p/Tester/JP1/part").get_data(as_text=True)
        self.assertIn("13–9", text)
        self.assertNotIn(" RR</span>", text)

    def test_matches_of_one_mode(self):
        text = self.get("/p/Tester/JP1?mode=competitive").get_data(as_text=True)
        self.assertIn('data-part="/p/Tester/JP1/part?mode=competitive"', text)
        self.assertIn('href="/p/Tester/JP1?mode=competitive&amp;full=1"', text)

        text = self.get("/p/Tester/JP1/part?mode=competitive").get_data(as_text=True)
        self.assertIn('<a href="/p/Tester/JP1?mode=competitive" aria-current="true">コンペティティブ</a>', text)
        self.assertIn('<a href="/p/Tester/JP1">すべて</a>', text)
        self.assertIn('<a href="/p/Tester/JP1?mode=teamdeathmatch">チームデスマッチ</a>', text)
        self.assertIn("13–9", text)
        self.assertTrue(all("mode=competitive" in url for url in self.fetch.henrik_calls() if "/v4/matches/" in url))

        kept = self.get("/p/Tester/JP1?mode=competitive").get_data(as_text=True)
        self.assertNotIn("data-part", kept, "kept: the whole page at once")
        self.assertIn("data-part", self.get("/p/Tester/JP1").get_data(as_text=True), "every mode is another list")

        none = self.get("/p/Tester/JP1/part?mode=unrated").get_data(as_text=True)  # the fixture has only competitive
        self.assertIn("このモードの最近の試合はありません。", none)
        bogus = self.get("/p/Tester/JP1/part?mode=bogus").get_data(as_text=True)
        self.assertIn('<a href="/p/Tester/JP1" aria-current="true">すべて</a>', bogus)

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

    def test_a_match_round_by_round(self):
        text = self.get(f"/m/ap/{MATCH}?me=Tester%23JP1").get_data(as_text=True)
        self.assertIn('href="/p/Tester/JP1">← Tester#JP1 の戦績', text)
        self.assertIn("平均ランク", text)
        self.assertEqual(text.count('<li class="win'), 13)  # Tester's team won 13 rounds
        self.assertIn('<li class="win turn"', text)  # the second half begins at round 13
        self.assertIn('<use href="#end-detonate"/>', text)
        self.assertIn('<span class="cer">CLUTCH</span>', text)
        self.assertIn("レッド: エコ", text)  # 800 a player in a pistol round
        self.assertIn('<details class="round-feed win" id="r1">', text)
        self.assertIn("スパイク爆発 · A サイト", text)
        self.assertIn("0:10", text)
        self.assertIn("+ Mate", text)  # the assist
        self.assertIn(
            '<th scope="col"><abbr title="キル・アシスト・生存・トレードされたラウンドの割合">KAST</abbr></th>', text
        )
        self.assertIn('<span class="party p1" title="パーティ 1">1</span>', text)
        self.assertEqual(text.count('class="party '), 2, "only parties of two or more")
        self.assertIn("Tester の対面成績", text)
        self.assertIn('<span class="up" title="倒した">12</span>', text)  # Tester on Foe
        # without the player the visitor came from: the rounds by team colour, no duels
        plain = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        self.assertIn('<li class="red"', plain)
        self.assertNotIn("対面成績", plain)

    def test_a_match_without_its_rounds(self):
        text = self.get("/m/ap/aaaaaaaa-0000-4000-8000-000000000002").get_data(as_text=True)
        self.assertIn("7–13", self.get("/p/Tester/JP1?full=1").get_data(as_text=True))
        self.assertNotIn("ラウンドの流れ", text)
        self.assertNotIn("KAST", text)
        self.assertIn("DDΔ", text)  # from the match's own numbers

    def test_the_players_own_line_stands_out(self):
        text = self.get(f"/m/ap/{MATCH}?me=tester%23jp1").get_data(as_text=True)
        self.assertEqual(text.count('<tr class="me">'), 1)
        self.assertNotIn('class="me"', self.get(f"/m/ap/{MATCH}").get_data(as_text=True))

    def test_a_deathmatch(self):
        henrik = self.app.config["VAL_HENRIK"]
        match = henrik.match("ap", MATCH)
        deathmatch = dict(match, teams=[], players=[dict(p, team_id=p["puuid"]) for p in match["players"]])
        deathmatch["metadata"] = dict(match["metadata"], queue={"id": "deathmatch", "mode_type": "Deathmatch"})
        henrik.cache.put(("match", MATCH), deathmatch, 60)
        text = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        self.assertIn("順位", text)
        self.assertNotIn('<th scope="col">ACS</th>', text)
        self.assertLess(text.index("Tester"), text.index("Foe"), "by kills: 20 before 18")

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

    def test_media_of_every_shape_the_pages_use(self):
        """valorant-api.com's pictures nest differently: a role's icon and an ability's were refused before."""
        agent = "11111111-1111-4111-8111-111111111111"
        for path in (
            "agents/roles/dbe8757e-9e92-4ed4-b39f-9dfc589691d4/displayicon.png",
            f"agents/{agent}/abilities/ability1/displayicon.png",
            f"agents/{agent}/abilities/ultimate/displayicon.png",
            f"competitivetiers/{agent}/21/smallicon.png",
            f"bundles/{agent}/displayicon2.png",
        ):
            with self.subTest(path=path):
                answer = self.get("/media/" + path)
                self.assertEqual(answer.status_code, 200)
                answer.close()

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
