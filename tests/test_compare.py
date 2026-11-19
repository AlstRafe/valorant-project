"""2 人の比較（/compare）。"""

import json
import os
import time
import unittest
from unittest import mock

from helpers import FakeFetch, fixture, make_app

from valtracker import henrik
from valtracker.compare import _best

ORIGINAL = FakeFetch.__call__


def other_player(self, url, headers):
    """「Other#KR1」の HenrikDev の答え: フィクスチャのプレイヤーを別名にし、ランクとレベルを上げたもの。"""
    status, body = ORIGINAL(self, url, headers)
    if "/Mate/" in url and "/v2/account/" in url and status == 200:
        # 「Mate#0001」: フィクスチャの試合に一緒に出ている別のプレイヤー（puuid-2）
        found = json.loads(body)
        found["data"].update(name="Mate", tag="0001", puuid="puuid-2")
        return status, json.dumps(found).encode()
    if "/Other/" not in url or status != 200:
        return status, body
    found = json.loads(body)
    data = found.get("data")
    if "/v2/account/" in url:
        data.update(name="Other", tag="KR1", account_level=300)
    elif "/v3/mmr/" in url:
        data["current"]["tier"] = {"id": 24, "name": "Immortal 1"}
    return status, json.dumps(found).encode()


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        patch = mock.patch.object(FakeFetch, "__call__", other_player)
        patch.start()
        self.addCleanup(patch.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200) -> str:
        answer = self.client.get(path)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def test_the_form_alone(self):
        text = self.page("/compare")
        self.assertIn("プレイヤー比較", text)
        self.assertIn('name="a"', text)
        self.assertIn('<option value="premier"', text)
        self.assertNotIn('<option value="deathmatch"', text, "no teams to win")
        self.assertNotIn("<table", text)

    def test_two_players(self):
        text = self.page("/compare?a=Tester%23JP1&b=Other%23KR1")
        self.assertIn("Tester#JP1 vs Other#KR1", text)
        self.assertIn('href="/p/Other/KR1"', text)
        row = text[text.index(">現在のランク<") :]
        row = row[: row.index("</tr>")]
        self.assertLess(row.index("Tier 21"), row.index('class="best"'), "the higher rank is the one marked")
        self.assertIn("Tier 24", row)
        self.assertIn("2 項目で優勢", text, "the rank and the act's best; the peak is the same, a level is no better")
        self.assertIn("<td>+95</td>", text)
        self.assertIn("ACS", text)
        self.assertIn("ジェット", text)
        self.assertIn('href="/compare?a=Other%23KR1&amp;b=Tester%23JP1"', text)
        self.assertIn("最大 15 試合", text)

    def test_a_link_pasted(self):
        text = self.page("/compare?a=https://tracker.gg/valorant/profile/riot/Tester%2523JP1/overview&b=Other%23KR1")
        self.assertIn('value="Tester#JP1"', text)
        self.assertIn("<table", text)

    def test_a_player_not_found_or_written_wrong(self):
        self.page("/compare?a=nothing&b=Other%23KR1", 400)
        self.fetch.henrik_status = 404
        text = self.page("/compare?a=Tester%23JP1&b=Other%23KR1", 404)
        self.assertIn("プレイヤーが見つかりません", text)
        self.assertIn('aria-invalid="true"', text)

    def test_the_mode(self):
        self.page("/compare?a=Tester%23JP1&b=Other%23KR1&mode=unrated")
        asked = [url for url in self.fetch.henrik_calls() if "/v4/matches/" in url]
        self.assertTrue(asked and all("mode=unrated" in url for url in asked))
        self.page("/compare?a=Tester%23JP1&b=Other%23KR1&mode=deathmatch")
        asked = [url for url in self.fetch.henrik_calls() if "/v4/matches/" in url]
        self.assertIn("mode=competitive", asked[-1], "a mode with nothing to compare is the usual one")

    def test_lifetime(self):
        text = self.page("/compare?a=Tester%23JP1&b=Other%23KR1&span=all")
        self.assertIn('<option value="all" selected>通算</option>', text)
        self.assertIn("通算の平均（2 試合と 2 試合）", text)
        self.assertIn('href="/p/Other/KR1?span=all"', text)
        self.assertIn('href="/compare?a=Other%23KR1&amp;b=Tester%23JP1&amp;span=all"', text)
        self.assertNotIn("最大 15 試合", text)

    def test_lifetime_says_whose_matches_are_still_loading(self):
        """読み込み途中の人の通算は試合数が少ない: 比較ページにも理由を出す（片方だけ数が少なく、壊れて見えた）。"""
        text = self.page("/compare?a=Tester%23JP1&b=Mate%230001&span=all")
        self.assertIn("Tester#JP1 の残りの試合を取得中…", text)
        self.assertIn("Mate#0001 の残りの試合を取得中…", text)
        self.assertIn("取得し終えるまで、その人の数値は取得できた試合だけのものです。", text)
        self.assertIn('<a href="/compare?a=Tester%23JP1&amp;b=Mate%230001&amp;span=all">再集計</a>', text)
        archive = self.app.config["VAL_ARCHIVE"]
        archive.save(archive.player("puuid-me") | {"finished": time.time()})
        text = self.page("/compare?a=Tester%23JP1&b=Mate%230001&span=all")
        self.assertNotIn("Tester#JP1 の残りの試合を取得中", text, "read through")
        self.assertIn("Mate#0001 の残りの試合を取得中…", text)
        self.assertNotIn("を取得中", self.page("/compare?a=Tester%23JP1&b=Mate%230001"), "not on the latest 15")

    def test_lifetime_counts_each_player_from_their_own_matches(self):
        """右側だけが出た試合: 右の通算だけが増え、左は左の試合のまま。簡易データの分はその人の行で言う。"""
        base = henrik._slim_matches(json.loads(fixture("henrik_matches.json"))["data"])[0]
        archive = self.app.config["VAL_ARCHIVE"]
        extra = []
        for number in range(3):
            match = json.loads(json.dumps(base))
            match["metadata"]["match_id"] = f"cccccccc-0000-4000-8000-{number:012d}"
            match["metadata"]["started_at"] = f"2026-10-0{number + 1}T10:00:00Z"
            match["players"] = [player for player in match["players"] if player["puuid"] != "puuid-me"]
            extra.append(match)
        archive.keep(extra)
        archive.keep_thin("puuid-me", henrik._slim_stored(json.loads(fixture("henrik_stored.json"))["data"]))
        thin = len([line for line in archive.thin("puuid-me", "competitive") if line["rounds"]])
        self.assertGreater(thin, 0)
        text = self.page("/compare?a=Tester%23JP1&b=Mate%230001&span=all")
        self.assertIn(f"通算の平均（{2 + thin} 試合と 5 試合）", text)
        self.assertIn(f"Tester#JP1: うち {thin} 試合は簡易データのため", text)
        self.assertIn("残り 2 試合から集計", text)
        self.assertNotIn("Mate#0001: うち", text)
        row = text[text.index(">試合数<") :]
        row = row[: row.index("</tr>")]
        self.assertIn(f"<td>{2 + thin}</td>", row)
        self.assertIn("<td>5</td>", row)
        # 左右を入れ替えても、それぞれ自分の試合のまま
        text = self.page("/compare?a=Mate%230001&b=Tester%23JP1&span=all")
        self.assertIn(f"通算の平均（5 試合と {2 + thin} 試合）", text)

    def test_in_english(self):
        self.client.set_cookie("lang", "en-GB")
        text = self.page("/compare?a=Tester%23JP1&b=Other%23KR1")
        self.assertIn("Compare players", text)

    def test_the_better(self):
        self.assertEqual(_best([3, 2], True), 0)
        self.assertEqual(_best([3, 2], False), 1)
        self.assertIsNone(_best([2, 2], True))
        self.assertIsNone(_best([None, 2], True))
        self.assertIsNone(_best([3, 2], None))

    def test_the_player_page_links_here_and_has_its_picture(self):
        text = self.page("/p/Tester/JP1?full=1")
        self.assertIn('href="/compare?a=Tester%23JP1"', text)
        found = text[text.index("data-card='") + len("data-card='") :]
        card = json.loads(found[: found.index("'")].replace("&#34;", '"').replace("&amp;", "&"))
        self.assertEqual(card["name"], "Tester")
        self.assertEqual(card["rank"], "Tier 21")
        self.assertIn(["K/D", 1.33], card["stats"])
        self.assertEqual(card["agents"][0]["name"], "ジェット")
        self.assertTrue(card["agents"][0]["icon"].startswith("/media/"))
        self.assertTrue(card["wide"].startswith("/media/"))


if __name__ == "__main__":
    unittest.main()
