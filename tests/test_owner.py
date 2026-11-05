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

    def test_no_match(self):
        self.link()
        text = self.page("/live")
        self.assertIn("いまは試合をしていません", text)
        self.assertIn('data-refresh="20"', text)

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


if __name__ == "__main__":
    unittest.main()
