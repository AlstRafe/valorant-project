"""壊れた・型の違う外部の答え（HenrikDev、Riot、valorant-api.com）や通信の失敗で、ページやスレッドが落ちないこと。"""

import http.client
import json
import math
import unittest
from unittest import mock

from helpers import make_app

from valtracker import content as content_module
from valtracker import crosshair, henrik, net, riot, rounds, util
from valtracker.util import as_dict, as_list, to_float, to_int


class ConversionTest(unittest.TestCase):
    def test_numbers_that_cannot_be_used_are_the_default(self):
        for broken in (math.nan, math.inf, -math.inf, "nan", "Infinity", 10**400, None, "", "abc", [], {}):
            self.assertEqual(to_float(broken, 7.0), 7.0, broken)
        self.assertEqual(to_float("1.5"), 1.5)
        self.assertEqual(to_int(math.inf, 3), 3)

    def test_lists_and_dicts_of_the_wrong_type_are_empty(self):
        self.assertEqual([as_list(value) for value in (None, "abc", 3, True, {"a": 1})], [[]] * 5)
        self.assertEqual([as_dict(value) for value in (None, "abc", 3, True, [1])], [{}] * 5)
        self.assertEqual(as_list([1]), [1])
        self.assertEqual(as_dict({"a": 1}), {"a": 1})


class ShapesTest(unittest.TestCase):
    """``$`` は末尾の改行も通す: 名前や ID の形は全体で確かめる（改行入りのトークンや cookie は保存されると、
    以後の全部の呼び出しがヘッダーの検査で失敗していた）。"""

    def test_a_newline_at_the_end_is_not_allowed(self):
        uuid = "0c0c0c0c-1111-4222-8333-444444444444"
        for pattern, good in (
            (henrik.NAME, "Tester"),
            (henrik.TAG, "JP1"),
            (henrik.MATCH_ID, uuid),
            (util.UUID, uuid),
            (riot._TOKEN, "a" * 40),
            (riot._COOKIE_VALUE, "ssid-value"),
            (riot._COOKIE_NAME, "ssid"),
            (crosshair.CODE, "0;P;h;0"),
        ):
            self.assertTrue(pattern.fullmatch(good), pattern.pattern)
            self.assertFalse(pattern.fullmatch(good + "\n"), pattern.pattern)

    def test_a_skin_address_with_a_newline_is_not_found(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        answer = app.test_client().get("/skins/aa000000-0000-4000-8000-000000000001%0A")
        self.assertEqual(answer.status_code, 404)


class NetTest(unittest.TestCase):
    """途中で切れた答え（IncompleteRead）や壊れた答え（BadStatusLine）は OSError ではない: 答え無しにする。"""

    def test_a_cut_off_answer_is_no_answer(self):
        with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"half")):
            self.assertEqual(net.fetch("https://example.com/"), (0, b""))
        broken = mock.Mock()
        broken.open.side_effect = http.client.BadStatusLine("nonsense")
        with mock.patch.object(net, "_OPENER", broken):
            self.assertEqual(net.send("GET", "https://example.com/"), (0, [], b""))


class ContentTest(unittest.TestCase):
    def test_ranks_without_a_readable_number_are_skipped(self):
        known = content_module.Content.__new__(content_module.Content)
        tiers = [{"tier": 0}, {"tier": "3"}, {"tier": None}, {"tier": ""}, {}, {"tier": "x"}, "broken"]
        with mock.patch.object(content_module.Content, "_list", return_value=[{"tiers": tiers}]):
            self.assertEqual(sorted(known.tiers("ja")), [0, 3])

    def test_kept_parts_are_text(self):
        weapon = {
            "uuid": "w1",
            "displayName": ["not", "a", "name"],
            "shopData": {"cost": "NaN"},
            "skins": [{"uuid": "s1", "displayName": 12.5, "levels": True, "chromas": "abc"}],
        }
        slim = content_module._slim_weapon(weapon)
        self.assertNotIn("displayName", slim)
        self.assertEqual(slim["cost"], 0)
        self.assertEqual(slim["skins"], [{"uuid": "s1", "levels": [], "chromas": []}])

    def test_contract_levels_with_broken_xp(self):
        item = {"uuid": "c1", "content": {"chapters": [{"levels": [{"xp": "NaN"}, {"xp": [1]}, {"xp": 2000}]}, 5]}}
        self.assertEqual(content_module._slim_contract(item)["levels"], [0, 0, 2000])


