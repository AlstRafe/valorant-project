"""The owner's progress (/progress), the match being played (/live) and wearing from the collection."""

import os
import re
import unittest
from unittest import mock

from helpers import make_app

VISITOR = {"homepage.visitor": True}
PRIME = "aa000000-0000-4000-8000-000000000001"
REAVER = "aa000000-0000-4000-8000-000000000002"


class OwnerTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.riot = self.app.riot
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def token(self) -> str:
        page = self.client.get("/settings").get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def link(self):
        self.client.post("/settings", data={"csrf": self.token(), "do": "riot", "riot": "ssid-1"})

    def page(self, path: str, status: int = 200, **kwargs) -> str:
        answer = self.client.get(path, **kwargs)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def test_only_the_owners(self):
        for path in ("/progress", "/live"):
            self.page(path, 404, environ_base=VISITOR)
        answer = self.client.post("/collection/equip", data={"item": PRIME}, environ_base=VISITOR)
        self.assertIn(answer.status_code, (400, 404))
        self.assertIn("まだ連携されていません", self.page("/progress"))
        self.assertIn("まだ連携されていません", self.page("/live"))

    def test_progress(self):
        self.link()
        text = self.page("/progress")
        self.assertIn("エピソード 9 · ACT III", text)
        self.assertIn("バトルパス ACT III", text)
        self.assertIn("レベル 2 / 4", text, "the epilogue's levels are not counted in")
        self.assertIn("次のレベルまで 2,500 XP", text)
        self.assertIn('value="32"', text, "(2000 + 3000 + 1500) of 20000")
        self.assertNotIn("バトルパス ACT I<", text, "an act before is not this act's pass")
        self.assertIn("イベントパス", text)
        self.assertIn("ジェット", text, "an agent's contract by the agent's name")
        self.assertIn("完了", text)
        self.assertIn("<strong>1 / 3</strong> 完了", text)
        self.assertIn("週のミッションが補充されるのは", text)
        self.assertIn('href="/progress" lang="ja" aria-current="true"', text)

    def test_progress_when_riot_fails(self):
        self.link()
        self.riot.broken.add("contracts")
        self.assertIn('class="problem"', self.page("/progress", 503))

    def test_game_closed(self):
        self.link()
        text = self.page("/live")
        self.assertIn("VALORANT: 開いていません", text)
        self.assertIn('data-refresh="20"', text)
        self.assertNotIn('action="/live/act"', text)

    def test_party_in_the_menus_and_starting_a_queue(self):
        self.link()
        self.riot.phase = "menus"
        text = self.page("/live")
        self.assertIn("VALORANT: メニュー", text)
        self.assertIn("ゴッド", text, "the owner's title")
        self.assertIn("リーダー", text)
        self.assertIn("名前を隠しています", text)
        self.assertNotIn("ルーキー", text, "a hidden player's title is left out")
        self.assertNotIn("cc000000-0000-4000-8000-000000000002", text, "and their card")
        self.assertIn('<option value="unrated" selected>アンレート</option>', text)
        self.assertIn('<option value="competitive">コンペティティブ</option>', text)
        answer = self.client.post("/live/act", data={"csrf": self.token(), "do": "start", "value": "competitive"})
        self.assertEqual(answer.status_code, 303)
        self.assertEqual(self.riot.pressed, [("queue", "competitive"), ("join", "")])
        text = self.page("/live")
        self.assertIn("キューを開始しました", text)
        self.assertIn("VALORANT: マッチング中", text)
        self.assertIn("<strong>コンペティティブ</strong> でマッチング中", text)
        self.assertNotIn("キューを開始しました", self.page("/live"), "said once")
        self.client.post("/live/act", data={"csrf": self.token(), "do": "leave"})
        self.assertEqual(self.riot.pressed[-1], ("leave", ""))
        self.assertIn("キューをやめました", self.page("/live"))

    def test_buttons_refused(self):
        self.link()
        token = self.token()
        for data in ({"do": "start", "value": "../x"}, {"do": "quit"}, {"do": "lock", "value": "not-a-uuid"}):
            self.client.post("/live/act", data={"csrf": token, **data})
            self.assertIn("そのボタンは使えません", self.page("/live"))
        self.client.post("/live/act", data={"csrf": token, "do": "leave"})
        self.assertIn("いまはできません", self.page("/live"), "the game is closed: no party")
        self.assertEqual(self.riot.pressed, [])
        self.assertEqual(self.client.post("/live/act", data={"do": "leave"}).status_code, 400, "no csrf")
        visitor = self.client.post("/live/act", data={"csrf": token, "do": "leave"}, environ_base=VISITOR)
        self.assertIn(visitor.status_code, (403, 404))

    def test_the_party_when_the_session_cannot_be_read(self):
        self.link()
        self.riot.phase = "menus"
        self.riot.no_session = True
        self.assertIn("VALORANT: メニュー", self.page("/live"))

    def test_agent_select(self):
        self.link()
        self.riot.phase = "pregame"
        text = self.page("/live")
        self.assertIn("エージェント選択", text)
        self.assertIn("アセント", text)
        self.assertIn("コンペティティブ", text)
        self.assertIn("残り 61 秒", text)
        self.assertIn("Teammate", text)
        self.assertIn("ジェット（確定）", text)
        self.assertNotIn("Secret", text, "a hidden name stays hidden")
        self.assertIn("名前を隠しています", text)
        self.assertNotIn("レベル 50", text, "a hidden level too")
        self.assertIn("相手は 2 / 5 人が確定", text)
        self.assertIn("イニシエーター", text, "a role the team lacks")
        self.assertIn("持っているエージェントでは埋められません", text, "the owner has no initiator")
        self.riot.pd["entitlements"]["agents"].append("22222222-2222-4222-8222-222222222222")
        self.app.config["VAL_RIOT"].cache = type(self.app.config["VAL_RIOT"].cache)(8)
        text = self.page("/live")
        self.assertIn("ソーヴァ", text, "the owner's agent for it")
        self.assertNotIn("<strong>デュエリスト</strong>", text, "a role a teammate already plays")
        names = [json for method, url, _, json in self.riot.asked if url.endswith("/name-service/v2/players")]
        self.assertNotIn(b"a1111111-0000-4000-8000-000000000002", names[0], "a hidden name is not even asked")
        self.assertIn('data-refresh="5"', text)

    def test_pick_and_lock(self):
        self.link()
        self.riot.phase = "pregame"
        jett, sova = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        self.riot.pd["entitlements"]["agents"].append(sova)
        text = self.page("/live")
        self.assertIn('aria-label="ソーヴァ を選ぶ"', text)
        self.assertIn("味方が確定済み", text, "the teammate's locked agent cannot be picked")
        self.assertNotIn("で確定</button>", text, "nothing picked yet")
        self.client.post("/live/act", data={"csrf": self.token(), "do": "select", "value": sova})
        text = self.page("/live")
        self.assertIn("選びました。まだ確定していません", text)
        self.assertIn("ソーヴァ で確定</button>", text)
        self.client.post("/live/act", data={"csrf": self.token(), "do": "lock", "value": sova})
        self.assertEqual(self.riot.pressed, [("select", sova), ("lock", sova)])
        text = self.page("/live")
        self.assertIn("<strong>ソーヴァ</strong> で確定しています", text)
        self.assertNotIn('class="pick-grid"', text)
        self.assertNotIn(jett + '" class="pick', text)

    def test_in_the_match(self):
        self.link()
        self.riot.phase = "ingame"
        text = self.page("/live")
        self.assertIn("試合中", text)
        self.assertIn("アンレート", text)
        self.assertLess(text.index("味方"), text.index("相手"))
        self.assertLess(text.index("Owner"), text.index("Teammate"), "our team first")
        self.assertIn('href="/p/Teammate/0001"', text)
        self.assertNotIn("Secret", text)
        self.assertNotIn("a1111111-0000-4000-8000-000000000003", text)
        self.assertEqual(len(re.findall(r'<li class="seat[ "]', text)), 3, "a coach has no seat")
        self.assertIn("47 RR", text, "the rank this act, from PD")
        self.assertIn("5 RR", text, "a hidden player's rank shows, as in the game")
        self.assertIn("ゴッド", text)
        self.assertNotIn("ルーキー", text, "a hidden player's title does not")
        ranks = sum("/mmr/v1/players/" in url for _, url, _, _ in self.riot.asked)
        self.app.config["VAL_RIOT"].cache = type(self.app.config["VAL_RIOT"].cache)(8)
        self.page("/live")
        self.assertEqual(sum("/mmr/v1/players/" in url for _, url, _, _ in self.riot.asked), ranks, "asked once")

    def test_wear_a_skin(self):
        self.link()
        text = self.page("/collection")
        self.assertIn('name="item" value="' + REAVER + '"', text, "a skin not worn")
        answer = self.client.post(
            "/collection/equip",
            data={
                "csrf": self.token(),
                "show": "skins",
                "item": PRIME,
                f"chroma.{PRIME}": "aa000000-0000-4000-8000-000000000021",
            },
        )
        self.assertEqual(answer.status_code, 303)
        self.assertIn("equip=ok", answer.headers["Location"])
        (worn,) = self.riot.worn
        vandal = next(gun for gun in worn["Guns"] if gun["ID"] == "9c82e19d-4575-0200-1a81-3eacf00cf872")
        self.assertEqual(vandal["SkinID"], PRIME)
        self.assertEqual(vandal["ChromaID"], "aa000000-0000-4000-8000-000000000021", "the base variant")
        self.assertEqual(vandal["CharmID"], "bb000000-0000-4000-8000-000000000001", "the rest as it was")
        self.assertNotIn("Version", worn)
        self.assertEqual(len(worn["Sprays"]), 1)
        self.assertIn("装備しました", self.page("/collection?equip=ok"))

    def test_wear_only_what_is_owned(self):
        self.link()
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "skins", "item": "ac000000-0000-4000-8000-000000000001"},
        )
        self.assertIn("equip=unknown", answer.headers["Location"])
        self.assertEqual(self.riot.worn, [])
        self.assertIn("持っていないものは装備できません", self.page("/collection?equip=unknown"))

    def test_wear_a_card(self):
        self.link()
        text = self.page("/collection?show=cards")
        found = re.findall(r'name="item" value="([0-9a-f-]{36})"', text)
        self.assertTrue(found, "a card not worn has its button")
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "cards", "item": found[0]})
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertEqual(self.riot.worn[0]["Identity"]["PlayerCardID"], found[0])

    def test_wear_a_spray(self):
        self.link()
        text = self.page("/collection?show=sprays")
        self.assertIn('name="slot.dd000000-0000-4000-8000-000000000001"', text)
        self.assertIn("1（いま GG スプレー）", text, "each place of the wheel with the spray in it")
        answer = self.client.post(
            "/collection/equip",
            data={
                "csrf": self.token(),
                "show": "sprays",
                "item": "dd000000-0000-4000-8000-000000000001",
                "slot.dd000000-0000-4000-8000-000000000001": "x",
            },
        )
        self.assertIn("show=sprays", answer.headers["Location"])
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertEqual(self.riot.worn[0]["Sprays"][0]["SprayID"], "dd000000-0000-4000-8000-000000000001")
        answer = self.client.post(
            "/collection/equip",
            data={
                "csrf": self.token(),
                "show": "sprays",
                "item": "dd000000-0000-4000-8000-000000000001",
                "slot.dd000000-0000-4000-8000-000000000001": "not-a-slot",
            },
        )
        self.assertIn("equip=unknown", answer.headers["Location"], "only a place the wheel has")
        self.assertEqual(len(self.riot.worn), 1)


if __name__ == "__main__":
    unittest.main()
