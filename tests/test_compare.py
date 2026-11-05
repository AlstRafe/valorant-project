"""Two players side by side (/compare)."""

import json
import os
import unittest
from unittest import mock

from helpers import FakeFetch, make_app

from valtracker.compare import _best

ORIGINAL = FakeFetch.__call__


def other_player(self, url, headers):
    """HenrikDev's answers for "Other#KR1": the fixtures' player with another name, a higher rank and level."""
    status, body = ORIGINAL(self, url, headers)
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
        self.assertIn("プレイヤーを比べる", text)
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
        self.assertIn("1 項目で上", text, "the peak is the same, a level is no better")
        self.assertIn("<td>+95</td>", text)
        self.assertIn("ACS", text)
        self.assertIn("ジェット", text)
        self.assertIn('href="/compare?a=Other%23KR1&amp;b=Tester%23JP1"', text)
        self.assertIn("多くて 15 試合", text)

    def test_a_link_pasted(self):
        text = self.page("/compare?a=https://tracker.gg/valorant/profile/riot/Tester%2523JP1/overview&b=Other%23KR1")
        self.assertIn('value="Tester#JP1"', text)
        self.assertIn("<table", text)

    def test_a_player_not_found_or_written_wrong(self):
        self.page("/compare?a=nothing&b=Other%23KR1", 400)
        self.fetch.henrik_status = 404
        text = self.page("/compare?a=Tester%23JP1&b=Other%23KR1", 404)
        self.assertIn("プレイヤーが見つかりませんでした", text)
        self.assertIn('aria-invalid="true"', text)

    def test_the_mode(self):
        self.page("/compare?a=Tester%23JP1&b=Other%23KR1&mode=unrated")
        asked = [url for url in self.fetch.henrik_calls() if "/v4/matches/" in url]
        self.assertTrue(asked and all("mode=unrated" in url for url in asked))
        self.page("/compare?a=Tester%23JP1&b=Other%23KR1&mode=deathmatch")
        asked = [url for url in self.fetch.henrik_calls() if "/v4/matches/" in url]
        self.assertIn("mode=competitive", asked[-1], "a mode with nothing to compare is the usual one")

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
