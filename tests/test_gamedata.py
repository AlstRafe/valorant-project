"""ゲームのデータのページ: 武器（weapons.py）、マップ（maps.py）、エージェントの集計と構成（activity.py の
maps・comps・agent）、試合の経済（views._economy）。"""

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from helpers import make_app

from valtracker import weapons
from valtracker.activity import COMPS_MIN, FEW, Activity

VISITOR = {"homepage.visitor": True}
NOON = int(datetime(2026, 11, 4, 12, tzinfo=UTC).timestamp())
VANDAL = "9c82e19d-4575-0200-1a81-3eacf00cf872"
JUDGE = "ec845bf4-4f79-ddda-a3da-0db3774b2794"
KNIFE = "2f59173c-4bed-b6c3-2191-dea9b58be9c7"
ASCENT = "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319"
DISTRICT = "690b3ed2-4dff-945b-8223-6da834e30d24"
JETT, SOVA = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
#: 5 体の構成（3 体はコンテンツに無い作りもの）
COMP = (
    JETT,
    SOVA,
    "33333333-3333-4333-8333-333333333331",
    "33333333-3333-4333-8333-333333333332",
    "33333333-3333-4333-8333-333333333333",
)
OTHER = tuple(f"44444444-4444-4444-8444-44444444444{n}" for n in range(5))


