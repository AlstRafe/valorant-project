"""The owner's progress (/progress), the match being played (/live) and wearing from the collection."""

import json
import os
import re
import time
import unittest
from datetime import UTC, datetime
from unittest import mock

from helpers import PUUID, make_app

from valtracker import owner
from valtracker.riot import ITEM_TYPES

VISITOR = {"homepage.visitor": True}
PRIME = "aa000000-0000-4000-8000-000000000001"
REAVER = "aa000000-0000-4000-8000-000000000002"
VANDAL = "9c82e19d-4575-0200-1a81-3eacf00cf872"
CLASSIC = "29a0cfab-485b-f5d5-779a-b59f85e204a8"
CHILI = "bb000000-0000-4000-8000-000000000001"
HEART = "bb000000-0000-4000-8000-000000000002"


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
        self.assertIn('href="/progress" aria-current="true"', text)

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
        self.assertIn("コンペティティブ でマッチング中", text)
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
        self.assertIn('class="live-tab" data-live-state="/live/state"', text)
        self.assertNotIn("live-tab", self.page("/", environ_base=VISITOR), "the owner's only")
        for path in ("/live", "/progress"):
            text = self.page(path)
            self.assertIn('data-live-state="/live/state" aria-current="page"', text, path)
            self.assertNotIn('href="/shop" aria-current="page"', text, "no longer under the shop")
            self.assertIn(
                '<nav class="modes" aria-label="ページ"><a href="/live"', text, "the two pages lead to each other"
            )
            self.assertIn('<a href="/progress"', text)
        shop = self.page("/shop")
        self.assertNotIn('aria-label="ページ"><a href="/live"', shop, "the shop's pages are the shop's")
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
        self.assertEqual(self.state()["words"], "対戦中（アセント）")

    def look(self) -> int:
        """Riot's calls of one look at the game (riot.Riot.live), asked anew."""
        riot = self.app.config["VAL_RIOT"]
        riot.cache.put("live", None, 0)
        before = len(self.riot.asked)
        riot.live()
        return len(self.riot.asked) - before

    def test_a_found_match_is_told_at_once(self):
        """DailyVal shows agent select from the game's 5-second count when the match is found: so does /live, without
        waiting for the session to say agent select."""
        self.link()
        self.riot.phase = "menus"
        party = self.riot.pd["party"]
        party["State"] = "MATCHMAKING"
        found = self.state()
        self.assertEqual((found["phase"], found["every"]), ("queue", 2), "matchmaking: every two seconds")
        self.assertTrue(found["icons"], "the agents' pictures to fetch ahead")
        self.assertTrue(all(url.startswith("/media/agents/") for url in found["icons"]), found["icons"])
        self.assertIn('data-refresh="2"', self.page("/live"))
        self.assertIn('data-warm="/media/agents/', self.page("/live"))
        party["State"] = "MATCHMADE_GAME_STARTING"  # the match is found; agent select not made yet
        found = self.state()
        self.assertEqual((found["phase"], found["words"], found["every"]), ("found", "試合が見つかりました", 1))
        text = self.page("/live")
        self.assertIn("試合が見つかりました。まもなくエージェント選択です。", text)
        self.assertIn('data-refresh="1"', text, "every second until agent select")
        self.riot.early_pregame = True  # agent select made: shown while the session still says the menus
        found = self.state()
        self.assertEqual((found["phase"], found["map"]), ("pregame", "アセント"))
        self.assertEqual(found["icons"], [])
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn('class="pick-grid"', text)
        self.assertIn("残り 61 秒", text)
        self.assertNotIn("data-warm", text)

    def test_agent_select_opens_without_reading_the_collection(self):
        """The owner's agents are read while matchmaking (one call, kept), not the whole collection at agent
        select: the pick screen opens without waiting for ten calls."""
        self.link()
        self.riot.phase = "menus"
        self.riot.pd["party"]["State"] = "MATCHMAKING"
        self.state()
        agents = f"/store/v1/entitlements/0c0c0c0c-1111-4222-8333-444444444444/{ITEM_TYPES['agents']}"
        self.assertEqual(sum(url.endswith(agents) for _, url, _, _ in self.riot.asked), 1, "read ahead")
        self.riot.phase = "pregame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        before = len(self.riot.asked)
        self.assertIn('class="pick-grid"', self.page("/live"))
        asked = [url for _, url, _, _ in self.riot.asked[before:]]
        self.assertFalse([url for url in asked if "/store/" in url or "playerloadout" in url], asked)

    def test_each_look_asks_riot_little(self):
        """Matchmaking and agent select are looked at every second or two: each look asks Riot one or two
        things (the session, and the party or agent select remembered from the last look), names once."""
        self.link()
        self.riot.phase = "menus"
        self.riot.pd["party"]["State"] = "MATCHMAKING"
        self.look()
        self.assertEqual(self.look(), 2, "the session and the party")
        self.riot.phase = "pregame"
        self.look()
        self.assertEqual(self.look(), 2, "the session and agent select")
        names = sum(url.endswith("/name-service/v2/players") for _, url, _, _ in self.riot.asked)
        self.assertEqual(names, 2, "the party's names, then the team's: each player asked once")
        self.riot.phase = "ingame"
        self.assertNotEqual(self.state()["phase"], "pregame", "the session says the match: agent select is over")

    def test_pictures_are_kept_whatever_the_cookie(self):
        """A picture answered with "Vary: Cookie" is kept by the browser only until the session cookie changes:
        the agents' squares were fetched again and again."""
        self.link()
        fresh = self.client.get("/media/agents/11111111-1111-4111-8111-111111111111/displayicon.png")
        self.assertNotIn("Cookie", fresh.headers.get("Vary", ""))
        self.assertIn("max-age=2592000", fresh.headers["Cache-Control"])
        self.assertIn("Cookie", self.client.get("/live").headers.get("Vary", ""), "the pages are the owner's")
        self.riot.phase = "ingame"
        self.state()
        answer = self.client.get("/live/state")
        self.assertNotIn("Set-Cookie", answer.headers, "the match is kept once, not written at every look")

    def test_the_small_pictures(self):
        from valtracker.live import icon

        small = "https://media.valorant-api.com/agents/x/displayiconsmall.png"
        self.assertEqual(icon({"displayIcon": "big", "displayIconSmall": small}), small)
        self.assertEqual(icon({"displayIcon": "big"}), "big")

    def test_the_live_page_says_what_it_does_on_each_screen(self):
        self.link()
        text = self.page("/live")
        self.assertIn("<h1>ライブ</h1>", text)
        self.assertIn('class="live-steps"', text)
        self.assertNotIn('aria-current="step"', text, "the game is closed")
        self.assertIn('data-live-phase=""', text)
        self.riot.phase = "menus"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn('<li aria-current="step"><strong>メニュー</strong>', text)
        self.assertIn('data-live-phase="menus"', text, "the tab's dot without asking again")

    def test_the_top_pages_box(self):
        text = self.page("/")
        self.assertIn('<h2 id="me-title">自分</h2>', text)
        self.assertIn("設定で連携すると", text)
        self.assertNotIn("<strong>自分の戦績</strong>", text, "not linked yet: whose page is not known")
        self.assertNotIn("me-title", self.page("/", environ_base=VISITOR))
        self.link()
        text = self.page("/")
        self.assertIn('href="/p/Owner/JP1"><strong>自分の戦績</strong><small>Owner#JP1</small>', text)
        self.assertIn("<small data-live-text>", text, "the game's state, from the tab's asking")
        self.assertNotIn("設定で連携すると", text)

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

    def played(self, queue: str = "competitive") -> str:
        """The owner's match is over and Riot's server has its record: the match's id."""
        match_id = self.riot.pd["coregame"]["MatchID"].lower()
        other = self.riot.pd["player"]
        record = json.loads(json.dumps(self.riot.pd["matches"]["bbbbbbbb-0000-4000-8000-000000000001"]))
        record["matchInfo"] |= {"matchId": match_id, "queueID": queue}
        record = json.loads(json.dumps(record).replace(other, PUUID))
        self.riot.pd["matches"][match_id] = record
        self.riot.pd["player"] = PUUID
        self.riot.pd["updates"]["Matches"][0]["MatchID"] = match_id
        self.riot.phase = "ingame"
        self.page("/live")
        self.riot.phase = "menus"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        return match_id

    def test_the_last_matchs_result_the_moment_it_ends(self):
        """From the game's own server: no waiting for HenrikDev, no key needed."""
        self.link()
        match_id = self.played()
        text = self.page("/live")
        self.assertIn('<h2 id="last-title">さっきの試合</h2>', text)
        self.assertIn(f'<li class="match win"><a href="/m/ap/{match_id}?me=Owner%23JP1">', text)
        self.assertIn("<strong>2–1</strong>", text)
        self.assertIn('<span class="up">+18 RR</span>', text)
        self.assertIn("<strong>3 / 1 / 1</strong>", text)
        self.assertIn("コンペティティブ", text)
        self.assertNotIn("さっきの試合を見る", text, "the result is the link")
        for name in ("Secret Mate", "Foe One", "Pd Player"):
            self.assertNotIn(name, text, "Riot's record names everyone: no one else's name is shown")
        asked = len(self.riot.asked)
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.page("/live")
        after = [url for _, url, _, _ in self.riot.asked[asked:]]
        self.assertFalse([url for url in after if "/match-details/" in url or "competitiveupdates" in url], "kept")
        # in the owner's lifetime numbers at once
        self.assertEqual(self.app.config["VAL_ARCHIVE"].known([match_id]), {match_id})
        self.assertEqual(self.app.config["VAL_ARCHIVE"].rr(PUUID)[match_id]["change"], 18)

    def test_the_result_waits_for_riot(self):
        self.link()
        match_id = self.played()
        record = self.riot.pd["matches"].pop(match_id)
        text = self.page("/live")
        self.assertNotIn('id="last-title"', text)
        self.assertIn("さっきの試合を見る", text, "the link as before")
        self.riot.pd["matches"][match_id] = record
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertNotIn('id="last-title"', self.page("/live"), "asked again only after a while")
        self.app.config["VAL_LAST_RESULT"]["tried"] = 0.0
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertIn('id="last-title"', self.page("/live"))

    def test_riot_is_asked_for_a_result_a_few_times_only(self):
        """The Range is a "match" without a record."""
        self.link()
        match_id = self.played()
        del self.riot.pd["matches"][match_id]
        for _ in range(owner.RESULT_TRIES + 3):
            self.app.config["VAL_RIOT"].cache.put("live", None, 0)
            self.assertNotIn('id="last-title"', self.page("/live"))
            self.app.config["VAL_LAST_RESULT"]["tried"] = 0.0
        asked = [url for _, url, _, _ in self.riot.asked if "/match-details/" in url]
        self.assertEqual(len(asked), owner.RESULT_TRIES)

    def test_a_result_without_rr(self):
        self.link()
        self.played("unrated")
        text = self.page("/live")
        self.assertIn('id="last-title"', text)
        self.assertNotIn(" RR</span>", text)
        self.assertFalse([url for _, url, _, _ in self.riot.asked if "competitiveupdates" in url], "not asked for")

    def test_nothing_explained_again_during_a_match(self):
        """Agent select and a match: the lobby takes the screen. What the page does is said while the game is
        closed and in the menus."""
        self.link()
        for phase, wide in (("", False), ("menus", False), ("pregame", True), ("ingame", True)):
            self.riot.phase = phase
            self.app.config["VAL_RIOT"].cache.put("live", None, 0)
            text = self.page("/live")
            self.assertEqual('class="live-wide"' in text, wide, phase)
            self.assertEqual('class="live-steps"' in text, not wide, phase)
            self.assertEqual("上のタブ「ライブ」の点で" in text, not wide, phase)
            self.assertEqual('<p class="game-state sr">' in text, wide, phase)

    def test_hidden_names_are_explained_only_when_someone_hides_theirs(self):
        self.link()
        self.riot.phase = "ingame"
        self.assertIn("本人が選んだ設定のため", self.page("/live"))
        for player in self.riot.pd["coregame"]["Players"]:
            player["PlayerIdentity"]["Incognito"] = False
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertNotIn("本人が選んだ設定のため", self.page("/live"))

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
        self.assertIn('data-refresh="2"', text, "agent select: asked every two seconds")

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
        self.assertIn("ソーヴァ で確定しています", text)
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
        self.assertIn("対戦中", text)
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
        text = self.page("/collection?weapon=9c82e19d-4575-0200-1a81-3eacf00cf872")
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
        self.assertIn("weapon=9c82e19d-4575-0200-1a81-3eacf00cf872", answer.headers["Location"], "back to that weapon")
        (worn,) = self.riot.worn
        vandal = next(gun for gun in worn["Guns"] if gun["ID"] == "9c82e19d-4575-0200-1a81-3eacf00cf872")
        self.assertEqual(vandal["SkinID"], PRIME)
        self.assertEqual(vandal["ChromaID"], "aa000000-0000-4000-8000-000000000021", "the base variant")
        self.assertEqual(vandal["CharmID"], "bb000000-0000-4000-8000-000000000001", "the rest as it was")
        self.assertNotIn("Version", worn)
        self.assertNotIn("Subject", worn, "PD's body is the loadout without Subject and Version")
        self.assertEqual(len(worn["ActiveExpressions"]), 2)
        self.assertEqual(worn["DynamicOptions"], {})
        (put,) = [url for method, url, _, _ in self.riot.asked if method == "PUT" and "playerloadout" in url]
        self.assertIn("/personalization/v3/", put, "back where the loadout came from")
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
        self.assertIn(f'<button type="submit" class="tile" name="item" value="{found[0]}"', text, "pressing the card")
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "cards", "item": found[0]})
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertNotIn("weapon=", answer.headers["Location"])
        self.assertEqual(self.riot.worn[0]["Identity"]["PlayerCardID"], found[0])
        # the cards stay where they were: only the mark moves
        after = self.page("/collection?show=cards&equip=ok")
        self.assertEqual(re.findall(r'data-name="([^"]+)"', after), re.findall(r'data-name="([^"]+)"', text))
        self.assertNotIn(f'value="{found[0]}"', after, "now worn")

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
                "slot.dd000000-0000-4000-8000-000000000001": "0",
            },
        )
        self.assertIn("show=sprays", answer.headers["Location"])
        self.assertIn("equip=ok", answer.headers["Location"])
        wheel = self.riot.worn[0]["ActiveExpressions"]
        self.assertEqual(wheel[0]["AssetID"], "dd000000-0000-4000-8000-000000000001")
        self.assertEqual(wheel[1]["AssetID"], "ff000000-0000-4000-8000-000000000001", "the flex stays")
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

    def test_wear_with_the_loadout_v2(self):
        """Where PD still has playerloadout v2 only (v3 answers 404), wearing goes back to v2."""
        self.riot.loadout_version = 2
        loadout = self.riot.pd["loadout"]
        del loadout["ActiveExpressions"], loadout["DynamicOptions"]
        loadout["Sprays"] = [
            {"EquipSlotID": "x", "SprayID": "dd000000-0000-4000-8000-000000000001", "SprayLevelID": None}
        ]
        self.link()
        spray = "dd000000-0000-4000-8000-000000000001"
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "sprays", "item": spray, f"slot.{spray}": "x"},
        )
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertEqual(self.riot.worn[0]["Sprays"][0]["SprayID"], spray)
        (put,) = [url for method, url, _, _ in self.riot.asked if method == "PUT" and "playerloadout" in url]
        self.assertIn("/personalization/v2/", put)

    def test_wear_when_the_loadout_cannot_be_read(self):
        self.riot.loadout_version = 1
        self.link()
        title = "ee000000-0000-4000-8000-000000000002"
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=down", answer.headers["Location"])
        self.assertEqual(self.riot.worn, [])
        self.assertIn("Riot のサーバーから答えがありませんでした", self.page("/collection?show=titles&equip=down"))

    def test_wear_that_pd_does_not_keep(self):
        """PD answers 200 but with the loadout it had: the page does not say it was worn."""
        self.link()
        self.riot.ignore_put = True
        title = "ee000000-0000-4000-8000-000000000002"
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=notsaved", answer.headers["Location"])
        text = self.page(answer.headers["Location"])
        self.assertIn("Riot は受け付けましたが、装備は変わっていませんでした。", text)
        self.assertIn("<code>equip: PD answered 200 with another loadout</code>", text)
        self.assertIn('class="verdict no equip-said" role="alert"', text)

    def test_wear_refused_says_what_riot_said(self):
        self.link()
        self.riot.broken.add("equip")
        title = "ee000000-0000-4000-8000-000000000002"
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=down", answer.headers["Location"])
        text = self.page(answer.headers["Location"])
        self.assertIn("<code>equip 500 SERVER_ERROR</code>", text)
        # the words are only shown with a failure, and only plain ones
        ok = self.page("/collection?show=titles&equip=ok&riot=x")
        self.assertNotIn("<code>x</code>", ok)
        self.assertIn("装備しました。Riot に保存されています。", ok)

    def test_wear_while_the_game_is_open(self):
        """The open game draws the loadout it started with: the note says the next match takes the new one."""
        self.link()
        title = "ee000000-0000-4000-8000-000000000002"
        closed = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=ok", closed.headers["Location"])
        self.riot.phase = "menus"
        title = "ee000000-0000-4000-8000-000000000001"
        open_ = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=okopen", open_.headers["Location"])
        text = self.page(open_.headers["Location"])
        self.assertIn("開いているゲームでは次の試合から使われます", text)
        self.assertIn('class="verdict ok equip-said" role="status"', text)

    def gun(self, weapon: str, loadout: dict | None = None) -> dict:
        loadout = loadout if loadout is not None else self.riot.worn[-1]
        return next(gun for gun in loadout["Guns"] if gun["ID"] == weapon)

    def test_put_a_buddy_on_a_weapon(self):
        """The open weapon's buddy, as the game's weapon screen changes it: a free copy goes on."""
        self.link()
        text = self.page(f"/collection?weapon={CLASSIC}")
        self.assertIn(f'name="buddy.{CLASSIC}"', text)
        self.assertIn("チリ バディー（あと 1 個）", text, "two copies, one on the Vandal")
        self.assertIn("ハート バディー（あと 1 個）", text)
        self.assertNotIn('name="unbuddy"', text, "nothing to take off")
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "skins", "buddy": CLASSIC, f"buddy.{CLASSIC}": HEART},
        )
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertIn(f"weapon={CLASSIC}", answer.headers["Location"], "back to the same weapon")
        classic = self.gun(CLASSIC)
        self.assertEqual(
            (classic["CharmInstanceID"], classic["CharmID"], classic["CharmLevelID"]),
            ("i3", HEART, "bb000000-0000-4000-8000-000000000012"),
        )
        self.assertEqual(self.gun(VANDAL)["CharmInstanceID"], "i1", "the Vandal keeps its own")
        text = self.page(f"/collection?weapon={CLASSIC}")
        self.assertIn("バディー：ハート バディー", text, "what the weapon wears now")
        self.assertIn("ハート バディー（付けている）", text)
        self.assertIn('name="unbuddy"', text)
        text = self.page(f"/collection?weapon={VANDAL}")
        self.assertIn("ハート バディー（クラシック から付け替え）", text, "its one copy is on the Classic")

    def test_a_buddy_with_every_copy_on_moves(self):
        self.link()
        token = self.token()
        self.client.post(
            "/collection/equip", data={"csrf": token, "show": "skins", "buddy": CLASSIC, f"buddy.{CLASSIC}": HEART}
        )
        self.client.post(
            "/collection/equip", data={"csrf": token, "show": "skins", "buddy": VANDAL, f"buddy.{VANDAL}": HEART}
        )
        self.assertEqual(self.gun(VANDAL)["CharmInstanceID"], "i3")
        self.assertNotIn("CharmID", self.gun(CLASSIC), "the one copy came off the Classic")
        self.assertNotIn("CharmInstanceID", self.gun(CLASSIC))

    def test_take_a_buddy_off(self):
        self.link()
        self.assertIn('name="unbuddy"', self.page(f"/collection?weapon={VANDAL}"))
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "skins", "unbuddy": VANDAL})
        self.assertIn("equip=ok", answer.headers["Location"])
        for key in ("CharmInstanceID", "CharmID", "CharmLevelID"):
            self.assertNotIn(key, self.gun(VANDAL))

    def test_a_buddy_only_on_a_gun_and_only_one_owned(self):
        self.link()
        token = self.token()
        knife = "2f59173c-4bed-b6c3-2191-dea9b58be9c7"
        answer = self.client.post(
            "/collection/equip", data={"csrf": token, "show": "skins", "buddy": knife, f"buddy.{knife}": HEART}
        )
        self.assertIn("equip=melee", answer.headers["Location"])
        self.assertIn("近接武器にはガンバディーを付けられません", self.page(answer.headers["Location"]))
        answer = self.client.post(
            "/collection/equip",
            data={
                "csrf": token,
                "show": "skins",
                "buddy": VANDAL,
                f"buddy.{VANDAL}": "bb000000-0000-4000-8000-000000000099",
            },
        )
        self.assertIn("equip=unknown", answer.headers["Location"])
        self.assertEqual(self.riot.worn, [])

    def test_put_a_buddy_on_from_its_tab(self):
        self.link()
        text = self.page("/collection?show=buddies")
        self.assertIn(f'name="weapon.{HEART}"', text)
        self.assertIn("ヴァンダル（いま チリ バディー）", text)
        self.assertNotIn(">近接武器<", text, "no buddy on a knife")
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "buddies", "item": HEART, f"weapon.{HEART}": VANDAL},
        )
        self.assertIn("show=buddies", answer.headers["Location"])
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertEqual((self.gun(VANDAL)["CharmID"], self.gun(VANDAL)["CharmInstanceID"]), (HEART, "i3"))

    def test_wear_a_flex(self):
        """playerloadout v3 lets any place of the wheel hold a spray or a flex; one already there swaps places."""
        self.link()
        flex = "ff000000-0000-4000-8000-000000000001"
        text = self.page("/collection?show=flex")
        self.assertIn(f'name="slot.{flex}"', text)
        self.assertIn("1（いま GG スプレー）", text)
        self.assertIn("2（いま ぬいぐるみ）", text)
        answer = self.client.post(
            "/collection/equip", data={"csrf": self.token(), "show": "flex", "item": flex, f"slot.{flex}": "0"}
        )
        self.assertIn("show=flex", answer.headers["Location"])
        self.assertIn("equip=ok", answer.headers["Location"])
        wheel = self.riot.worn[-1]["ActiveExpressions"]
        self.assertEqual((wheel[0]["TypeID"], wheel[0]["AssetID"]), (ITEM_TYPES["flex"], flex))
        self.assertEqual(
            (wheel[1]["TypeID"], wheel[1]["AssetID"]),
            (ITEM_TYPES["sprays"], "dd000000-0000-4000-8000-000000000001"),
            "the spray took the flex's place",
        )

    def test_no_flex_on_the_loadout_v2(self):
        self.riot.loadout_version = 2
        self.riot.pd["loadout"].pop("ActiveExpressions")
        self.riot.pd["loadout"]["Sprays"] = [
            {"EquipSlotID": "slot-a", "SprayID": "dd000000-0000-4000-8000-000000000001", "SprayLevelID": None}
        ]
        self.link()
        self.assertNotIn('name="slot.', self.page("/collection?show=flex"), "v2's wheel had sprays only")

    def test_save_and_wear_a_set(self):
        self.link()
        text = self.page("/collection?show=sets")
        self.assertIn("いまの装備を保存", text)
        self.assertIn("まだありません", text)
        answer = self.client.post(
            "/collection/sets", data={"csrf": self.token(), "do": "save", "name": "  ランク  用 "}
        )
        self.assertIn("show=sets", answer.headers["Location"])
        self.assertIn("equip=saved", answer.headers["Location"])
        saved = json.loads((self.folder / "loadout_sets.json").read_text(encoding="utf-8"))["sets"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "ランク 用")
        self.assertEqual(self.gun(VANDAL, saved[0])["SkinID"], PRIME)
        self.assertEqual(self.gun(VANDAL, saved[0])["CharmInstanceID"], "i1")
        self.assertEqual(len(saved[0]["ActiveExpressions"]), 2)
        self.assertEqual(saved[0]["Identity"]["PlayerCardID"], "cc000000-0000-4000-8000-000000000001")
        text = self.page(answer.headers["Location"])
        self.assertIn("いまの装備をセットに保存しました", text)
        self.assertIn("ランク 用", text)
        self.assertIn('<span class="badge">いまの装備</span>', text, "it is what is worn")
        self.assertIn("<strong>ヴァンダル</strong> プライム ヴァンダル · チリ バディー", text)
        self.assertIn(">セット <small>1</small>", text, "the tab counts the sets")
        # something else is worn, then the set brings it all back
        token = self.token()
        self.client.post("/collection/equip", data={"csrf": token, "show": "skins", "item": REAVER})
        self.client.post("/collection/equip", data={"csrf": token, "show": "skins", "unbuddy": VANDAL})
        self.client.post(
            "/collection/equip", data={"csrf": token, "show": "titles", "item": "ee000000-0000-4000-8000-000000000002"}
        )
        self.assertNotIn('<span class="badge">いまの装備</span>', self.page("/collection?show=sets"))
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "use", "set": saved[0]["id"]})
        self.assertIn("equip=ok", answer.headers["Location"])
        vandal = self.gun(VANDAL)
        self.assertEqual((vandal["SkinID"], vandal["CharmInstanceID"], vandal["CharmID"]), (PRIME, "i1", CHILI))
        self.assertEqual(self.riot.worn[-1]["Identity"]["PlayerTitleID"], "ee000000-0000-4000-8000-000000000001")
        self.assertIn('<span class="badge">いまの装備</span>', self.page("/collection?show=sets"))
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "delete", "set": saved[0]["id"]})
        self.assertIn("equip=deleted", answer.headers["Location"])
        self.assertEqual(json.loads((self.folder / "loadout_sets.json").read_text(encoding="utf-8"))["sets"], [])
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "use", "set": saved[0]["id"]})
        self.assertIn("equip=noset", answer.headers["Location"])

    def test_a_set_is_read_from_riot_now(self):
        """A set holds what is worn this moment, not the collection's kept answer (changed in the game since)."""
        self.link()
        self.page("/collection")
        self.riot.pd["loadout"]["Guns"][0]["SkinID"] = REAVER
        self.client.post("/collection/sets", data={"csrf": self.token(), "do": "save"})
        saved = json.loads((self.folder / "loadout_sets.json").read_text(encoding="utf-8"))["sets"]
        self.assertEqual(self.gun(VANDAL, saved[0])["SkinID"], REAVER)
        self.assertEqual(saved[0]["name"], "セット 1", "a name when none is given")

    def test_sets_have_a_limit(self):
        self.link()
        (self.folder / "loadout_sets.json").write_text(
            json.dumps({"sets": [{"id": f"s{number}", "name": f"s{number}", "Guns": []} for number in range(20)]}),
            encoding="utf-8",
        )
        text = self.page("/collection?show=sets")
        self.assertIn(
            'placeholder="例：ランク用" autocomplete="off" spellcheck="false" enterkeyhint="done" disabled', text
        )
        answer = self.client.post("/collection/sets", data={"csrf": self.token(), "do": "save"})
        self.assertIn("equip=full", answer.headers["Location"])
        self.assertIn("セットは 20 個までです", self.page(answer.headers["Location"]))

    def test_sets_are_the_owners(self):
        answer = self.client.post("/collection/sets", data={"do": "save"}, environ_base=VISITOR)
        self.assertIn(answer.status_code, (400, 404))

    def test_the_owners_pages_in_other_languages(self):
        """The owner's own pages are in the page's language too (not Japanese whatever the language)."""
        self.link()
        self.riot.phase = "menus"
        pages = {
            "/live": ("Reads your VALORANT as you play", "Start the queue", "Party"),
            "/progress": ("Battle pass", "Agent contracts", "Missions"),
            "/collection": ("Gun buddies", "Filter by skin or weapon name", "Level 187"),
            f"/collection?weapon={VANDAL}": ("Equipped now", "Buddy: ", "Take off"),
            "/collection?show=sets": ("Save what is equipped now", "Set name", "None yet."),
            "/settings": ("HenrikDev API key", "Stay signed in", "linked to Owner#JP1 (AP)"),
            "/shop/history": ("Past stores",),
            "/": ("Me", "Collection", "Progress", "Start a queue · pick and lock in"),
            "/tools": ("My account", "Battle pass, contracts and missions."),
        }
        self.client.set_cookie("lang", "en-GB")
        for path, words in pages.items():
            text = self.page(path)
            for said in words:
                self.assertIn(said, text, path)
            main = text.split("<main", 1)[1]
            self.assertNotIn('lang="ja"', main.replace('lang="ja" hreflang="ja"', ""), path)
        self.assertIn(
            'data-live-said="Agent select has started|Pick on Live|Close|Live ({state})|Match found"', self.page("/")
        )
        self.assertEqual(self.state()["words"], "Menu")
        self.client.set_cookie("lang", "ko")
        self.assertIn("총기 장식", self.page("/collection"))
        self.client.set_cookie("lang", "zh-TW")
        self.assertIn("即時", self.page("/live"))
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "titles", "item": "ee000000-0000-4000-8000-000000000002"},
        )
        self.assertIn("已裝備", self.page(answer.headers["Location"]))


if __name__ == "__main__":
    unittest.main()
