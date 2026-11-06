"""The owner's progress (/progress), the match being played (/live) and wearing from the collection."""

import os
import re
import time
import unittest
from datetime import UTC, datetime
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

    def test_each_press_is_sent_on_a_slow_clock(self):
        """Windows' clock moves in 16 ms steps: two presses in one step are still two presses."""
        self.link()
        self.riot.phase = "menus"
        riot = self.app.config["VAL_RIOT"]
        riot.cache = type(riot.cache)(64, clock=lambda: 100.0)
        self.client.post("/live/act", data={"csrf": self.token(), "do": "start", "value": "competitive"})
        self.client.post("/live/act", data={"csrf": self.token(), "do": "leave"})
        self.assertEqual(self.riot.pressed[-1], ("leave", ""))

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

    def state(self) -> dict:
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        answer = self.client.get("/live/state")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.headers["Cache-Control"], "no-store")
        return answer.get_json()

    def test_the_live_tab_on_every_page(self):
        text = self.page("/")
        self.assertIn('class="live-tab" lang="ja" data-live-state="/live/state"', text)
        self.assertNotIn("live-tab", self.page("/", environ_base=VISITOR), "the owner's only")
        for path in ("/live", "/progress"):
            text = self.page(path)
            self.assertIn('data-live-state="/live/state" aria-current="page"', text, path)
            self.assertNotIn('href="/shop" aria-current="page"', text, "no longer under the shop")
            self.assertIn('href="/progress" lang="ja"', text, "the two pages lead to each other")
        shop = self.page("/shop")
        self.assertNotIn('href="/live" lang="ja">', shop.split("<main")[1], "the shop's pages are the shop's")
        self.assertIn('href="/collection"', shop)
        tools = self.page("/tools")
        self.assertIn("<strong>ライブ</strong>", tools)
        self.assertIn("<strong>進み具合</strong>", tools)

    def test_where_the_game_is_for_the_tab(self):
        self.assertEqual(self.state()["problem"], "nologin")
        self.assertEqual(self.client.get("/live/state", environ_base=VISITOR).status_code, 404)
        self.link()
        self.assertEqual((self.state()["phase"], self.state()["words"]), ("", "VALORANT は開いていません"))
        self.riot.phase = "menus"
        self.assertEqual(self.state()["words"], "メニュー")
        self.riot.pd["party"]["State"] = "MATCHMAKING"
        self.assertEqual(self.state()["phase"], "queue")
        self.riot.phase = "pregame"
        found = self.state()
        self.assertEqual((found["phase"], found["map"]), ("pregame", "アセント"))
        self.assertEqual(found["words"], "エージェント選択中（アセント）")
        self.assertEqual(found["match"], self.riot.pd["pregame"]["ID"].lower(), "told once per match")
        self.riot.phase = "ingame"
        self.assertEqual(self.state()["words"], "試合中（アセント）")

    def test_the_live_page_says_what_it_does_on_each_screen(self):
        self.link()
        text = self.page("/live")
        self.assertIn("<h1>ライブ</h1>", text)
        self.assertIn('class="live-steps"', text)
        self.assertNotIn('aria-current="step"', text, "the game is closed")
        self.assertIn('data-live-phase=""', text)
        self.riot.phase = "pregame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn('<li aria-current="step"><strong>エージェント選択</strong>', text)
        self.assertIn('data-live-phase="pregame"', text, "the tab's dot without asking again")

    def test_the_top_pages_box(self):
        text = self.page("/")
        self.assertIn('<h2 id="me-title">自分</h2>', text)
        self.assertIn("設定</a>で連携すると", text)
        self.assertNotIn("<strong>自分の戦績</strong>", text, "not linked yet: whose page is not known")
        self.assertNotIn("me-title", self.page("/", environ_base=VISITOR))
        self.link()
        text = self.page("/")
        self.assertIn('href="/p/Owner/JP1"><strong>自分の戦績</strong><small>Owner#JP1</small>', text)
        self.assertIn("<small data-live-text>", text, "the game's state, from the tab's asking")
        self.assertNotIn("設定</a>で連携すると", text)

    def test_the_players_act_and_the_teams_rank(self):
        self.link()
        self.riot.phase = "pregame"
        text = self.page("/live")
        self.assertIn("このアクト 15勝 / 30試合（50%） · 最高 Tier 22", text, "a player who shows their name")
        self.assertNotIn("7勝 / 9試合", text, "nothing about a player who hides who they are")
        self.assertIn('味方 <small class="team-average">平均 Tier 17</small>', text, "(18 + 21 + 12) / 3")
        self.riot.phase = "ingame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertEqual(text.count('class="team-average"'), 1, "the other team has one rank: no average")
        self.assertIn("このアクト 15勝 / 30試合", text)

    def test_the_last_match_once_it_is_over(self):
        self.link()
        self.riot.phase = "ingame"
        self.assertNotIn("さっきの試合を見る", self.page("/live"))
        self.riot.phase = ""
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        match_id = self.riot.pd["coregame"]["MatchID"].lower()
        self.assertIn(f'href="/m/ap/{match_id}?me=Owner%23JP1">さっきの試合を見る', self.page("/live"))
        with self.client.session_transaction() as kept:
            kept["live_match"] = [match_id, time.time() - 4 * 3600]
        self.assertNotIn("さっきの試合を見る", self.page("/live"), "hours ago: not the last match any more")
        self.riot.phase = "ingame"
        self.state()  # played while another page was open: the tab's asking keeps it
        self.riot.phase = "menus"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertIn("さっきの試合を見る", self.page("/live"))

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

    def test_agent_select_names_the_agents_that_win_on_the_map(self):
        self.link()
        self.riot.phase = "pregame"
        jett, sova = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        self.riot.pd["entitlements"]["agents"].append(sova)
        started = time.time() - 3600
        games = []
        for number in range(8):  # on Ascent, Sova's team wins 6 of 8, Jett's 2 of 8
            game = {
                "metadata": {
                    "match_id": f"asc{number}",
                    "region": "ap",
                    "cluster": "Tokyo",
                    "queue": {"id": "competitive"},
                    "map": {"id": "7EAECC1B-4337-BBF6-6AB9-04B8F06B3319"},
                    "started_at": datetime.fromtimestamp(started - number * 60, UTC).isoformat(),
                    "game_length_in_ms": 1800000,
                },
                "teams": [{"team_id": "Red", "won": number < 6}, {"team_id": "Blue", "won": number >= 6}],
                "players": [
                    {"puuid": f"x{number}-{i}", "team_id": "Red" if i < 5 else "Blue", "tier": {"id": 12}}
                    | {"agent": {"id": sova if i == 0 else jett if i == 5 else f"y{i}"}}
                    for i in range(10)
                ],
            }
            games.append(game)
        self.app.config["VAL_ACTIVITY"].seen(games)
        text = self.page("/live")
        self.assertIn("このマップで勝っている: <strong>ソーヴァ</strong> 75%", text)
        self.assertIn("このサイトが見た 8 試合", text)
        self.assertIn('<small class="pick-win">75%</small>', text)
        self.assertNotIn("<strong>ジェット</strong> 25%", text, "a teammate has locked Jett")

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
