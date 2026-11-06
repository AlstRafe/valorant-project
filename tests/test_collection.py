"""The owner's collection: linking the Riot account on the settings page, and the page of what it owns."""

import json
import os
import re
import unittest
from unittest import mock

from helpers import make_app

from valtracker import riot

VISITOR = {"homepage.visitor": True}
VANDAL = "9c82e19d-4575-0200-1a81-3eacf00cf872"


class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def link(self, pasted="ssid-1"):
        page = self.client.get("/settings").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        return self.client.post("/settings", data={"csrf": token, "do": "riot", "riot": pasted})

    def saved(self) -> dict:
        return json.loads((self.folder / "riot.json").read_text(encoding="utf-8"))

    def page(self, path="/collection") -> str:
        answer = self.client.get(path)
        self.assertEqual(answer.status_code, 200)
        return answer.get_data(as_text=True)

    def test_only_the_owner_has_it(self):
        self.assertEqual(self.client.get("/collection", environ_base=VISITOR).status_code, 404)
        self.assertNotIn('href="/collection"', self.client.get("/tools", environ_base=VISITOR).get_data(as_text=True))
        self.assertIn('href="/collection"', self.client.get("/tools").get_data(as_text=True))
        self.assertIn('href="/collection"', self.client.get("/shop").get_data(as_text=True))

    def test_before_linking(self):
        text = self.page()
        self.assertIn("まだ連携されていません", text)
        self.assertIn('href="/settings#riot"', text)
        self.assertEqual(self.riot.asked, [])

    def test_link_with_the_ssid(self):
        answer = self.link()
        self.assertEqual(answer.status_code, 200)
        text = answer.get_data(as_text=True)
        self.assertIn("Owner#JP1", text)
        self.assertIn("連携しました", text)
        self.assertNotIn("ssid-1", text)
        saved = self.saved()
        self.assertEqual(saved["cookies"], {"ssid": "ssid-2"}, "Riot's new cookie is kept, the removed one is not")
        self.assertEqual((saved["puuid"], saved["shard"]), ("0c0c0c0c-1111-4222-8333-444444444444", "ap"))
        if os.name == "posix":
            self.assertEqual((self.folder / "riot.json").stat().st_mode & 0o777, 0o600)

    def test_a_whole_cookie_header(self):
        self.link("Cookie: clid=uw1; ssid=ssid-1; tdid=abc")
        cookie = next(headers["Cookie"] for _, url, headers, _ in self.riot.asked if "/authorize" in url)
        self.assertEqual(cookie, "clid=uw1; ssid=ssid-1; tdid=abc")
        self.assertEqual(self.saved()["cookies"], {"clid": "uw1", "ssid": "ssid-2", "tdid": "abc"})

    def test_something_else_pasted(self):
        for pasted in ("", "hello world", "clid=uw1"):
            with self.subTest(pasted=pasted):
                text = self.link(pasted).get_data(as_text=True)
                self.assertIn("ssid ではありませんでした", text)
        self.assertFalse((self.folder / "riot.json").exists())

    def test_an_old_ssid(self):
        text = self.link("ssid-old").get_data(as_text=True)
        self.assertIn("ログインが切れています", text)
        self.assertIn("未連携", text)

    def test_link_with_the_address_for_an_hour(self):
        text = self.link(self.riot.address()).get_data(as_text=True)
        self.assertIn("Owner#JP1", text)
        self.assertRegex(text, r"\d{2}:\d{2} まで")
        self.assertNotIn("cookies", self.saved())
        self.assertNotIn("/authorize", " ".join(url for _, url, _, _ in self.riot.asked))

    def test_the_collection(self):
        self.link()
        text = self.page()
        self.assertIn('Owner<span class="tag">#JP1</span>', text)
        self.assertIn("ゴッド", text)  # the equipped title
        self.assertIn("レベル 187", text)
        self.assertIn("1,250", text)
        self.assertIn("4,200", text)
        # first every weapon in the game's order (sidearms first), each with what it wears (the chroma's picture)
        guns = text[text.index('<ul class="guns"') :]
        self.assertLess(guns.index("クラシック"), guns.index("ヴァンダル"))
        self.assertIn("weaponskinchromas/aa000000-0000-4000-8000-000000000022/displayicon.png", guns)
        self.assertIn('<small class="worn">プライム ヴァンダル</small>', guns)
        self.assertIn(
            'data-name="ヴァンダル レイヴァー ヴァンダル プライム ヴァンダル"', guns, "a skin's name finds it"
        )
        self.assertNotIn('class="items skins rows"', text, "no weapon open yet")
        self.assertNotIn("近接武器", text, "a weapon without skins of its own is left out")
        self.assertRegex(text, r"スキン <small>3</small>")
        sources = re.findall(r'(?:src|href)="([^"]+)"', text)
        self.assertEqual([s for s in sources if s.startswith(("http:", "https:", "//"))], [])
        # then one weapon's skins: the dearest first, whatever is worn (it is marked where it is)
        vandal = self.page(f"/collection?weapon={VANDAL}")
        self.assertIn(
            f'href="/collection?weapon={VANDAL}#gun" title="ヴァンダル: プライム ヴァンダル" aria-current="true"',
            vandal,
        )
        rows = vandal[vandal.index('class="items skins rows"') :]
        self.assertLess(rows.index("レイヴァー ヴァンダル"), rows.index("プライム ヴァンダル"))
        self.assertNotIn("クラシック", rows)
        self.assertIn("バリエーション 2/3", rows)
        self.assertIn("tier-premium on", rows)
        self.assertIn('<span class="badge">装備中</span>', rows)
        self.assertNotIn("スタンダード", rows)
        self.assertIn('href="/collection">← 武器一覧</a>', vandal)
        # at the top, before the list: what the weapon wears now, with its variant, level and buddy
        now = vandal[vandal.index('class="worn-now') : vandal.index('class="items skins rows"')]
        self.assertIn('class="worn-now tier-premium"', now)
        self.assertIn('<p class="worn-name">プライム ヴァンダル</p>', now)
        self.assertIn("aa000000-0000-4000-8000-000000000022/fullrender.png", now)  # the worn variant's picture
        self.assertIn("<span>バリエーション 2 ゴールド</span>", now)
        self.assertRegex(now, r"レベル \d/\d")
        self.assertIn("バディー：チリ バディー", now)
        self.assertNotIn('class="items skins rows"', self.page("/collection?weapon=nothing"))

    def test_the_other_tabs(self):
        self.link()
        buddies = self.page("/collection?show=buddies")
        self.assertIn("2 個", buddies)
        self.assertLess(buddies.index("チリ バディー"), buddies.index("ハート バディー"))
        cards = self.page("/collection?show=cards")
        self.assertIn("playercards/cc000000-0000-4000-8000-000000000002/smallart.png", cards)
        titles = self.page("/collection?show=titles")
        self.assertIn("ルーキー", titles)
        sprays = self.page("/collection?show=sprays")
        self.assertIn("fulltransparenticon.png", sprays)
        agents = self.page("/collection?show=agents")
        self.assertIn("未獲得", agents)
        self.assertLess(agents.index("ジェット"), agents.index("locked"))
        self.assertIn("ぬいぐるみ", self.page("/collection?show=flex"))
        self.assertIn('aria-current="true">スキン', self.page("/collection?show=nothing"))

    def test_the_loadout_v3(self):
        """PD's playerloadout v3 (v2 answers 404 since September 2026): sprays and flex are its ActiveExpressions."""
        self.link()
        vandal = self.page(f"/collection?weapon={VANDAL}")
        self.assertIn('<p class="worn-name">プライム ヴァンダル</p>', vandal)
        self.assertNotIn("読めませんでした", vandal)
        asked = [url for _, url, _, _ in self.riot.asked if "playerloadout" in url]
        self.assertTrue(asked, "the loadout is asked")
        self.assertTrue(all("/personalization/v3/" in url for url in asked), asked)
        sprays = self.page("/collection?show=sprays")
        self.assertIn('<span class="badge" lang="ja">装備中</span>', sprays)
        self.assertIn('<option value="0">1（いま GG スプレー）</option>', sprays, "a place is its position")
        flex = self.page("/collection?show=flex")
        self.assertIn('<span class="badge" lang="ja">装備中</span>', flex, "the flex on the wheel is marked")

    def test_the_loadout_v2_when_v3_is_not_there(self):
        self.riot.loadout_version = 2
        loadout = self.riot.pd["loadout"]
        del loadout["ActiveExpressions"], loadout["DynamicOptions"]
        loadout["Sprays"] = [
            {"EquipSlotID": "x", "SprayID": "dd000000-0000-4000-8000-000000000001", "SprayLevelID": None}
        ]
        self.link()
        self.assertIn('<p class="worn-name">プライム ヴァンダル</p>', self.page(f"/collection?weapon={VANDAL}"))
        self.assertIn('<option value="x">1（いま GG スプレー）</option>', self.page("/collection?show=sprays"))

    def test_what_is_worn_cannot_be_read(self):
        """No loadout (both answer 404): nothing is called worn or standard, and the page says why."""
        self.riot.loadout_version = 1
        self.link()
        text = self.page()
        self.assertIn("いま装備しているものを Riot から読めませんでした", text)
        self.assertIn("loadout 404 RESOURCE_NOT_FOUND", text)
        self.assertNotIn('<small class="worn">', text)
        self.assertIn('<ul class="guns"', text, "what is owned is still shown")
        vandal = self.page(f"/collection?weapon={VANDAL}")
        now = vandal[vandal.index('class="worn-now') : vandal.index('class="items skins rows"')]
        self.assertIn('<p class="worn-name">読めませんでした</p>', now)
        self.assertNotIn("標準", now)
        self.assertNotIn('<span class="badge">装備中</span>', vandal)
        self.assertNotIn("読めませんでした", self.page("/collection?show=agents"))

    def test_kept_a_while(self):
        self.link()
        self.page()
        calls = self.riot.pd_calls()
        self.page("/collection?show=cards")
        self.assertEqual(self.riot.pd_calls(), calls)

    def test_a_refused_token_is_renewed_once(self):
        self.link()
        self.riot.refuse = 1
        self.assertIn("プライム ヴァンダル", self.page())
        self.assertEqual(self.riot.renewals, 2)
        self.assertEqual(self.saved()["cookies"]["ssid"], "ssid-3")

    def test_a_new_token_refused_too(self):
        self.link()
        self.riot.refuse = 100
        answer = self.client.get("/collection")
        self.assertEqual(answer.status_code, 503)
        self.assertIn("BAD_CLAIMS", answer.get_data(as_text=True))
        self.assertNotIn("signed_out", self.saved())

    def test_after_the_hour_the_cookie_is_traded_again(self):
        self.link()
        store = self.app.config["VAL_RIOT"].store
        store.save(token={**self.saved()["token"], "until": 0})
        self.page()
        self.assertEqual(self.riot.renewals, 2)

    def test_signed_out_later(self):
        self.link()
        self.app.config["VAL_RIOT"].store.save(token=None)
        self.riot.ssid = "changed-password"
        text = self.page()
        self.assertIn("ログインが切れています", text)
        self.assertIn('href="/settings#riot"', text)
        self.assertIn("ログインが切れています", self.client.get("/settings").get_data(as_text=True))
        self.assertIn("ssid-2", self.saved()["cookies"]["ssid"], "kept: it may work again")

    def test_one_kind_missing(self):
        self.link()
        self.riot.broken = {"sprays"}
        self.assertIn("プライム ヴァンダル", self.page())
        self.assertIn("読み込めませんでした", self.page("/collection?show=sprays"))
        self.assertIn("sprays 500 SERVER_ERROR", self.client.get("/settings").get_data(as_text=True))

    def test_riot_down(self):
        self.link()
        self.riot.broken = {"wallet", "loadout", *riot.ITEM_TYPES}
        answer = self.client.get("/collection")
        self.assertEqual(answer.status_code, 503)
        self.assertIn("答えがありませんでした", answer.get_data(as_text=True))

    def test_unlink(self):
        self.link()
        page = self.client.get("/settings").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        text = self.client.post("/settings", data={"csrf": token, "do": "riot_off"}).get_data(as_text=True)
        self.assertIn("解除しました", text)
        self.assertEqual(self.saved(), {})
        self.assertIn("まだ連携されていません", self.page())

    def test_the_big_lists_are_kept_small(self):
        self.link()
        self.page()
        saved = json.loads((self.folder / "content" / "weapons-ja-JP.json").read_text(encoding="utf-8"))
        vandal = saved["data"][0]
        self.assertNotIn("weaponStats", vandal)
        self.assertNotIn("assetPath", json.dumps(vandal))
        self.assertNotIn("example.org", json.dumps(vandal), "a video only from the known places (skin library)")
        self.assertEqual(vandal["cost"], 2900)

    def test_pd_is_told_the_game_version(self):
        self.link()
        self.page()
        headers = next(headers for _, url, headers, _ in self.riot.asked if url.startswith("https://pd."))
        self.assertEqual(headers["X-Riot-ClientVersion"], "release-09.10-shipping-14-2973541")
        self.assertTrue(headers["X-Riot-ClientPlatform"])


class PastedTest(unittest.TestCase):
    def test_cookies(self):
        self.assertEqual(riot._pasted_cookies("  abc.def-ghi "), {"ssid": "abc.def-ghi"})
        self.assertEqual(riot._pasted_cookies('ssid="a.b"; clid=ec1'), {"ssid": "a.b", "clid": "ec1"})
        self.assertEqual(riot._pasted_cookies("two words"), {})

    def test_renewed(self):
        renewed = riot._renewed(
            {"ssid": "old", "asid": "x"}, [("Set-Cookie", "ssid=new; Path=/"), ("set-cookie", "asid=; Max-Age=0")]
        )
        self.assertEqual(renewed, {"ssid": "new"})

    def test_address(self):
        found = riot._tokens_in("https://playvalorant.com/opt_in#access_token=" + "a" * 30 + "&expires_in=3600", 1000)
        self.assertEqual(found, {"access": "a" * 30, "id": "", "until": 1000 + 3600 - riot.EARLY})
        self.assertIsNone(riot._tokens_in("/login", 0))


if __name__ == "__main__":
    unittest.main()
