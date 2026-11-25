"""Where Japanese and Chinese lines may break (valtracker.phrases, the same as Home Page homepage.phrases)."""

from __future__ import annotations

import unittest

from valtracker.phrases import mark, mark_page


class PhrasesTest(unittest.TestCase):
    def test_japanese_breaks_between_phrases_only(self):
        self.assertEqual(mark("障害対応を練習。", "ja"), "障害対応を<wbr>練習。")
        self.assertEqual(
            mark("アドレスが変わったか、なくなった可能性があります。", "ja"),
            "アドレスが<wbr>変わったか、<wbr>なくなった<wbr>可能性が<wbr>あります。",
        )

    def test_chinese_keeps_one_character_words_with_their_neighbours(self):
        self.assertEqual(mark("在瀏覽器裡練習排除故障。", "zh-TW"), "在瀏覽器裡<wbr>練習排除故障。")
        self.assertEqual(mark("挑一個感興趣的打開看看吧。", "zh-TW"), "挑一個<wbr>感興趣的<wbr>打開<wbr>看看吧。")
        self.assertIn("<wbr>與各特務、", mark("近期對戰與各特務、各地圖的戰績。", "zh-TW"))
        self.assertIn("<wbr>並列閱讀。", mark("原文與譯文並列閱讀。", "zh-TW"))
        marked = mark("每小時收集 BBC、中央社、韓聯社與 InfoQ、iThome 的報導", "zh-TW")
        for word in ("每小時", "中央社", "韓聯社", "InfoQ", "iThome"):
            self.assertIn(word, marked)
        for chunk in marked.split("<wbr>"):
            self.assertGreater(len(chunk.strip()), 1, marked)

    def test_latin_words_and_spaces_get_no_extra_break(self):
        self.assertEqual(mark("Kubernetes と Docker を学ぶ", "ja"), "Kubernetes\u00a0と Docker\u00a0を<wbr>学ぶ")
        self.assertEqual(mark("InfoQ iThome BBC Yonhap", "zh-TW"), "InfoQ iThome BBC Yonhap")

    def test_a_short_name_and_the_word_after_it_stay_on_one_line(self):
        self.assertEqual(
            mark("成績を、Riot ID を入れるだけで。", "ja"), "成績を、<wbr>Riot\u00a0ID\u00a0を<wbr>入れるだけで。"
        )
        self.assertEqual(mark("Riot ID만 입력하면", "ko"), "Riot\u00a0ID만 입력하면")
        self.assertIn("iThome\u00a0的報導", mark("韓聯社與 InfoQ、iThome 的報導", "zh-TW"))
        self.assertEqual(
            mark("One Riot ID. Lifetime stats.", "en-GB"), "One Riot ID. Lifetime stats."
        )  # English: as it is
        self.assertIn("Google Cloud Platform", mark("Google Cloud Platform の障害", "ja"))  # three words: not a name

    def test_the_text_decides_the_model(self):
        # a Japanese news title on the English page, Chinese on the Korean one, kanji alone on the Japanese one
        self.assertEqual(
            mark("Kubernetes 新版、スケジューラを改良", "en-GB"), "Kubernetes\u00a0新版、<wbr>スケジューラを<wbr>改良"
        )
        self.assertEqual(mark("中央社報導颱風接近台灣", "ko"), "中央社<wbr>報導<wbr>颱風接近<wbr>台灣")
        self.assertEqual(mark("翻訳して読み比べる", "zh-TW"), "翻訳して<wbr>読み<wbr>比べる")

    def test_kanji_words_numbers_and_particles_stay_whole(self):
        self.assertEqual(mark("インストール不要・架空データ", "ja"), "インストール不要・架空データ")  # not 架|空
        self.assertEqual(mark("要新增網站，請在這裡", "zh-TW").split("<wbr>")[0], "要新增網站，")  # not 網|站
        self.assertIn("（資格\u00a060\u00a0試験）", mark("分野別の学習問題（資格 60 試験）", "ja"))
        self.assertIn("約\u00a01\u00a0分", mark("master に push して約 1 分", "ja"))
        self.assertIn("（Kubernetes）\u2060を", mark("復旧せよ（Kubernetes）を見る", "ja"))
        self.assertIn("[[site]]\u2060를", mark("sites.toml에 [[site]]를 하나", "ko"))

    def test_the_text_is_escaped(self):
        marked = mark("<b>タグ</b>&を含むテキストです", "ja")
        self.assertNotIn("<b>", marked)
        self.assertTrue(marked.startswith("&lt;b&gt;タグ&lt;/b&gt;&amp;"))

    def test_other_languages_and_short_words_stay_as_they_are(self):
        self.assertEqual(mark("인프라 운영 트레이닝 게임", "ko"), "인프라 운영 트레이닝 게임")
        self.assertEqual(mark("Practise <incident> response", "en-GB"), "Practise &lt;incident&gt; response")
        self.assertEqual(mark("毎時更新", "ja"), "毎時更新")
        self.assertEqual(mark("", "ja"), "")


class PageTest(unittest.TestCase):
    """The whole drawn page: only the text between tags changes."""

    def test_text_between_tags_only(self):
        page = (
            '<title>VAL Tracker: プレイヤーの戦績</title><script>if(a>0){b="試合の横に、もうひとつの画面"}</script>'
            '<a href="/help" title="ゲームより低い優先度で動作します">ゲームより低い優先度で動作します</a>'
            "<!-- 直近の試合を、ゲームのキャリアと -->"
            '<input placeholder="名前#タグを入れる"><textarea>同じランク帯の中での位置も</textarea>'
            "<code>Riot ID を入れる</code><p>Riot ID を入れるだけで。</p><p>One Riot ID.</p>"
        )
        marked = mark_page(page, "ja")
        self.assertIn('title="ゲームより低い優先度で動作します">ゲームより<wbr>低い優先度で<wbr>動作します</a>', marked)
        for kept in (
            "<title>VAL Tracker: プレイヤーの戦績</title>",
            '<script>if(a>0){b="試合の横に、もうひとつの画面"}</script>',
            "<!-- 直近の試合を、ゲームのキャリアと -->",
            "<textarea>同じランク帯の中での位置も</textarea>",
            "<code>Riot ID を入れる</code>",
            "<p>One Riot ID.</p>",
        ):
            self.assertIn(kept, marked)
        self.assertIn("<p>Riot\u00a0ID\u00a0を<wbr>入れるだけで。</p>", marked)

    def test_escaped_text_stays_escaped(self):
        marked = mark_page("<p>&lt;script&gt; と &amp; の説明です</p>", "ja")
        self.assertEqual(marked.replace("<wbr>", ""), "<p>&lt;script&gt; と &amp; の説明です</p>")
        self.assertEqual(mark_page("<p>a < b</p>", "ja"), "<p>a < b</p>")


if __name__ == "__main__":
    unittest.main()
