import base64
import hashlib
import json
import os
import re
import unittest
from unittest import mock

import helpers
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
        for lang, words in {"ko": "플레이어 검색", "en-GB": "Find a player", "zh-TW": "搜尋玩家"}.items():
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
        scripts = re.findall(r"<script>(.*?)</script>", answer.get_data(as_text=True), re.DOTALL)
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
        # LINE、X、Discord は相対の og:image では絵を出さない（Home Page のサーバーは公開名に https の印を付ける）
        text = self.client.get("/p/Tester/JP1?x=1", base_url="https://example.org/val").get_data(as_text=True)
        image = re.search(r'<meta property="og:image" content="([^"]+)"', text).group(1)
        self.assertRegex(image, r"^https://example\.org/val/static/og\.png\?v=\w+$")
        self.assertIn('<meta property="og:url" content="https://example.org/val/p/Tester/JP1">', text)

    def test_search_goes_to_the_player(self):
        answer = self.get("/search?q=Tester%EF%BC%83JP1")  # 全角の ＃ も
        self.assertEqual(answer.status_code, 303)
        self.assertTrue(answer.headers["Location"].endswith("/p/Tester/JP1"))

    def test_search_without_a_tag(self):
        answer = self.get("/search?q=Tester")
        self.assertEqual(answer.status_code, 400)
        text = answer.get_data(as_text=True)
        self.assertIn("タグも入力してください。例: Tester#JP1", text)  # 足りないものを、名前と一緒に
        self.assertIn('href="/help#riot-id"', text)
        self.assertIn('id="same-name" data-name="Tester"', text)  # その名前で保存したプレイヤー（app.js）
        self.assertNotIn('class="guess"', text)
        # # の位置に空白: Riot ID らしいものを候補に出すが開かない（名前に空白が入ることもある）
        text = self.get("/search?q=Tester+JP1").get_data(as_text=True)
        self.assertIn('<a class="button quiet" href="/p/Tester/JP1">「Tester#JP1」で検索</a>', text)
        self.assertIn("例: Tester#JP1", text)
        # 名前ではない: 一般的な文
        text = self.get("/search?q=%3Cb%3E").get_data(as_text=True)
        self.assertIn("「名前#タグ」の形式で入力してください", text)
        self.assertNotIn('id="same-name"', text)
        self.assertIn('list="known"', self.get("/").get_data(as_text=True))  # 保存したプレイヤーを候補に

    def test_help_page(self):
        for lang, words in {"ja": "ヘルプ", "ko": "도움말", "en-GB": "Help"}.items():
            with self.subTest(lang=lang):
                answer = self.get("/help", lang=lang)
                self.assertEqual(answer.status_code, 200)
                text = answer.get_data(as_text=True)
                self.assertIn(words, text)
                self.assertIn('id="riot-id"', text)
                self.assertIn('<details class="glossary" open>', text)
                self.assertEqual(text.count("<dt>"), 18, "every word of the numbers")
        self.assertIn('href="/help"', self.get("/agents").get_data(as_text=True))  # 全ページの下から

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
        self.assertIn('<span class="read-at" id="read-at">たった今更新</span>', text)  # 1 分間押していない
        self.assertNotIn('class="refresh"', text)
        calls = len(self.fetch.henrik_calls())
        henrik = self.app.config["VAL_HENRIK"]
        for key in henrik.player_keys("ap", "Tester", "JP1"):  # 3 分前に読んだ
            at = henrik.read_times.get(key)
            if at:
                henrik.read_times.put(key, at - 180, 60)
        text = self.get("/p/Tester/JP1").get_data(as_text=True)  # すぐ: まだ保持
        self.assertIn("3 分前に更新", text)
        self.assertIn('href="/p/Tester/JP1?fresh=1&amp;full=1"', text)  # JavaScript 無し: ページまるごと
        self.assertIn('data-part="/p/Tester/JP1/part?fresh=1"', text)
        self.assertEqual(len(self.fetch.henrik_calls()), calls, "nothing asked again yet")

        shell = self.get("/p/Tester/JP1?fresh=1").get_data(as_text=True)  # 押した: ページが聞き直す
        self.assertIn('data-part="/p/Tester/JP1/part?fresh=1"', shell)
        part = self.get("/p/Tester/JP1/part?fresh=1").get_data(as_text=True)
        self.assertIn("たった今更新", part)
        self.assertEqual(len(self.fetch.henrik_calls()), calls + 4, "the rank, the RR and both parts of the matches")
        self.get("/p/Tester/JP1/part?fresh=1")  # すぐまた押した: 1 分待つ必要がある
        self.assertEqual(len(self.fetch.henrik_calls()), calls + 4)

    def test_the_last_answer_when_the_stats_server_does_not_answer(self):
        """HenrikDev は時々 502・503 を返す: もう 1 回聞き、それでもだめならエラーではなくその日の最後の答えを出す。"""
        self.assertIn("Tester", self.get("/p/Tester/JP1?full=1").get_data(as_text=True))
        henrik = self.app.config["VAL_HENRIK"]
        for key in henrik.player_keys("ap", "Tester", "JP1", ""):
            henrik.cache.drop(key)  # 読んだものが古い
        self.fetch.henrik_status = 503
        asked = len(self.fetch.asked)
        answer = self.get("/p/Tester/JP1?full=1")
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertNotIn("応答しません", text)
        self.assertIn("Tester", text)
        urls = [url for url, _ in self.fetch.asked[asked:] if "henrikdev" in url]
        self.assertTrue(urls and all(urls.count(url) == 2 for url in urls), "each call once more, no more")

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
        self.assertIn("Tier 21", text)  # 前に出したもの
        self.assertIn("2 分前に更新", text)

    def test_player(self):
        answer = self.get("/p/Tester/JP1?full=1")
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertIn("Tier 21", text)  # valorant-api.com の名前のランク
        self.assertIn("アセント", text)  # マップも
        self.assertIn("13–9", text)
        self.assertIn("7–13", text)
        self.assertIn("<small>ACS</small><strong>255</strong>", text)  # 5600 / 22 ラウンド
        self.assertIn("50%", text)  # 1 勝 1 敗
        self.assertIn('data-name="Tester"', text)
        self.assertIn(f"/m/ap/{MATCH}", text)
        self.assertIn("Authorization", self.fetch.asked[0][1])

    def test_player_page_comes_at_once(self):
        # HenrikDev は数秒かかる: ページが先に来て、そのスクリプトがランクと試合を頼む
        shell = self.get("/p/Tester/JP1")
        self.assertEqual(shell.status_code, 200)
        text = shell.get_data(as_text=True)
        self.assertIn('data-part="/p/Tester/JP1/part"', text)
        self.assertIn("戦績を読み込み中", text)
        self.assertIn('href="/p/Tester/JP1?full=1"', text)  # JavaScript 無し
        self.assertEqual(self.fetch.henrik_calls(), [])

        part = self.get("/p/Tester/JP1/part")
        self.assertEqual(part.status_code, 200)
        text = part.get_data(as_text=True)
        self.assertIn("Tier 21", text)
        self.assertIn('data-name="Tester"', text)
        self.assertNotIn("<html", text)
        self.assertEqual(part.headers["Cache-Control"], "no-store")
        self.assertRegex(part.headers["Server-Timing"], r"^henrik;dur=\d+$")
        # アカウント、それから同時にランク、各試合の RR、試合（15 試合: 1 回の答えは最大 10 なので 2 回に分ける）
        self.assertEqual(len(self.fetch.henrik_calls()), 5)

    def test_what_a_player_page_tells(self):
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        self.assertIn('<progress class="rr-bar" max="100" value="42" aria-label="RR 42 / 100"></progress>', text)
        self.assertIn('<option value="e9a3">E9 A3</option>', text)  # アクトランク: 最新のアクトから
        self.assertIn('<div class="act" data-act="e9a3">', text)
        self.assertIn('<div class="act" data-act="e9a2" hidden>', text)
        self.assertIn("<span>60%</span>", text)  # 20 のうち 12
        # バッジ: 上から良い勝利 9 つ、三角は上向きと下向きを交互に
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
        self.assertIn('<span class="up">+18 RR</span>', text)  # コンペティティブの各試合の RR
        self.assertIn('<span class="down">-15 RR</span>', text)
        # サブ垢の推定: レベルの横に %、根拠はページの中（足すと確率）
        self.assertRegex(
            text,
            r'<a class="smurf (main|maybe|likely) sure-(high|mid|low)" href="#smurf" title="サブ垢の確率 \d+%（推定）',
        )
        self.assertIn('<div class="smurf-card" id="smurf">', text)
        self.assertIn("出発点: ランク戦をする人の約 1 割がサブ垢という見当", text)
        # 各試合の行: ランク（絵、無ければ名前）とその試合後の RR
        self.assertRegex(text, r'<small class="then" title="この試合時点のランク">.*?<span>\d+ RR</span></small>')
        self.assertIn('<span class="mvp match" title="マッチ MVP">MVP', text)  # 試合の最高コンバットスコア
        self.assertEqual(text.count('class="mvp match'), 1)
        self.assertIn('<span class="mvp place" title="4人中 3位">3位</span>', text)  # 負けた後のランク
        self.assertIn('<li class="win">勝</li><li class="loss">敗</li>', text)  # 新しい順
        self.assertIn("<dt>ダメージ / ラウンド</dt><dd>143", text)  # (3000 / 22 -> 136, 3000 / 20 -> 150) / 2
        self.assertIn(f"/m/ap/{MATCH}?me=Tester%23JP1", text)
        self.assertNotIn("デスマッチなどは除く", text)
        self.assertIn("勝利", text)  # ゲームの文
        self.assertIn("敗北", text)
        self.assertNotIn("勝ち", text)

        english = self.get("/p/Tester/JP1?full=1", lang="en-GB").get_data(as_text=True)
        self.assertIn("Act rank for E9 A3 (best 9 wins)", english)
        self.assertIn("Victory", english)
        self.assertIn('<li class="win">W</li><li class="loss">L</li>', english)

    def test_the_best_rank_is_the_best_ever_in_todays_numbers(self):
        # v3 mmr の最高は Tier 22 だが、エピソード 4 は 21 で終わった: アセンダント以前はそれがイモータル 1（今の 24）
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        best = text[text.index('<p class="label">通算最高</p>') :]
        self.assertIn("<span>Tier 24</span>", best[:400])
        self.assertIn("/24/smallicon.png", best[:400])

    def test_a_player_page_in_depth(self):
        text = self.get("/p/Tester/JP1?full=1").get_data(as_text=True)
        self.assertIn("Tier 22 まであと 58 RR · 平均 +18 で約 4 勝", text)  # 100 のうち 42 RR
        self.assertIn('<path class="line" d="M14.0,93.3 L786.0,56.7"/>', text)  # RR の推移: コンペティティブ 2 試合
        self.assertIn("+3 RR", text)  # +18 - 15
        self.assertIn("<dd>59%", text)  # ラウンドごとの試合での KAST
        self.assertIn("<dt>ファーストキル</dt><dd>13", text)
        self.assertIn("<dt>ファーストデス</dt><dd>1<", text)
        self.assertIn("機会 9 · 成功 11%", text)  # 9 ラウンド中 1 回、チーム最後の 1 人になった
        self.assertIn(
            '<dt>1v2</dt><dd><meter min="0" max="100" value="0"></meter><span>0%</span><small>0/8</small>', text
        )
        self.assertIn("1時間20分", text)  # 40 分の試合 2 つ
        self.assertIn('class="versus"', text)
        self.assertIn("強み", text)
        self.assertIn("頭 25%・体 62%・脚 13%", text)
        self.assertIn("攻撃", text)
        self.assertIn('id="ag-title"', text)
        self.assertIn('id="map-title"', text)
        self.assertIn("ヴァンダル", text)  # valorant-api.com の名前の武器
        self.assertIn("/media/weapons/9c82e19d-4575-0200-1a81-3eacf00cf872/killstreamicon.png", text)
        self.assertIn('href="/p/Mate/0001"', text)  # パーティー
        self.assertIn('data-rank="Tier 21"', text)  # お気に入りと一緒に保存
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

        none = self.get("/p/Tester/JP1/part?mode=unrated").get_data(as_text=True)  # フィクスチャはコンペティティブだけ
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
        self.assertEqual(self.get("/p/Nobody/0000").status_code, 200)  # ページが残りを頼む
        for path in ("/p/Nobody/0000/part", "/p/Nobody/0000?full=1"):
            with self.subTest(path=path):
                answer = self.get(path)
                self.assertEqual(answer.status_code, 404)
                self.assertIn("プレイヤーが見つかりません", answer.get_data(as_text=True))

    def test_part_of_a_bad_riot_id(self):
        self.assertEqual(self.get("/p/a/b/part").status_code, 404)

    def test_no_key(self):
        self.app.config["VAL_SETTINGS"].save(henrik_key="")
        owner = self.get("/p/Tester/JP1")
        self.assertEqual(owner.status_code, 503)
        self.assertIn("API キーが未設定です", owner.get_data(as_text=True))
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

    def test_a_match_begins_with_the_team_of_the_player_the_visitor_came_from(self):
        text = self.get(f"/m/ap/{MATCH}?me=Tester%23JP1").get_data(as_text=True)
        self.assertLess(text.index('<h2 id="team-1"><span>レッド'), text.index('<h2 id="team-2"><span>ブルー'))
        text = self.get(f"/m/ap/{MATCH}?me=Other%23EUW").get_data(as_text=True)
        self.assertLess(text.index('<h2 id="team-1"><span>ブルー'), text.index('<h2 id="team-2"><span>レッド'))

    def test_a_match_round_by_round(self):
        text = self.get(f"/m/ap/{MATCH}?me=Tester%23JP1").get_data(as_text=True)
        self.assertIn('href="/p/Tester/JP1">← Tester#JP1 の戦績', text)
        self.assertIn("平均ランク", text)
        self.assertEqual(text.count('<li class="win'), 13)  # Tester のチームが 13 ラウンド取った
        self.assertIn('<li class="win swap"', text)  # 後半はラウンド 13 から
        self.assertIn('<use href="#end-detonate"/>', text)
        self.assertIn('<span class="cer">CLUTCH</span>', text)
        self.assertIn("レッド: エコ", text)  # ピストルラウンドは 1 人 800
        self.assertIn('<details class="round-feed win" id="r1">', text)
        self.assertIn("スパイク爆発 · A サイト", text)
        self.assertIn("0:10", text)
        self.assertIn("+ Mate", text)  # アシスト
        self.assertIn(
            '<th scope="col"><abbr title="キル・アシスト・生存・トレードがあったラウンドの割合">KAST</abbr></th>', text
        )
        self.assertIn('<span class="party p1" title="パーティー 1">1</span>', text)
        self.assertEqual(text.count('class="party '), 2, "only parties of two or more")
        self.assertIn("Tester の対面成績", text)
        self.assertIn('<span class="up" title="キル">12</span>', text)  # Tester が Foe を
        # ラウンドごとのプレイヤーの数値: そこまでのキル、デス、K/D、ヘッドショット率
        self.assertIn("Tester のラウンドごとの推移", text)
        self.assertRegex(text, r'<tr class="win"><th scope="row"><a href="#r1">1</a></th><td>\d</td>')
        self.assertIn('<th scope="col">HS%</th>', text)
        # 訪問者がどのプレイヤーから来たかわからないとき: ラウンドはチーム色で、撃ち合いは無し
        plain = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        self.assertIn('<li class="red"', plain)
        self.assertNotIn("対面成績", plain)
        self.assertNotIn("ラウンドごとの推移", plain)

    def test_marks_of_rounds_in_a_row_go_above_and_below(self):
        # 続けて印のあるラウンドは印を上と下に交互に（横に並ぶと重なる: 本番で CLUTCH と TEAM ACE が重なった）
        real = helpers.fixture

        def fixture(name):
            if name != "henrik_matches.json":
                return real(name)
            data = json.loads(real(name))
            marks = ("CeremonyAce", "CeremonyTeamAce", "CeremonyFlawless", "CeremonyThrifty")
            for item, mark in zip(data["data"][0]["rounds"][1:], marks):  # ラウンド 1 は CLUTCH
                item["ceremony"] = mark
            return json.dumps(data).encode()

        with mock.patch.object(helpers, "fixture", fixture):
            text = self.get(f"/m/ap/{MATCH}").get_data(as_text=True)
        marks = re.findall(r'<span class="(cer(?: low)?)">([A-Z ]+)</span>', text)
        self.assertEqual(
            marks,
            [("cer", "CLUTCH"), ("cer low", "ACE"), ("cer", "TEAM ACE"), ("cer low", "FLAWLESS"), ("cer", "THRIFTY")],
        )

    def test_a_match_without_its_rounds(self):
        text = self.get("/m/ap/aaaaaaaa-0000-4000-8000-000000000002").get_data(as_text=True)
        self.assertIn("7–13", self.get("/p/Tester/JP1?full=1").get_data(as_text=True))
        self.assertNotIn("ラウンドの流れ", text)
        self.assertNotIn("KAST", text)
        self.assertIn("DDΔ", text)  # 試合自身の数値から

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
        # アビリティの文中のゲームの印（"<whitebold>…</>"）は出さない
        self.assertIn("<p>大技を構える。ダメージを与える。</p>", page.get_data(as_text=True))
        self.assertNotIn("whitebold", page.get_data(as_text=True))
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
        """valorant-api.com の絵は入れ子が違う: ロールのアイコンとアビリティのアイコンは前は断っていた。"""
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

    def test_the_way_back_to_the_site_list_is_always_in_reach(self):
        """ホームページのサーバーの下: 広い画面はヘッダーの最初、スマホは下のタブバーの最初のタブ（どのページも）。"""
        home = {"homepage.home": "/"}
        arrow = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12H5M11 5.5 4.5 12l6.5 6.5"/></svg>'
        for path, environ in (("/", home), ("/leaderboard", {**home, **VISITOR}), ("/nowhere", home)):
            with self.subTest(path=path):
                text = self.get(path, lang="en-GB", environ_base=environ).get_data(as_text=True)
                back = f'href="/" title="Back to all sites" aria-label="Back to all sites">{arrow}<span>All sites</span></a>'
                self.assertIn(f'<div class="bar">\n<a class="home-link" {back}\n<a class="brand"', text)
                self.assertRegex(text, r'<nav class="tabs" aria-label="Menu">\n+<a class="home-tab" ' + re.escape(back))
        # ひとりで動くとき（PC アプリ、テスト）は無い
        alone = self.get("/").get_data(as_text=True)
        self.assertNotIn("home-link", alone)
        self.assertNotIn("home-tab", alone)

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
