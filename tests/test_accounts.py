"""持ち主の複数の Riot アカウント: 2 つ以上の連携、別のアカウントを表示するボタン、
アカウントごとに持つもの（ストア日記、保存したセット）、PC アプリ（ゲームはいつも Riot Client のアカウント）。"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import PUUID, PUUID2, make_app
from test_desktop import FakeClient

from valtracker import riot as riot_module
from valtracker.localclient import LocalClient

VISITOR = {"homepage.visitor": True}


class AccountsTest(unittest.TestCase):
    """サイト: アカウントは cookie で連携する。"""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def token(self, path: str = "/settings") -> str:
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def link(self, pasted: str):
        return self.client.post("/settings", data={"csrf": self.token(), "do": "riot", "riot": pasted})

    def both(self):
        self.link("ssid-1")
        return self.link("sub-1")

    def switch(self, puuid: str, back: str = "/"):
        return self.client.post("/account", data={"csrf": self.token(), "account": puuid, "next": back})

    def saved(self, name: str = "riot.json") -> dict:
        return json.loads((self.folder / name).read_text(encoding="utf-8"))

    def page(self, path: str) -> str:
        answer = self.client.get(path)
        self.assertEqual(answer.status_code, 200, path)
        return answer.get_data(as_text=True)

    def test_a_second_account_is_linked_beside_the_first(self):
        text = self.both().get_data(as_text=True)
        self.assertIn("Riot アカウント Smurf#JP2 と連携しました。", text)
        saved = self.saved()
        self.assertEqual(saved["puuid"], PUUID2, "the account just linked is the one in use")
        self.assertEqual(list(saved["others"]), [PUUID])
        self.assertEqual(saved["others"][PUUID]["name"], "Owner")
        self.assertTrue(saved["others"][PUUID]["cookies"]["ssid"].startswith("ssid-"), "its own cookie is kept")
        self.assertNotIn("linked_first", saved, "the site has no Riot Client to choose against")
        # 設定: 1 行ずつ、使用中に印、ほかには切り替えボタン
        row = re.compile(
            r'<li( class="now")?>\s*<span class="account-name"><strong>(\w+#\w+)</strong>(.*?)</li>', re.DOTALL
        )
        rows = {name: (bool(now), rest) for now, name, rest in row.findall(text)}
        self.assertEqual(list(rows), ["Owner#JP1", "Smurf#JP2"], "by name, the same order whichever is in use")
        self.assertTrue(rows["Smurf#JP2"][0])
        self.assertIn("使用中", rows["Smurf#JP2"][1])
        self.assertNotIn('value="riot_use"', rows["Smurf#JP2"][1])
        self.assertIn('value="riot_use"', rows["Owner#JP1"][1])
        self.assertIn(f'name="account" value="{PUUID}"', rows["Owner#JP1"][1])
        self.assertIn('value="riot_off"', rows["Owner#JP1"][1])
        self.assertIn('<details class="ssid-way"><summary>アカウントを追加</summary>', text, "the steps are folded")

    def test_the_buttons_show_another_account(self):
        self.both()
        text = self.page("/")
        self.assertIn('<form method="post" action="/account" class="accounts"', text)
        self.assertIn(
            '<strong class="account-now" aria-current="true">Smurf<span class="tag">#JP2</span></strong>', text
        )
        self.assertIn(f'<button type="submit" name="account" value="{PUUID}">Owner', text)
        self.assertIn('href="/p/Smurf/JP2"><strong>戦績</strong><small>Smurf#JP2</small>', text)
        answer = self.switch(PUUID, "/shop")
        self.assertEqual((answer.status_code, answer.headers["Location"]), (303, "/shop"), "back where the button was")
        saved = self.saved()
        self.assertEqual((saved["puuid"], list(saved["others"])), (PUUID, [PUUID2]))
        self.assertIn('href="/p/Owner/JP1"><strong>戦績</strong>', self.page("/"))
        # 次からはそのアカウントとして、その cookie で読む
        before = len(self.riot.asked)
        self.assertIn("ヴァンダル", self.page("/collection"))
        asked = [url for _, url, _, _ in self.riot.asked[before:] if "pvp.net" in url]
        self.assertTrue(asked)
        self.assertTrue(all(PUUID2 not in url for url in asked), "nothing of the other account")
        self.switch(PUUID2)
        before = len(self.riot.asked)
        self.page("/collection")
        self.assertTrue(any(PUUID2 in url for _, url, _, _ in self.riot.asked[before:]))
        self.assertTrue(self.saved()["cookies"]["ssid"].startswith("sub-"))

    def test_the_owners_pages_have_the_buttons_and_a_visitor_never_sees_them(self):
        self.both()
        for path in ("/", "/shop", "/shop/history", "/collection", "/progress", "/live"):
            self.assertIn('class="accounts"', self.page(path), path)
        for path in ("/", "/shop", "/tools", "/p/Tester/JP1"):
            text = self.client.get(path, environ_base=VISITOR).get_data(as_text=True)
            self.assertNotIn('class="accounts"', text, path)
            self.assertNotIn("Smurf", text, path)
        self.assertEqual(self.client.post("/account", data={"csrf": "x"}, environ_base=VISITOR).status_code, 400)
        token = self.token()
        refused = self.client.post("/account", data={"csrf": token, "account": PUUID}, environ_base=VISITOR)
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(self.saved()["puuid"], PUUID2)

    def test_one_account_has_nothing_to_choose(self):
        self.link("ssid-1")
        self.assertNotIn('class="accounts"', self.page("/"))
        self.assertNotIn("others", self.saved())
        text = self.page("/settings")
        self.assertIn("複数のアカウントを追加して切り替えられます。", text)
        self.assertNotIn('value="riot_use"', text)

    def test_the_button_leads_back_to_this_site_only(self):
        self.both()
        for outside in ("https://example.com/", "//example.com/", "/\\example.com", "javascript:alert(1)", ""):
            self.assertEqual(self.switch(PUUID, outside).headers["Location"], "/", outside)
        self.assertEqual(self.switch(PUUID2, "/collection?show=sets").headers["Location"], "/collection?show=sets")
        # 連携していない（もうしていない）アカウント: 何も変わらない
        self.assertEqual(self.switch("0e0e0e0e-1111-4222-8333-666666666666").status_code, 303)
        self.assertEqual(self.saved()["puuid"], PUUID2)

    def test_under_a_mount_the_button_leads_back_under_it(self):
        app, _, _ = make_app(mount="/val")
        self.addCleanup(app._test_folder.cleanup)
        client = app.test_client()
        base = "http://localhost/val"  # Home Page のサーバーが配る形で
        page = client.get("/settings", base_url=base).get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        for pasted in ("ssid-1", "sub-1"):
            client.post("/settings", data={"csrf": token, "do": "riot", "riot": pasted}, base_url=base)
        text = client.get("/shop", base_url=base).get_data(as_text=True)
        self.assertIn('name="next" value="/val/shop"', text)
        data = {"csrf": token, "account": PUUID, "next": "/val/shop"}
        self.assertEqual(client.post("/account", data=data, base_url=base).headers["Location"], "/val/shop")
        data["next"] = "/shop"  # マウントの外: このホストの別サイトのページ
        self.assertEqual(client.post("/account", data=data, base_url=base).headers["Location"], "/val/")

    def test_switching_and_unlinking_on_the_settings_page(self):
        self.both()
        data = {"csrf": self.token(), "do": "riot_use", "account": PUUID}
        text = self.client.post("/settings", data=data).get_data(as_text=True)
        self.assertIn("Owner#JP1 に切り替えました。", text)
        self.assertEqual(self.saved()["puuid"], PUUID)
        # 使用中のものを解除: 以後はもう一方を使う
        data = {"csrf": self.token(), "do": "riot_off", "account": PUUID}
        text = self.client.post("/settings", data=data).get_data(as_text=True)
        self.assertIn("Riot アカウントとの連携を解除しました。", text)
        saved = self.saved()
        self.assertEqual(saved["puuid"], PUUID2)
        self.assertNotIn("others", saved)
        self.assertNotIn("Owner#JP1", text)
        # 使っていないものを解除: 使用中のものはそのまま
        self.link(self.riot.ssid)
        self.assertEqual(self.saved()["puuid"], PUUID)
        self.client.post("/settings", data={"csrf": self.token(), "do": "riot_off", "account": PUUID2})
        saved = self.saved()
        self.assertEqual(saved["puuid"], PUUID)
        self.assertNotIn("others", saved)
        self.client.post("/settings", data={"csrf": self.token(), "do": "riot_off", "account": PUUID})
        self.assertNotIn("puuid", self.saved())
        self.assertIn("未連携", self.page("/settings"))

    def test_pasting_an_account_again_links_it_once(self):
        self.both()
        self.link(self.riot.ssid)  # 1 つ目の今の cookie: そのアカウントがまた使用中に
        saved = self.saved()
        self.assertEqual((saved["puuid"], list(saved["others"])), (PUUID, [PUUID2]))
        self.assertEqual([row["name"] for row in self.app.config["VAL_RIOT"].accounts()], ["Owner", "Smurf"])

    def test_no_more_accounts_than_the_most(self):
        self.link("ssid-1")
        with mock.patch.object(riot_module, "ACCOUNTS_MAX", 1), mock.patch("valtracker.views.ACCOUNTS_MAX", 1):
            text = self.link("sub-1").get_data(as_text=True)
        self.assertIn("連携できるアカウントは 1 個までです。", text)
        self.assertEqual(self.saved()["puuid"], PUUID)
        self.assertNotIn("others", self.saved())

    def test_an_account_riot_signed_out_says_so(self):
        self.both()
        self.switch(PUUID)
        # Riot が 2 つ目のアカウントの cookie を受け付けなくなり、1 時間のトークンも切れた
        self.riot.second, self.riot.refuse = "gone", 1
        self.switch(PUUID2)
        self.assertIn("Riot のログインが切れました", self.page("/collection"))
        text = self.page("/settings")
        self.assertIn("ログイン切れ。下の手順で貼り直してください。", text)
        self.assertIn('<details class="ssid-way" open>', text, "the steps are the next thing to do")
        self.switch(PUUID)
        self.assertIn("<small>ログイン切れ</small>", self.page("/"), "on its button too")
        self.assertIn("ヴァンダル", self.page("/collection"), "the other account still works")

    def test_each_account_has_its_own_store_diary(self):
        self.link("ssid-1")
        self.page("/shop")
        first = self.saved("diary.json")
        self.assertEqual(len(first["days"]), 1)
        self.link("sub-1")  # ここまで保存したものは 1 つ目のアカウントのもの
        self.assertEqual(self.saved("diary.json")["account"], PUUID)
        self.assertIn("まだ記録がありません", self.page("/shop/history"), "the second account has looked at no store")
        self.page("/shop")
        diary = self.saved("diary.json")
        self.assertEqual(diary["days"], first["days"])
        self.assertEqual(list(diary["more"]), [PUUID2])
        self.assertNotIn("まだ記録がありません", self.page("/shop/history"))
        self.switch(PUUID)
        self.assertNotIn("まだ記録がありません", self.page("/shop/history"))

    def test_each_account_has_its_own_sets(self):
        self.link("ssid-1")
        data = {"csrf": self.token("/collection?show=sets"), "do": "save", "name": "メインのセット"}
        self.client.post("/collection/sets", data=data)
        self.assertIn("メインのセット", self.page("/collection?show=sets"))
        self.link("sub-1")
        self.assertEqual(self.saved("loadout_sets.json")["sets"][0]["account"], PUUID)
        self.assertNotIn("メインのセット", self.page("/collection?show=sets"))
        data = {"csrf": self.token("/collection?show=sets"), "do": "save", "name": "サブのセット"}
        self.client.post("/collection/sets", data=data)
        sets = self.saved("loadout_sets.json")["sets"]
        self.assertEqual(
            [(item["name"], item["account"]) for item in sets], [("メインのセット", PUUID), ("サブのセット", PUUID2)]
        )
        self.switch(PUUID)
        text = self.page("/collection?show=sets")
        self.assertIn("メインのセット", text)
        self.assertNotIn("サブのセット", text)


class AppAccountsTest(unittest.TestCase):
    """PC アプリ: Riot Client のアカウントと、その横に連携したアカウント。"""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lockfile = Path(folder.name) / "lockfile"
        self.lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.local = LocalClient(self.lockfile, Path(folder.name) / "none.log", ask=FakeClient())
        self.app, _, self.folder = make_app(local=self.local)
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def token(self) -> str:
        page = self.client.get("/settings").get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def link(self, pasted: str = "sub-1"):
        return self.client.post("/settings", data={"csrf": self.token(), "do": "riot", "riot": pasted})

    def saved(self) -> dict:
        try:
            return json.loads((self.folder / "riot.json").read_text(encoding="utf-8"))
        except OSError:
            return {}

    def page(self, path: str) -> str:
        return self.client.get(path).get_data(as_text=True)

    def test_the_clients_account_alone(self):
        text = self.page("/settings")
        self.assertIn("<strong>Owner#JP1</strong><small>AP · Riot クライアント</small>", text)
        self.assertIn("使用中", text)
        self.assertNotIn('value="riot_off"', text, "nothing to unlink: it is the client's")
        self.assertNotIn('value="riot_use"', text)
        self.assertIn('<details class="ssid-way"><summary>ssid で別のアカウントを追加</summary>', text)
        self.assertNotIn('class="accounts"', self.page("/"))

    def test_a_linked_account_is_shown_while_the_game_stays_the_clients(self):
        self.assertIn("Riot アカウント Smurf#JP2 と連携しました。", self.link().get_data(as_text=True))
        saved = self.saved()
        self.assertEqual((saved["puuid"], saved["linked_first"]), (PUUID2, True))
        text = self.page("/")
        self.assertIn('href="/p/Smurf/JP2"><strong>戦績</strong>', text, "the pages show the linked account")
        self.assertIn(f'<button type="submit" name="account" value="{PUUID}">Owner', text)
        before = len(self.riot.asked)
        self.assertIn("ヴァンダル", self.page("/collection"))
        self.assertTrue(any(PUUID2 in url for _, url, _, _ in self.riot.asked[before:]), "its own collection")
        # この PC のゲームは、どれを表示していても Riot Client のアカウント
        self.riot.phase = "menus"
        before = len(self.riot.asked)
        live = self.page("/live")
        self.assertIn("ライブは Riot クライアントのアカウント（Owner#JP1）で動作します。", live)
        glz = [url for _, url, _, _ in self.riot.asked[before:] if "glz-" in url]
        self.assertTrue(glz)
        self.assertTrue(all(PUUID2 not in url for url in glz))
        self.assertNotIn('class="accounts"', live, "no choice on the game's own page")
        # 設定: 両方。クライアントのものに戻るボタンつき
        text = self.page("/settings")
        self.assertIn("<strong>Owner#JP1</strong><small>AP · Riot クライアント</small>", text)
        self.assertRegex(text, rf'name="account" value="{PUUID}"><button type="submit" name="do" value="riot_use"')
        self.assertRegex(text, rf'name="account" value="{PUUID2}"><button type="submit" name="do" value="riot_off"')
        # クライアントのアカウントに戻る
        self.client.post("/account", data={"csrf": self.token(), "account": PUUID, "next": "/"})
        self.assertNotIn("linked_first", self.saved())
        self.assertIn('href="/p/Owner/JP1"><strong>戦績</strong>', self.page("/"))
        self.assertNotIn("ライブは Riot クライアントのアカウント", self.page("/live"))

    def test_the_client_opening_again_is_followed_again(self):
        self.link()
        self.assertIn('href="/p/Smurf/JP2"><strong>戦績</strong>', self.page("/"))
        lockfile = self.lockfile.read_text(encoding="utf-8")
        self.lockfile.unlink()  # Riot Client が閉じている: 連携したアカウントだけ
        text = self.page("/settings")
        self.assertNotIn("Owner#JP1", text)
        self.assertIn("Smurf#JP2", text)
        self.assertTrue(self.saved()["linked_first"], "the choice holds while the client is closed")
        self.lockfile.write_text(lockfile, encoding="utf-8")  # また開いた: そのアカウントをまた表示する
        self.assertIn('href="/p/Owner/JP1"><strong>戦績</strong>', self.page("/"))
        self.assertNotIn("linked_first", self.saved())
        self.assertEqual(self.saved()["puuid"], PUUID2, "the linked account stays linked")

    def test_the_clients_account_linked_too_is_one_line(self):
        self.link("ssid-1")  # クライアントと同じアカウント（cookie つき）
        rows = self.app.config["VAL_RIOT"].accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["client"], rows[0]["linked"], rows[0]["current"]), (True, True, True))
        self.assertIn('value="riot_off"', self.page("/settings"))


if __name__ == "__main__":
    unittest.main()