class HenrikTest(unittest.TestCase):
    def test_match_parts_of_the_wrong_type_are_none(self):
        match = {
            "metadata": {"match_id": "m1", "map": "Ascent", "queue": ["x"], "started_at": 5},
            "players": [{"puuid": "p1", "stats": "broken", "party_id": {"x": 1}, "agent": True, "economy": 3}, 7],
            "teams": [{"team_id": "Red", "rounds": "x"}],
            "rounds": True,
            "kills": 5,
        }
        slim = henrik.slim_match(match)
        self.assertEqual(slim["metadata"]["map"], None)
        self.assertEqual(slim["metadata"]["started_at"], None)
        player = slim["players"][0]
        self.assertEqual((player["stats"], player["party_id"], player["agent"], player["economy"]), (None,) * 4)
        self.assertEqual(len(slim["players"]), 1)
        self.assertIsNone(slim["teams"][0]["rounds"])

    def test_one_broken_match_does_not_hide_the_list(self):
        found = henrik._slim_matches([{"metadata": "broken"}, {"metadata": {"match_id": "m1"}}])
        self.assertEqual([match["metadata"]["match_id"] for match in found], ["m1"])

    def test_moments_that_are_not_dates(self):
        for broken in (math.inf, 10**30, -5, "0001-01-01T00:00:00+14:00", "abc", None):
            self.assertEqual(henrik._moment(broken), 0, broken)
        self.assertEqual(henrik._moment(1_700_000_000), 1_700_000_000)

    def test_status_and_esports_with_broken_parts(self):
        found = henrik.slim_status({"maintenances": [{"titles": 5, "updates": True, "platforms": 3.5}], "incidents": 1})
        self.assertEqual(found["incidents"], [])
        self.assertEqual(found["maintenances"][0]["platforms"], [])
        game = {
            "match": {"teams": [{"name": "A", "game_wins": "²"}, {"name": "B", "game_wins": "2"}], "game_type": {}},
            "date": "2026-11-16",
        }
        game["match"]["game_type"]["count"] = "²"
        listed = henrik._slim_esports([game])
        self.assertEqual(listed[0]["best_of"], 0)
        self.assertEqual([team["wins"] for team in listed[0]["teams"]], [0, 2])

    def test_an_account_that_is_not_an_object(self):
        with self.assertRaises(henrik.HenrikError):
            henrik._slim_account("broken")


class RoundsTest(unittest.TestCase):
    def test_places_that_cannot_be_drawn(self):
        for x in (math.nan, math.inf, 1e308, 10**30):
            self.assertIsNone(rounds._spot({"x": x, "y": 10}), x)
        self.assertEqual(rounds._spot({"x": 1200.4, "y": -300}), [1200, -300])

    def test_kills_with_broken_parts(self):
        place = {"v": 0, "k": 1}
        kill = {"victim": "v", "killer": "k", "assistants": True, "player_locations": 5, "round": 0}
        self.assertEqual(rounds._kill(kill, place)[5], [])


class RiotRanksTest(unittest.TestCase):
    def test_a_rank_that_failed_in_a_surprising_way_is_asked_again(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        reader = app.config["VAL_RIOT"]
        subject = "0c0c0c0c-1111-4222-8333-444444444444"
        with (
            mock.patch.object(reader, "_base", return_value="https://pd.ap.a.pvp.net"),
            mock.patch.object(reader, "_pd", side_effect=TypeError("surprise")),
        ):
            with self.assertRaises(TypeError):
                reader._ask_ranks({}, {}, [subject])()
        self.assertNotIn(subject, reader._asking, "the next look asks again")


class BrokenAnswerPagesTest(unittest.TestCase):
    """HenrikDev の試合の一部の型が違っても、プレイヤーページと試合ページは出る（以前は 500）。"""

    def setUp(self):
        self.app, self.fetch, _ = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        henrik_reader = self.app.config["VAL_HENRIK"]
        plain = henrik_reader.fetch

        def broken(url, headers):
            status, body = plain(url, headers)
            if status == 200 and ("/v4/matches/" in url or "/v4/by-puuid/matches/" in url or "/v4/match/" in url):
                data = json.loads(body)
                matches = data["data"] if isinstance(data["data"], list) else [data["data"]]
                for match in matches:
                    match["metadata"]["map"] = "Ascent"
                    for player in match["players"]:
                        player.update(stats="broken", party_id={"x": 1}, agent=True, economy=[1])
                    for team in match.get("teams") or []:
                        team["rounds"] = "x"
                body = json.dumps(data).encode()
            return status, body

        henrik_reader.fetch = broken
        self.client = self.app.test_client()

    def test_the_pages_still_open(self):
        for page in ("/p/Tester/JP1?full=1", "/m/ap/aaaaaaaa-0000-4000-8000-000000000001?me=Tester%23JP1"):
            self.assertEqual(self.client.get(page).status_code, 200, page)


if __name__ == "__main__":
    unittest.main()