def game(number: int, started: int, won_red: bool = True, queue: str = "competitive", rounds: int = 20) -> dict:
    """ラウンドのダイジェストつきの作りものの試合: Red が COMP、Blue が OTHER。前半 Red が攻撃。"""
    found = {
        "metadata": {
            "match_id": f"g{number}",
            "region": "ap",
            "cluster": "Tokyo",
            "queue": {"id": queue},
            "map": {"id": ASCENT.upper(), "name": "Ascent"},
            "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
            "game_length_in_ms": 1_800_000,
        },
        "players": [
            {
                "puuid": f"p{number}-{place}",
                "team_id": "Red" if place < 5 else "Blue",
                "tier": {"id": 16},
                "agent": {"id": (COMP + OTHER)[place]},
            }
            for place in range(10)
        ],
        "teams": [{"team_id": "Red", "won": won_red}, {"team_id": "Blue", "won": not won_red}],
        # 攻撃側が 3 ラウンドに 2 回取る。A に 2 回、B に 1 回設置
        "digest": {
            "rounds": [
                {
                    "a": "Red" if index < rounds // 2 else "Blue",
                    "w": ("Red" if index < rounds // 2 else "Blue")
                    if index % 3
                    else ("Blue" if index < rounds // 2 else "Red"),
                    "s": "A" if index % 3 == 1 else "B" if index % 3 == 2 else "",
                }
                for index in range(rounds)
            ]
        },
    }
    return found


class WeaponMathTest(unittest.TestCase):
    def test_shots_and_time_to_kill(self):
        self.assertEqual(weapons.shots(150, 40), 4)
        self.assertEqual(weapons.shots(150, 160), 1)
        self.assertEqual(weapons.shots(100, 25), 4, "exactly enough is enough")
        self.assertEqual(weapons.shots(100, 0), 0)
        self.assertEqual(weapons.ttk(4, 9.75), 308)
        self.assertEqual(weapons.ttk(1, 9.75), 0, "the first shot is at once")
        self.assertIsNone(weapons.ttk(0, 9.75))
        self.assertIsNone(weapons.ttk(3, 0))


class GameDataPagesTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, _ = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def page(self, path: str, status: int = 200) -> str:
        answer = self.client.get(path, environ_base=VISITOR)
        self.assertEqual(answer.status_code, status, path)
        return answer.get_data(as_text=True)

    def seed(self, count: int = FEW + 2) -> None:
        now = self.app.config["VAL_ACTIVITY"].clock()
        self.app.config["VAL_ACTIVITY"].seen(
            [game(n, int(now) - 3600 * (n + 1), won_red=n % 4 != 0) for n in range(count)]
        )

    def test_the_weapon_list_by_kind_without_the_knife(self):
        text = self.page("/weapons")
        self.assertIn("サイドアーム", text)
        self.assertIn("ショットガン", text)
        self.assertIn(f'href="/weapons/{VANDAL}"', text)
        self.assertNotIn(f'href="/weapons/{KNIFE}"', text, "the knife has no numbers")
        self.assertIn("アーマー 150 に体 4 発", text)
        self.assertIn("TTK 308 ms", text)
        self.assertIn("×12", text, "a shotgun says its pellets")
        self.client.set_cookie("lang", "en-GB")
        self.assertIn("Shotguns", self.page("/weapons"))

    def test_a_weapon_page_with_ranges_shots_and_a_comparison(self):
        text = self.page(f"/weapons/{VANDAL}")
        self.assertIn("ヴァンダル", text)
        self.assertIn("2,900", text)
        self.assertIn("9.75", text, "the fire rate as the game has it")
        self.assertIn("0–50 m", text)
        self.assertIn("308 ms", text)
        self.assertIn("×1.25", text, "the zoom")
        self.assertIn(f"/skins?weapon={VANDAL}", text)
        self.assertNotIn('class="gun-vs"', text)
        text = self.page(f"/weapons/{VANDAL}?vs={JUDGE}")
        self.assertIn('class="board plain gun-vs"', text)
        self.assertIn("ジャッジ", text)
        self.assertIn('<td class="better">1,850</td>', text, "the cheaper one is better")
        classic = self.page("/weapons/29a0cfab-485b-f5d5-779a-b59f85e204a8")
        self.assertIn("30–50 m", classic, "every range")
        self.assertIn("セミオート", classic)
        self.page(f"/weapons/{KNIFE}", 404)
        self.page("/weapons/nothing", 404)

    def test_one_download_of_the_weapons_feeds_both_lists(self):
        self.page("/weapons")
        content = self.app.config["VAL_CONTENT"]
        self.assertTrue(content.weapons("ja"))
        asked = [url for url, _ in self.fetch.asked if "valorant-api.com/v1/weapons" in url]
        self.assertEqual(len(asked), 1, asked)

    def test_the_player_page_links_a_weapon_to_its_page(self):
        text = self.page("/p/Tester/JP1/part")
        self.assertIn(f'href="/weapons/{VANDAL}"', text)

    def test_maps_with_sites_only_and_their_numbers(self):
        text = self.page("/maps")
        self.assertIn(f'href="/maps/{ASCENT}"', text)
        self.assertNotIn(DISTRICT, text, "team deathmatch maps have no sites")
        self.assertNotIn("攻撃側", text, "no numbers before enough matches")
        self.page(f"/maps/{DISTRICT}", 404)
        self.page("/maps/nothing", 404)
        text = self.page(f"/maps/{ASCENT}")
        self.assertIn("集計できる試合がまだ足りません。", text)
        self.assertIn(">ツリー</text>", text, "callouts on the minimap")
        self.assertIn("メインエントランス", text)
        self.seed()
        self.assertIn("攻撃側 65.0%", self.page("/maps"))
        text = self.page(f"/maps/{ASCENT}")
        self.assertIn("65.0%", text)
        self.assertIn('<th scope="row">A</th>', text)
        self.assertIn("プール", self.page("/maps"))

    def test_an_agent_page_with_its_numbers_and_comps(self):
        text = self.page(f"/agents/{JETT}")
        self.assertIn("集計できる試合がまだ足りません。", text)
        self.seed()
        text = self.page(f"/agents/{JETT}")
        self.assertIn("50.0%", text, "a pick in every match: half the teams")
        self.assertIn("75.0%", text, "Red won 3 of 4")
        self.assertIn(f'href="/maps/{ASCENT}"', text)
        self.assertIn("アセント", text)
        self.client.set_cookie("lang", "ko")
        self.assertIn("맵별", self.page(f"/agents/{JETT}"))

    def test_the_numbers_for_the_pc_app(self):
        self.seed()
        found = self.client.get("/numbers/maps").get_json()
        self.assertEqual(found["maps"][ASCENT]["atk"][1], 20 * (FEW + 2))
        found = self.client.get(f"/numbers/comps?map={ASCENT}").get_json()
        self.assertEqual(found["comps"][0]["agents"], sorted(COMP))
        found = self.client.get(f"/numbers/agent?agent={JETT}").get_json()
        self.assertEqual(found["picks"], FEW + 2)
        self.assertEqual(self.client.get("/numbers/comps?map=../x").status_code, 404)
        self.assertEqual(self.client.get("/numbers/agent").status_code, 404)
        self.assertEqual(self.client.get("/numbers/maps?queue=deathmatch").status_code, 404)

    def test_the_match_page_shows_the_economy(self):
        text = self.page("/m/ap/aaaaaaaa-0000-4000-8000-000000000001?me=Tester%23JP1")
        self.assertIn('id="eco-title"', text)
        self.assertIn("ピストル", text)
        self.assertIn("味方", text)
        text = self.page("/m/ap/aaaaaaaa-0000-4000-8000-000000000001")
        self.assertIn('class="economy plain-teams"', text, "red and blue without a player")

    def test_the_game_data_pages_light_their_tab(self):
        for path in ("/weapons", "/maps", f"/maps/{ASCENT}", f"/weapons/{VANDAL}"):
            text = self.page(path)
            self.assertIn('<a href="/agents" aria-current="page">', text.replace("\n", ""), path)
        self.assertIn('<a href="/tools" aria-current="page">', self.page("/ranks").replace("\n", ""))


class SidesAndCompsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.now = NOON + 3600
        self.activity = Activity(Path(folder.name), clock=lambda: self.now)
        self.addCleanup(self.activity.close)

    def test_attack_wins_and_plants_by_site(self):
        self.activity.seen([game(1, NOON), game(2, NOON - 60)])
        found = self.activity.maps()
        self.assertEqual(found["matches"], 2)
        ascent = found["maps"][ASCENT]
        self.assertEqual(ascent["atk"], [26, 40])
        self.assertEqual(ascent["sites"]["A"][0], 14)
        self.assertEqual(ascent["sites"]["B"][0], 12)
        self.assertEqual(self.activity.maps("unrated")["matches"], 0)

    def test_a_match_without_rounds_counts_later_with_them(self):
        bare = game(3, NOON)
        bare.pop("digest")
        self.activity.seen([bare])
        self.assertEqual(self.activity.maps()["matches"], 0)
        self.activity.seen([game(3, NOON)])
        self.assertEqual(self.activity.maps()["matches"], 1, "the same match read again with its rounds")
        short = game(4, NOON, rounds=3)
        self.activity.seen([short])
        self.assertEqual(self.activity.maps()["matches"], 1, "a remake is not a match")

    def test_comps_need_five_agents_and_a_few_games(self):
        self.activity.seen([game(1, NOON, won_red=True)])
        self.assertEqual(self.activity.comps()["comps"], [], f"one game is not a comp (needs {COMPS_MIN})")
        self.activity.seen([game(2, NOON - 60, won_red=False)])
        found = self.activity.comps()
        self.assertEqual(found["teams"], 4)
        listed = {tuple(item["agents"]): item for item in found["comps"]}
        self.assertEqual(listed[tuple(sorted(COMP))]["win"], 50.0)
        self.assertEqual(len(self.activity.comps(agent=JETT)["comps"]), 1)
        self.assertEqual(self.activity.comps(map_id="elsewhere")["comps"], [])
        broken = game(5, NOON)
        broken["players"] = broken["players"][:4] + broken["players"][5:]
        self.activity.seen([broken])
        self.assertEqual(self.activity.comps()["teams"], 5, "a team of four is no comp")

    def test_one_agent_by_map_and_rank_band(self):
        self.activity.seen([game(n, NOON - n * 60, won_red=n != 0) for n in range(4)])
        found = self.activity.agent(JETT)
        self.assertEqual((found["matches"], found["picks"], found["wins"]), (4, 4, 3))
        self.assertEqual(found["maps"][ASCENT], [4, 3, 4])
        self.assertEqual(found["bands"]["plat-diamond"], [4, 3, 4])
        self.assertEqual(self.activity.agent("nobody")["picks"], 0)

    def test_broken_site_numbers_are_left_out(self):
        self.activity.seen([game(1, NOON)])
        with self.activity._lock:
            self.activity._connect().execute("UPDATE sides SET sites = ?", (json.dumps({"A": "x", "B": [1]}),))
        self.activity._summary.clear()
        self.assertEqual(self.activity.maps()["maps"][ASCENT]["sites"], {})


if __name__ == "__main__":
    unittest.main()
