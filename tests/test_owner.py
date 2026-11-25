"""持ち主の進み具合（/progress）、進行中の試合（/live）、コレクションからの装備。"""

import json
import os
import re
import threading
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

    def solo(self):
        """持ち主のパーティーは持ち主だけ: 名前を隠している Secret はパーティー外（フィクスチャでは仲間）。"""
        self.riot.pd["party"]["Members"] = self.riot.pd["party"]["Members"][:1]

    def page(self, path: str, status: int = 200, **kwargs) -> str:
        answer = self.client.get(path, **kwargs)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def test_only_the_owners(self):
        for path in ("/progress", "/live"):
            self.page(path, 404, environ_base=VISITOR)
        answer = self.client.post("/collection/equip", data={"item": PRIME}, environ_base=VISITOR)
        self.assertIn(answer.status_code, (400, 404))
        self.assertIn("Riot アカウントが未連携です", self.page("/progress"))
        self.assertIn("Riot アカウントが未連携です", self.page("/live"))

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
        self.assertIn("ウィークリーの補充:", text)
        self.assertIn('href="/progress" aria-current="true"', text)

    def test_progress_when_riot_fails(self):
        self.link()
        self.riot.broken.add("contracts")
        self.assertIn('class="problem"', self.page("/progress", 503))

    def test_game_closed(self):
        self.link()
        text = self.page("/live")
        self.assertIn("VALORANT: 未起動", text)
        self.assertIn('data-refresh="20"', text)
        self.assertNotIn('action="/live/act"', text)

    def test_party_in_the_menus_and_starting_a_queue(self):
        self.link()
        self.riot.phase = "menus"
        text = self.page("/live")
        self.assertIn("VALORANT: メニュー", text)
        self.assertIn("ゴッド", text, "the owner's title")
        self.assertIn("リーダー", text)
        # 名前を隠していてもパーティーの仲間: ゲームもパーティーには名前を見せる
        self.assertIn('Secret<span class="tag">#9999</span>', text)
        self.assertIn("ルーキー", text, "and their title")
        self.assertIn("cc000000-0000-4000-8000-000000000002", text, "and their card")
        self.assertNotIn("非公開", text)
        self.assertNotIn('class="party p', text, "in the menus everyone is in the party: no number")
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
        self.assertIn("キューをキャンセルしました", self.page("/live"))

    def test_each_press_is_sent_on_a_slow_clock(self):
        """Windows の時計は 16 ms 刻み: 同じ刻みの中の 2 回の押下も 2 回として数える。"""
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
            self.assertIn("この操作はできません", self.page("/live"))
        self.client.post("/live/act", data={"csrf": token, "do": "leave"})
        self.assertIn("今は実行できません", self.page("/live"), "the game is closed: no party")
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
        self.assertIn("<strong>進行状況</strong>", tools)

    def test_where_the_game_is_for_the_tab(self):
        self.assertEqual(self.state()["problem"], "nologin")
        self.assertEqual(self.client.get("/live/state", environ_base=VISITOR).status_code, 404)
        self.link()
        self.assertEqual((self.state()["phase"], self.state()["words"]), ("", "VALORANT 未起動"))
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
        """ゲームを 1 回見るときの Riot の呼び出し（riot.Riot.live）を新しく。"""
        riot = self.app.config["VAL_RIOT"]
        riot.cache.put("live", None, 0)
        before = len(self.riot.asked)
        riot.live()
        return len(self.riot.asked) - before

    def test_a_found_match_is_told_at_once(self):
        """DailyVal は試合が見つかったときのゲームの 5 秒カウントからエージェント選択を出す: /live も同じく、
        セッションがエージェント選択と言うのを待たない。"""
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
        party["State"] = "MATCHMADE_GAME_STARTING"  # 試合が見つかった。エージェント選択はまだ作られていない
        found = self.state()
        self.assertEqual((found["phase"], found["words"], found["every"]), ("found", "試合が見つかりました", 1))
        text = self.page("/live")
        self.assertIn("試合が見つかりました。まもなくエージェント選択です。", text)
        self.assertIn('data-refresh="1"', text, "every second until agent select")
        self.riot.early_pregame = True  # エージェント選択ができた: セッションがまだメニューと言っていても出す
        found = self.state()
        self.assertEqual((found["phase"], found["map"]), ("pregame", "アセント"))
        self.assertEqual(found["icons"], [])
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn('class="pick-grid"', text)
        self.assertIn("残り 61 秒", text)
        self.assertNotIn("data-warm", text)

    def test_agent_select_opens_without_reading_the_collection(self):
        """持ち主のエージェントはマッチング中に読む（1 回、保持）。
        エージェント選択でコレクション全部は読まない: ピック画面は 10 回の呼び出しを待たずに開く。"""
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
        """マッチング中とエージェント選択は 1〜2 秒ごとに見る: 1 回で Riot に聞くのは 1〜2 つ（セッションと、
        前回覚えたパーティーかエージェント選択）、名前は 1 回だけ。"""
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
        """ "Vary: Cookie" 付きで答えた絵は、セッション cookie が変わるまでしかブラウザーに残らない:
        エージェントの四角が何度も取り直されていた。"""
        self.link()
        fresh = self.client.get("/media/agents/11111111-1111-4111-8111-111111111111/displayicon.png")
        self.assertNotIn("Cookie", fresh.headers.get("Vary", ""))
        self.assertIn("max-age=2592000", fresh.headers["Cache-Control"])
        fresh.close()  # send_file の開いたファイルを閉じる（閉じないと ResourceWarning）
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
        self.assertIn('<h2 id="me-title" class="headline">マイアカウント</h2>', text)
        self.assertIn("Riot アカウントを連携すると", text)
        self.assertNotIn("<strong>戦績</strong>", text, "not linked yet: whose page is not known")
        self.assertNotIn("me-title", self.page("/", environ_base=VISITOR))
        self.link()
        text = self.page("/")
        self.assertIn('href="/p/Owner/JP1"><strong>戦績</strong><small>Owner#JP1</small>', text)
        self.assertIn("<small data-live-text>", text, "the game's state, from the tab's asking")
        self.assertNotIn("Riot アカウントを連携すると", text)

    def test_the_best_rank_is_the_best_of_every_act(self):
        from valtracker.live import best_ever

        acts = {
            "act-now": {"CompetitiveTier": 21, "WinsByTier": {"18": 3, "22": 2, "23": 0}},
            "act-before": {"CompetitiveTier": 24, "WinsByTier": {"25": 1}},
            "act-old": {"CompetitiveTier": 0, "WinsByTier": {"23": 4}},  # エピソード 3 のイモータル 3
        }
        mmr = {"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": acts}}}
        self.assertEqual(best_ever(mmr), 25, "an earlier act's, not only this act's")
        self.assertEqual(best_ever(mmr, frozenset({"act-old"})), 26, "before Ascendant, 23 was today's 26")
        self.assertEqual(best_ever({"QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {"a": {}}}}}), 0)
        self.assertEqual(best_ever(None), 0)

    def test_the_players_act_and_the_teams_rank(self):
        self.link()
        self.solo()
        self.riot.phase = "pregame"
        text = self.page("/live")
        self.assertIn("今アクト 15勝 / 30試合（50%）", text)
        self.assertRegex(
            text,
            r'<span class="rank-label">今アクト最高</span>.*?<span>Tier 22</span>.*?'
            r'<span class="rank-label">通算最高</span>.*?<span>Tier 24</span>',
            "a player who shows their name: this act's best, and the best of every act (the act before)",
        )
        self.assertIn("今アクト 7勝 / 9試合（78%）", text, "a player who hides their name: all but the name")
        self.assertIn('味方 <small class="team-average">平均 Tier 17</small>', text, "(18 + 21 + 12) / 3")
        self.riot.phase = "ingame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertEqual(text.count('class="team-average"'), 1, "the other team has one rank: no average")
        self.assertIn("今アクト 15勝 / 30試合", text)

    def test_every_seat_shows_the_smurf_chance_and_why(self):
        """サブ垢の推定は % で、押すとその場で根拠が開く（試合中もページを離れずに）。名前を隠している人にも。"""
        self.link()
        self.riot.phase = "ingame"
        text = self.page("/live")
        chip = r'<button type="button" class="smurf (?:main|maybe|likely) sure-\w+" popovertarget="([\w-]+)"'
        chips = re.findall(chip, text)
        self.assertGreaterEqual(len(chips), 2)
        self.assertEqual(len(set(chips)), len(chips), "one popover per seat")
        for popup in chips:
            self.assertRegex(popup, r"^smurf-[0-9a-f]{12}$", "no puuid on the page")
            self.assertIn(f'<span class="smurf-pop" id="{popup}" popover>', text)
        self.assertRegex(text, r">(サブ垢|本垢) \d+%</button>")
        self.assertIn("サブ垢の確率（推定）", text)
        self.assertIn("今アクトの勝率", text, "the act's record from the game")
        self.assertNotIn(PUUID, text)
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertEqual(re.findall(chip, self.page("/live")), chips, "the same ids at each look")

    def test_the_last_match_once_it_is_over(self):
        self.link()
        self.riot.phase = "ingame"
        self.assertNotIn("直前の試合を見る", self.page("/live"))
        self.riot.phase = ""
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        match_id = self.riot.pd["coregame"]["MatchID"].lower()
        self.assertIn(f'href="/m/ap/{match_id}?me=Owner%23JP1">直前の試合を見る', self.page("/live"))
        with self.client.session_transaction() as kept:
            kept["live_match"] = [match_id, time.time() - 4 * 3600]
        self.assertNotIn("直前の試合を見る", self.page("/live"), "hours ago: not the last match any more")
        self.riot.phase = "ingame"
        self.state()  # 別のページを開いている間にプレイした: タブの問い合わせで残る
        self.riot.phase = "menus"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertIn("直前の試合を見る", self.page("/live"))

    def test_the_last_match_no_page_saw(self):
        """PC アプリはゲームの裏で何も読まない: 最後の試合はゲーム自身の履歴から。"""
        self.link()
        match_id = "cccccccc-0000-4000-8000-000000000009"
        record = json.loads(json.dumps(self.riot.pd["matches"]["bbbbbbbb-0000-4000-8000-000000000001"]))
        record["matchInfo"] |= {"matchId": match_id}
        record = json.loads(json.dumps(record).replace(self.riot.pd["player"], PUUID))
        self.riot.pd["matches"][match_id] = record
        self.riot.pd["player"] = PUUID
        started = int((time.time() - 2400) * 1000)
        self.riot.pd["history"]["History"].insert(
            0, {"MatchID": match_id, "GameStartTime": started, "QueueID": "competitive"}
        )
        self.riot.phase = "menus"
        text = self.page("/live")
        self.assertIn(f'<a href="/m/ap/{match_id}?me=Owner%23JP1">', text)
        # HenrikDev がその試合を持っていなくても、そのページを出す: Riot から、ほかの人の名前は無しで
        self.fetch.fail = 404
        page = self.client.get(f"/m/ap/{match_id}?me=Owner%23JP1")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn("Owner", text)
        # 持ち主と同じパーティー（Riot の partyId）だった人の名前は出す。ほかの人のは出さない
        self.assertIn("Secret Mate", text)
        self.assertNotIn("Foe One", text)
        self.assertEqual(page.headers["Cache-Control"], "no-store", "a page only the owner may see")
        kept = json.dumps(list(self.app.config["VAL_ARCHIVE"].matches(PUUID, "")), default=str)
        self.assertIn(match_id, kept)
        self.assertNotIn("Secret Mate", kept, "the kept match feeds pages anyone can see: no one else's name")
        self.assertNotIn("Secret Mate", self.page(f"/m/ap/{match_id}", 404, environ_base=VISITOR), "nor visitors")

    def test_the_collector_reads_the_games_own_records(self):
        """サイトの数字用の、持ち主がプレイしていない試合: HenrikDev のキー不要、名前は読まない。"""
        collector = self.app.config["VAL_COLLECTOR"]
        self.link()
        activity = self.app.config["VAL_ACTIVITY"]
        other = self.riot.pd["player"]
        activity.candidates.append(("ap", other, 15))
        new = collector.once()
        self.assertGreater(new, 0)
        self.assertTrue([url for _, url, _, _ in self.riot.asked if "/match-details/" in url], "from Riot")
        self.assertIn(other, collector.looked)
        asked = len(self.riot.asked)
        collector.looked.discard(other)
        collector.once()
        after = [url for _, url, _, _ in self.riot.asked[asked:]]
        self.assertFalse([url for url in after if "/match-details/" in url], "a match is read once")

    def played(self, queue: str = "competitive") -> str:
        """持ち主の試合が終わり、Riot のサーバーに記録がある: その試合の ID。"""
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
        """ゲーム自身のサーバーから: HenrikDev を待たず、キーも不要。"""
        self.link()
        match_id = self.played()
        text = self.page("/live")
        self.assertIn('<h2 id="last-title">直前の試合</h2>', text)
        self.assertIn(f'<li class="match win"><a href="/m/ap/{match_id}?me=Owner%23JP1">', text)
        self.assertIn("<strong>2–1</strong>", text)
        self.assertIn('<span class="up">+18 RR</span>', text)
        self.assertIn('<strong class="kda">3&thinsp;/&thinsp;1&thinsp;/&thinsp;1</strong>', text)
        self.assertIn("コンペティティブ", text)
        self.assertNotIn("直前の試合を見る", text, "the result is the link")
        for name in ("Secret Mate", "Foe One", "Pd Player"):
            self.assertNotIn(name, text, "Riot's record names everyone: no one else's name is shown")
        asked = len(self.riot.asked)
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.page("/live")
        after = [url for _, url, _, _ in self.riot.asked[asked:]]
        self.assertFalse([url for url in after if "/match-details/" in url or "competitiveupdates" in url], "kept")
        # すぐに持ち主の通算に入る
        self.assertEqual(self.app.config["VAL_ARCHIVE"].known([match_id]), {match_id})
        self.assertEqual(self.app.config["VAL_ARCHIVE"].rr(PUUID)[match_id]["change"], 18)

    def test_the_result_waits_for_riot(self):
        self.link()
        match_id = self.played()
        record = self.riot.pd["matches"].pop(match_id)
        text = self.page("/live")
        self.assertNotIn('id="last-title"', text)
        self.assertIn("直前の試合を見る", text, "the link as before")
        self.riot.pd["matches"][match_id] = record
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertNotIn('id="last-title"', self.page("/live"), "asked again only after a while")
        self.app.config["VAL_LAST_RESULT"]["tried"] = 0.0
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertIn('id="last-title"', self.page("/live"))

    def test_a_record_that_is_not_a_finished_match(self):
        self.link()
        match_id = self.played()
        whole = self.riot.pd["matches"][match_id]
        for record in ({"httpStatus": 200}, whole | {"matchInfo": whole["matchInfo"] | {"isCompleted": False}}):
            self.riot.pd["matches"][match_id] = record
            self.app.config["VAL_LAST_RESULT"] = {}
            self.app.config["VAL_RIOT"].cache.put("live", None, 0)
            answer = self.client.get("/live")
            self.assertEqual(answer.status_code, 200)
            self.assertNotIn('id="last-title"', answer.get_data(as_text=True))

    def test_riot_is_asked_for_a_result_a_few_times_only(self):
        """射撃場は記録の無い「試合」。"""
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
        """エージェント選択と試合: ロビーが画面を占める。ページが何をするかは、
        ゲームが閉じているときとメニューで説明する。"""
        self.link()
        for phase, wide in (("", False), ("menus", False), ("pregame", True), ("ingame", True)):
            self.riot.phase = phase
            self.app.config["VAL_RIOT"].cache.put("live", None, 0)
            text = self.page("/live")
            self.assertEqual('class="live-wide"' in text, wide, phase)
            self.assertEqual('class="live-steps"' in text, not wide, phase)
            self.assertEqual("自動で更新されます" in text, not wide, phase)
            self.assertEqual('<p class="game-state sr">' in text, wide, phase)

    def test_hidden_names_are_explained_only_when_someone_hides_theirs(self):
        self.link()
        self.solo()
        self.riot.phase = "ingame"
        note = "パーティー外の非公開プレイヤーは名前を表示しません"
        self.assertIn(note, self.page("/live"))
        for player in self.riot.pd["coregame"]["Players"]:
            player["PlayerIdentity"]["Incognito"] = False
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        self.assertNotIn(note, self.page("/live"))

    def test_agent_select(self):
        self.link()
        self.solo()
        self.riot.phase = "pregame"
        text = self.page("/live")
        self.assertIn("エージェント選択", text)
        self.assertIn("アセント", text)
        self.assertIn("コンペティティブ", text)
        self.assertIn("残り 61 秒", text)
        self.assertIn("Teammate", text)
        self.assertIn("ジェット（確定）", text)
        self.assertNotIn("Secret", text, "a hidden name stays hidden")
        self.assertIn("非公開", text)
        self.assertNotIn("レベル 50", text, "a hidden level too")
        self.assertIn("相手は 2 / 5 人が確定", text)
        self.assertIn("イニシエーター", text, "a role the team lacks")
        self.assertIn("所持エージェントなし", text, "the owner has no initiator")
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
        self.assertIn("選択しました（未確定）", text)
        self.assertIn("ソーヴァ で確定</button>", text)
        self.client.post("/live/act", data={"csrf": self.token(), "do": "lock", "value": sova})
        self.assertEqual(self.riot.pressed, [("select", sova), ("lock", sova)])
        text = self.page("/live")
        self.assertRegex(text, r'<div class="locked-agent">.*<span class="badge">確定</span><strong>ソーヴァ</strong>')
        self.assertNotIn('class="pick-grid"', text)
        self.assertNotIn(jett + '" class="pick', text)

    def test_agent_select_names_the_agents_that_win_on_the_map(self):
        self.link()
        self.riot.phase = "pregame"
        jett, sova = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
        self.riot.pd["entitlements"]["agents"].append(sova)
        started = time.time() - 3600
        games = []
        for number in range(8):  # アセントで、ソーヴァのチームが 8 戦 6 勝、ジェットのチームが 8 戦 2 勝
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
        self.assertIn("このマップの勝率上位: <strong>ソーヴァ</strong> 75%", text)
        self.assertIn("8 試合の集計", text)
        self.assertIn('<small class="pick-win">75%</small>', text)
        self.assertNotIn("<strong>ジェット</strong> 25%", text, "a teammate has locked Jett")

    def test_in_the_match(self):
        self.link()
        self.solo()
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
        # 名前を隠している人も名前以外は出す（持ち主の頼み、2026-11-17）: 称号、カード、今アクトの戦績
        self.assertIn("ルーキー", text, "a hidden player's title")
        self.assertEqual(text.count('class="seat-record"'), 2, "everyone's act record, the hidden player's too")
        self.assertNotIn('class="party p', text, "the owner alone: no party numbers")
        self.assertNotIn("同じ番号の人は同じパーティーです", text)
        ranks = sum("/mmr/v1/players/" in url for _, url, _, _ in self.riot.asked)
        self.app.config["VAL_RIOT"].cache = type(self.app.config["VAL_RIOT"].cache)(8)
        self.page("/live")
        self.assertEqual(sum("/mmr/v1/players/" in url for _, url, _, _ in self.riot.asked), ranks, "asked once")

    def test_the_loading_screen_shows_the_other_team_at_once(self):
        """エージェント選択が終わった（ロード画面）: セッションがまだエージェント選択と言っても、相手の構成をすぐ出す。"""
        self.link()
        reader = self.app.config["VAL_RIOT"]
        self.riot.phase = "pregame"
        self.assertIn("ピック", self.page("/live"))
        parties = sum("/parties/v1/" in url for _, url, _, _ in self.riot.asked)
        # Riot は試合を用意した。エージェント選択の試合はまだ答え、セッションはまだ PREGAME
        self.riot.pd["pregame"]["PregameState"] = "provisioned"
        self.riot.phase, self.riot.loop = "ingame", "PREGAME"
        reader.cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn("相手", text)
        self.assertIn("対戦中", text)
        self.assertEqual(
            sum("/parties/v1/" in url for _, url, _, _ in self.riot.asked), parties, "the party read in agent select"
        )
        # 次の確認はその試合を直接聞く: エージェント選択と持ち主の居場所は聞かない
        asked = len(self.riot.asked)
        reader.cache.put("live", None, 0)
        self.assertIn("相手", self.page("/live"))
        urls = [url for _, url, _, _ in self.riot.asked[asked:] if "/v1/" in url and "glz-" in url]
        self.assertEqual([url.split("/v1/")[0].rsplit("/", 1)[-1] for url in urls], ["session", "core-game"], urls)
        self.assertNotIn("/players/", urls[1])

    def test_agent_select_stays_until_the_match_can_be_read(self):
        """ロード画面の初め、エージェント選択も試合も GLZ に無い間: 「起動中」に戻さず確定したピックを出し、すぐまた見る。"""
        self.link()
        reader = self.app.config["VAL_RIOT"]
        self.riot.phase = "pregame"
        self.page("/live")
        self.riot.phase, self.riot.loop = "loading", "INGAME"  # どちらの /players も 404
        reader.cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn("味方", text)
        self.assertNotIn("起動中", text)
        self.assertNotIn("残り", text, "agent select is over")
        self.assertIn('data-refresh="2"', text)
        self.riot.phase, self.riot.loop = "ingame", None
        reader.cache.put("live", None, 0)
        self.assertIn("相手", self.page("/live"))
        # ずっと前のエージェント選択は出さない（試合が終わってセッションがまだ INGAME の間など）
        self.riot.phase, self.riot.loop = "loading", "INGAME"
        reader.cache.put("live", None, 0)
        reader.where.pop("ingame")  # 終わった試合は GLZ にもう無い
        with mock.patch.object(reader, "clock", return_value=time.time() + 3600):
            text = self.page("/live")
        self.assertNotIn("味方", text)
        self.assertIn("起動中", text)

    def test_the_ranks_fill_in_soon_after_the_seats(self):
        """ランクが遅い: エージェントと名前を先に出し、ランクは試合中の 10 秒を待たずにすぐ足す。"""
        self.link()
        self.solo()
        reader = self.app.config["VAL_RIOT"]
        self.riot.phase = "ingame"
        self.riot.slow_ranks = threading.Event()
        self.addCleanup(self.riot.slow_ranks.set)
        text = self.page("/live")
        self.assertIn("相手", text)
        self.assertIn('href="/p/Teammate/0001"', text, "the names first")
        self.assertNotIn("47 RR", text, "the rank is not here yet")
        self.assertIn('data-refresh="2"', text, "looked at again soon")
        self.riot.slow_ranks.set()
        for _ in range(100):
            if not reader._asking:
                break
            time.sleep(0.02)
        reader.cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn("47 RR", text)
        self.assertIn('data-refresh="10"', text, "every rank is in: as slow as a match")

    def test_party_members_are_named_in_agent_select_and_the_match(self):
        """ゲームの「パーティー外のプレイヤーに名前を隠す」: 同じパーティーの人には名前が見える。
        パーティーは Riot 自身の GLZ のパーティーのメンバーで決め、1 試合に 1 回だけ読む。"""
        self.link()
        self.riot.phase = "pregame"
        stranger = self.riot.pd["pregame"]["AllyTeam"]["Players"][1]
        stranger["PlayerIdentity"]["Incognito"] = True  # パーティー外で名前を隠している
        text = self.page("/live")
        self.assertIn('Secret<span class="tag">#9999</span>', text, "a party member who hides their name")
        # 持ち主と仲間に同じ番号（1）: サイトでわかるのは持ち主のパーティーだけ
        self.assertEqual(text.count('<span class="party p1" title="パーティー 1">'), 2, "the owner and the member")
        self.assertIn('<small class="team-party">パーティー 2人</small>', text)
        self.assertIn("同じ番号の人は同じパーティーです。", text)
        self.assertIn("相手や野良のパーティーは PC アプリだけに表示されます", text, "the site knows only the owner's")
        self.assertNotIn("Teammate", text, "someone outside the party who hides their name")
        self.assertIn("パーティー外の非公開プレイヤーは名前を表示しません", text)
        asked = b"".join(body or b"" for _, url, _, body in self.riot.asked if url.endswith("/name-service/v2/players"))
        self.assertIn(b"a1111111-0000-4000-8000-000000000002", asked)
        self.assertNotIn(b"a1111111-0000-4000-8000-000000000001", asked, "a hidden stranger's name is never asked")
        parties = sum("/parties/v1/" in url for _, url, _, _ in self.riot.asked)
        for _ in range(3):
            self.app.config["VAL_RIOT"].cache.put("live", None, 0)
            self.page("/live")
        self.assertEqual(sum("/parties/v1/" in url for _, url, _, _ in self.riot.asked), parties, "once a match")
        self.riot.phase = "ingame"
        self.app.config["VAL_RIOT"].cache.put("live", None, 0)
        text = self.page("/live")
        self.assertIn('Secret<span class="tag">#9999</span>', text)
        self.assertIn("ルーキー", text, "a party member's title")
        # フィクスチャの試合で持ち主（Red）と Secret（Blue）は別のチーム: 番号は付けても、各チームでは 1 人
        self.assertEqual(text.count('class="party p1"'), 2)
        self.assertNotIn('class="team-party"', text, "one of the party on each team is no party of a team")

    def test_party_members_stay_hidden_when_the_party_cannot_be_read(self):
        self.link()
        self.riot.phase = "pregame"
        self.riot.broken.add("party")
        text = self.page("/live")
        self.assertNotIn("Secret", text, "not known to be in the party: hidden, as before")
        self.assertIn("非公開", text)
        self.assertIn("Teammate", text, "the rest of the page still shows")

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
        self.assertIn("未所持のアイテムは装備できません", self.page("/collection?equip=unknown"))

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
        # カードは元の場所のまま: 動くのは印だけ
        after = self.page("/collection?show=cards&equip=ok")
        self.assertEqual(re.findall(r'data-name="([^"]+)"', after), re.findall(r'data-name="([^"]+)"', text))
        self.assertNotIn(f'value="{found[0]}"', after, "now worn")

    def test_wear_a_spray(self):
        self.link()
        text = self.page("/collection?show=sprays")
        self.assertIn('name="slot.dd000000-0000-4000-8000-000000000001"', text)
        self.assertIn("1（現在: GG スプレー）", text, "each place of the wheel with the spray in it")
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
        """PD が playerloadout v2 しか持たない場合（v3 は 404）、装備は v2 に戻す。"""
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
        self.assertIn("Riot のサーバーが応答しません", self.page("/collection?show=titles&equip=down"))

    def test_wear_that_pd_does_not_keep(self):
        """PD は 200 で答えるが元のロードアウトのまま: ページは装備したとは言わない。"""
        self.link()
        self.riot.ignore_put = True
        title = "ee000000-0000-4000-8000-000000000002"
        answer = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=notsaved", answer.headers["Location"])
        text = self.page(answer.headers["Location"])
        self.assertIn("装備が反映されませんでした。もう一度お試しください。", text)
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
        # 文は失敗のときだけ、それも素のものだけ出す
        ok = self.page("/collection?show=titles&equip=ok&riot=x")
        self.assertNotIn("<code>x</code>", ok)
        self.assertIn("装備しました。", ok)

    def test_wear_while_the_game_is_open(self):
        """開いているゲームは開始時のロードアウトで描く: 次の試合から新しいものになると書く。"""
        self.link()
        title = "ee000000-0000-4000-8000-000000000002"
        closed = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=ok", closed.headers["Location"])
        self.riot.phase = "menus"
        title = "ee000000-0000-4000-8000-000000000001"
        open_ = self.client.post("/collection/equip", data={"csrf": self.token(), "show": "titles", "item": title})
        self.assertIn("equip=okopen", open_.headers["Location"])
        text = self.page(open_.headers["Location"])
        self.assertIn("ゲームには次の試合から反映されます", text)
        self.assertIn('class="verdict ok equip-said" role="status"', text)

    def gun(self, weapon: str, loadout: dict | None = None) -> dict:
        loadout = loadout if loadout is not None else self.riot.worn[-1]
        return next(gun for gun in loadout["Guns"] if gun["ID"] == weapon)

    def test_put_a_buddy_on_a_weapon(self):
        """開いている武器のガンバディを、ゲームの武器画面と同じように変える: 空いているコピーを着ける。"""
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
        self.assertIn("ハート バディー（装備中）", text)
        self.assertIn('name="unbuddy"', text)
        text = self.page(f"/collection?weapon={VANDAL}")
        self.assertIn("ハート バディー（クラシック から移動）", text, "its one copy is on the Classic")

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
        self.assertIn("ヴァンダル（現在: チリ バディー）", text)
        self.assertNotIn(">近接武器<", text, "no buddy on a knife")
        answer = self.client.post(
            "/collection/equip",
            data={"csrf": self.token(), "show": "buddies", "item": HEART, f"weapon.{HEART}": VANDAL},
        )
        self.assertIn("show=buddies", answer.headers["Location"])
        self.assertIn("equip=ok", answer.headers["Location"])
        self.assertEqual((self.gun(VANDAL)["CharmID"], self.gun(VANDAL)["CharmInstanceID"]), (HEART, "i3"))

    def test_wear_a_flex(self):
        """playerloadout v3 はホイールのどの場所にもスプレーかフレックスを入れられる。
        すでにあるものとは場所を入れ替える。"""
        self.link()
        flex = "ff000000-0000-4000-8000-000000000001"
        text = self.page("/collection?show=flex")
        self.assertIn(f'name="slot.{flex}"', text)
        self.assertIn("1（現在: GG スプレー）", text)
        self.assertIn("2（現在: ぬいぐるみ）", text)
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
        self.assertIn("現在の装備を保存", text)
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
        self.assertIn("現在の装備をセットに保存しました", text)
        self.assertIn("ランク 用", text)
        self.assertIn('<span class="badge">現在の装備</span>', text, "it is what is worn")
        self.assertIn("<strong>ヴァンダル</strong> プライム ヴァンダル · チリ バディー", text)
        self.assertIn(">セット <small>1</small>", text, "the tab counts the sets")
        # 別のものを着けてから、セットで全部戻す
        token = self.token()
        self.client.post("/collection/equip", data={"csrf": token, "show": "skins", "item": REAVER})
        self.client.post("/collection/equip", data={"csrf": token, "show": "skins", "unbuddy": VANDAL})
        self.client.post(
            "/collection/equip", data={"csrf": token, "show": "titles", "item": "ee000000-0000-4000-8000-000000000002"}
        )
        self.assertNotIn('<span class="badge">現在の装備</span>', self.page("/collection?show=sets"))
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "use", "set": saved[0]["id"]})
        self.assertIn("equip=ok", answer.headers["Location"])
        vandal = self.gun(VANDAL)
        self.assertEqual((vandal["SkinID"], vandal["CharmInstanceID"], vandal["CharmID"]), (PRIME, "i1", CHILI))
        self.assertEqual(self.riot.worn[-1]["Identity"]["PlayerTitleID"], "ee000000-0000-4000-8000-000000000001")
        self.assertIn('<span class="badge">現在の装備</span>', self.page("/collection?show=sets"))
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "delete", "set": saved[0]["id"]})
        self.assertIn("equip=deleted", answer.headers["Location"])
        self.assertEqual(json.loads((self.folder / "loadout_sets.json").read_text(encoding="utf-8"))["sets"], [])
        answer = self.client.post("/collection/sets", data={"csrf": token, "do": "use", "set": saved[0]["id"]})
        self.assertIn("equip=noset", answer.headers["Location"])

    def test_a_set_is_read_from_riot_now(self):
        """セットに入るのは今着けているもの。コレクションの保持していた答え（その後ゲームで変わったかもしれない）
        ではない。"""
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
        """持ち主専用ページもページの言語で出す（言語にかかわらず日本語、ではない）。"""
        self.link()
        self.riot.phase = "menus"
        pages = {
            "/live": ("Follows your game", "Start the queue", "Party"),
            "/progress": ("Battle pass", "Agent contracts", "Missions"),
            "/collection": ("Gun buddies", "Filter by skin or weapon name", "Level 187"),
            f"/collection?weapon={VANDAL}": ("Equipped", "Buddy: ", "Take off"),
            "/collection?show=sets": ("Save what is equipped now", "Set name", "None yet."),
            "/settings": ("HenrikDev API key", "Stay signed in", "<strong>Owner#JP1</strong><small>AP", "In use"),
            "/shop/history": ("Store history",),
            "/": ("My account", "Collection", "Progress", "Queue and agent select"),
            "/tools": ("My account", "Battle pass, contracts, missions"),
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
