"""A player's lifetime: the game's own match records in HenrikDev's shape (pdmatch.py), the archive and its
background reading (archive.py), and the player page's "通算" (views._lifetime)."""

from __future__ import annotations

import json
import os
import re
import unittest
from datetime import datetime
from unittest import mock

from helpers import fixture, make_app

from valtracker import archive as archive_module
from valtracker import henrik, i18n, pdmatch, rounds, stats, views
from valtracker.stats import JST

PD = json.loads(fixture("riot_match.json"))
PLAYER = PD["player"]
M1, M2 = (item["MatchID"] for item in reversed(PD["history"]["History"]))
MAPS = {"ascent": {"uuid": "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319", "displayName": "Ascent"}}


def converted(match_id: str = M1) -> dict:
    return pdmatch.to_v4(PD["matches"][match_id], "ap", MAPS, PLAYER, "Pd Player", "PD1")


class PdMatchTest(unittest.TestCase):
    def test_the_match_reads_as_henriks(self):
        raw = converted()
        metadata = raw["metadata"]
        self.assertEqual(metadata["match_id"], M1)
        self.assertEqual(metadata["map"], {"id": MAPS["ascent"]["uuid"], "name": "Ascent"})
        self.assertEqual(metadata["queue"], {"id": "competitive", "name": "", "mode_type": "Standard"})
        self.assertEqual(metadata["cluster"], "Tokyo")
        self.assertEqual(metadata["started_at"], "2025-11-06T03:33:20Z")
        self.assertEqual(len(raw["players"]), 4, "the observer is not a player")
        slim = henrik._slim_match(raw)
        me = stats.find_player(slim, PLAYER)
        line = stats.line(me, stats.rounds_played(slim), stats.advanced(slim, me))
        self.assertEqual((line["kills"], line["deaths"], line["assists"]), (3, 1, 1))
        self.assertEqual((line["headshots"], line["bodyshots"], line["legshots"]), (2, 4, 1))
        self.assertEqual((line["damage"], line["taken"]), (440, 150))
        self.assertEqual(line["acs"], 300)
        self.assertEqual(line["econ"], round(440 * 1000 / 6900))
        self.assertEqual((line["fk"], line["fd"], line["mk"]), (2, 1, 0))
        self.assertEqual(stats.advanced(slim, me)["mk"], [1, 0, 0, 0])
        self.assertEqual(stats.team_result(slim, "Red"), {"won": True, "us": 2, "them": 1, "rounds": True})
        self.assertEqual(slim["digest"]["sides"]["Red"], {"atk": [0, 0], "def": [2, 3]}, "Blue planted: Red defended")
        self.assertEqual(slim["digest"]["rounds"][1]["s"], "A")
        self.assertEqual(slim["digest"]["rounds"][2]["c"], "clutch")
        self.assertEqual(me["agent"]["id"], "11111111-1111-4111-8111-111111111111")
        # a weapon is known by its uuid (lower case); an ability keeps the name PD gives it
        self.assertEqual(
            slim["digest"]["players"][0]["weapons"], {"9c82e19d-4575-0200-1a81-3eacf00cf872": 2, "ultimate": 1}
        )

    def test_only_the_player_is_named(self):
        raw = converted()
        named = {player["puuid"]: (player["name"], player["tag"]) for player in raw["players"]}
        self.assertEqual(named[PLAYER], ("Pd Player", "PD1"))
        self.assertEqual({value for key, value in named.items() if key != PLAYER}, {("", "")})
        self.assertNotIn("Secret Mate", json.dumps(henrik._slim_match(raw)))

    def test_kills_from_the_rounds_when_the_match_has_none(self):
        details = json.loads(json.dumps(PD["matches"][M1]))
        for kill in details.pop("kills"):
            line = next(
                item
                for item in details["roundResults"][kill["round"]]["playerStats"]
                if item["subject"] == kill["killer"]
            )
            line["kills"].append({key: value for key, value in kill.items() if key != "round"})
        raw = pdmatch.to_v4(details, "ap", MAPS, PLAYER, "Pd Player", "PD1")
        self.assertEqual(len(raw["kills"]), 6)
        self.assertEqual(rounds.digest(raw)["players"][0]["fk"], 2)

    def test_what_is_not_a_match(self):
        self.assertEqual(pdmatch.to_v4({}, "ap", MAPS), {})
        self.assertEqual(pdmatch.to_v4({"matchInfo": {}}, "ap", MAPS), {})
        deathmatch = json.loads(json.dumps(PD["matches"][M1]))
        deathmatch["matchInfo"]["queueID"] = "deathmatch"
        raw = pdmatch.to_v4(deathmatch, "ap", MAPS)
        self.assertEqual(raw["teams"], [])
        self.assertEqual(raw["metadata"]["queue"]["mode_type"], "Deathmatch")

    def test_servers(self):
        self.assertEqual(pdmatch.city("aresriot.aws-rclusterprod-ape1-1.ap-gp-hongkong-1"), "Hong Kong")
        self.assertEqual(pdmatch.city("aresriot.aws-rclusterprod-use1-1.na-gp-ashburn-1"), "N. Virginia")
        self.assertEqual(pdmatch.city("aresriot.aws-x.eu-gp-somewhere-1"), "Somewhere")
        self.assertEqual(pdmatch.city(""), "")

    def test_history_and_rr(self):
        found = pdmatch.history(PD["history"])
        self.assertEqual(found["total"], 2)
        self.assertEqual([item["id"] for item in found["matches"]], [M2, M1])
        self.assertEqual(found["matches"][0]["queue"], "unrated")
        changes = pdmatch.rr_updates(PD["updates"])
        self.assertEqual(list(changes), [M1], "a placement match moves no RR")
        self.assertEqual(changes[M1], {"change": 18, "tier": 21, "rr": 58, "elo": 1858, "at": 1762400000})
        self.assertEqual(pdmatch.history("x"), {"total": 0, "matches": []})


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.archive = self.app.config["VAL_ARCHIVE"]

    def test_kept_once_and_henriks_wins(self):
        slim = henrik._slim_match(converted())
        self.assertEqual(self.archive.keep([slim], "riot"), 1)
        self.assertEqual(self.archive.keep([slim], "riot"), 0)
        self.assertEqual(self.archive.known([M1, M2]), {M1})
        named = json.loads(json.dumps(slim))
        for player in named["players"]:
            player["name"], player["tag"] = "Named", "X"
        self.archive.keep([named], "henrik")
        kept = list(self.archive.matches(PLAYER))
        self.assertEqual(kept[0]["players"][1]["name"], "Named")
        self.archive.keep([slim], "riot")
        self.assertEqual(next(self.archive.matches(PLAYER))["players"][1]["name"], "Named", "not replaced by Riot's")
        # each player of the match has it in their own list
        self.assertEqual(len(list(self.archive.matches("22222222-3333-4444-8555-666666666666"))), 1)
        self.assertEqual(list(self.archive.matches(PLAYER, "unrated")), [])

    def test_unfinished_matches_are_not_kept(self):
        slim = henrik._slim_match(converted())
        slim["metadata"]["is_completed"] = False
        self.assertEqual(self.archive.keep([slim, {"metadata": {}}, "x"]), 0)

    def test_thin_lines_and_rr(self):
        lines = henrik._slim_stored(json.loads(fixture("henrik_stored.json"))["data"])
        self.archive.keep_thin("puuid-me", lines)
        thin = self.archive.thin("puuid-me")
        self.assertEqual(len(thin), len(lines))
        self.assertEqual(thin[0]["queue"], "competitive")
        self.assertGreater(thin[0]["at"], thin[-1]["at"])
        self.archive.keep_rr(PLAYER, pdmatch.rr_updates(PD["updates"]))
        self.assertEqual(self.archive.rr(PLAYER)[M1]["change"], 18)
        self.assertEqual(self.archive.version(PLAYER), (0, 0, 1))
        self.assertGreater(self.archive.first("puuid-me"), 0)


class FillerTest(unittest.TestCase):
    def setUp(self):
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.archive = self.app.config["VAL_ARCHIVE"]
        self.filler = self.app.config["VAL_FILLER"]
        self.riot = self.app.riot
        self.client = self.app.test_client()

    def link(self):
        page = self.client.get("/settings").get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        self.client.post("/settings", data={"csrf": token, "do": "riot", "riot": "ssid-1"})

    def steps(self, most: int = 20) -> list[str]:
        done = []
        with mock.patch.object(archive_module, "HENRIK_SECONDS", 0):
            for _ in range(most):
                did = self.filler.step()
                if not did:
                    break
                done.append(did)
        return done

    def test_riot_first(self):
        self.link()
        with self.app.app_context():
            self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.assertEqual(self.steps(), ["history", "match", "match", "rr", "stored", "done"])
        self.assertEqual(self.archive.known([M1, M2]), {M1, M2})
        read = [url.rsplit("/", 1)[-1] for _, url, _, _ in self.riot.asked if "/match-details/" in url]
        self.assertEqual(read, [M2, M1], "the newest first")
        self.assertEqual(self.archive.rr(PLAYER)[M1]["rr"], 58)
        state = self.archive.player(PLAYER)
        self.assertEqual((state["via"], state["total"], state["scanned"]), ("", 2, 2))
        self.assertFalse(archive_module.progress(state, self.archive)["busy"])
        self.assertTrue(any("/v1/stored-matches/ap/Pd%20Player/PD1" in url for url in self.fetch.henrik_calls()))
        self.assertFalse(any("/v4/matches/" in url for url in self.fetch.henrik_calls()), "Riot has the matches")

    def test_new_matches_only_when_asked_again(self):
        self.link()
        self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.steps()
        calls = self.riot.pd_calls()
        self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.assertEqual(self.steps(), [], "read through a moment ago")
        later = self.archive.clock() + archive_module.AGAIN + 1
        with mock.patch.object(self.archive, "clock", return_value=later):
            self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.assertEqual(self.steps(), ["history", "rr", "done"])
        self.assertEqual(self.riot.pd_calls() - calls, 2)

    def test_a_match_riot_no_longer_has(self):
        self.link()
        del self.riot.pd["matches"][M2]
        self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.assertEqual(self.steps(), ["history", "match", "match", "rr", "stored", "done"])
        self.assertEqual(self.archive.known([M1, M2]), {M1})
        self.assertEqual(self.archive.player(PLAYER)["via"], "", "the rest still from Riot")

    def test_too_many_calls_rest(self):
        self.link()
        self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.riot.broken.add("history")
        with mock.patch.object(self.riot, "_answer", side_effect=lambda kind, value: (429, [], b"{}")):
            self.assertEqual(self.filler.step(), "rest")
        self.assertEqual(self.filler.step(), "rest", "resting a while")
        self.assertEqual(self.archive.player(PLAYER)["via"], "")

    def test_henrik_without_riot(self):
        self.filler.want("ap", "puuid-me", "Tester", "JP1")
        self.assertEqual(self.steps(), ["switch", "older", "stored", "done"])
        state = self.archive.player("puuid-me")
        self.assertEqual((state["via"], state["problem"]), ("henrik", "down"), "not a puuid Riot knows")
        self.assertTrue(any("/v4/matches/ap/pc/Tester/JP1?size=10&start=0" in url for url in self.fetch.henrik_calls()))
        self.assertEqual(len(list(self.archive.matches("puuid-me"))), 2)
        self.assertEqual(
            self.archive.thin("puuid-me"),
            [line for line in self.archive.thin("puuid-me") if line["id"].startswith("ss")],
        )

    def test_no_linked_account(self):
        self.filler.want("ap", PLAYER, "Pd Player", "PD1")
        self.assertEqual(self.filler.step(), "switch")
        self.assertEqual(self.archive.player(PLAYER)["problem"], "nologin")
        self.assertEqual(self.riot.pd_calls(), 0)

    def test_henrik_leaves_the_visitors_room(self):
        self.filler.want("ap", "puuid-me", "Tester", "JP1")
        self.filler.step()  # switch
        henrik_key = self.app.config["VAL_HENRIK"]
        while henrik_key.budget.room() >= archive_module.HENRIK_ROOM:
            henrik_key.budget.take()
        with mock.patch.object(archive_module, "HENRIK_SECONDS", 0):
            self.assertEqual(self.filler.step(), "wait")
        self.assertEqual(len(list(self.archive.matches("puuid-me"))), 0)


class LifetimePageTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app, self.fetch, self.folder = make_app()
        self.addCleanup(self.app._test_folder.cleanup)
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")
        self.archive = self.app.config["VAL_ARCHIVE"]

    def page(self, path: str) -> str:
        answer = self.client.get(path)
        self.assertEqual(answer.status_code, 200)
        return answer.get_data(as_text=True)

    def test_the_recent_list_is_kept(self):
        text = self.page("/p/Tester/JP1?full=1")
        self.assertIn('href="/p/Tester/JP1?span=all"', text)
        self.assertIn(">直近 15 試合</a>", text)
        self.assertEqual(len(list(self.archive.matches("puuid-me"))), 2)

    def test_lifetime(self):
        self.app.config["VAL_ARCHIVE"].keep_thin(
            "puuid-me", henrik._slim_stored(json.loads(fixture("henrik_stored.json"))["data"])
        )
        text = self.page("/p/Tester/JP1?span=all&full=1")
        whole = len(list(self.archive.matches("puuid-me")))
        thin = len([line for line in self.archive.thin("puuid-me") if line["rounds"]])
        self.assertIn(f"通算 {whole + thin} 試合", text)
        self.assertIn(f"うち {thin} 試合は簡単な記録だけ", text)
        self.assertIn(f"残りの {whole} 試合から", text)
        self.assertIn("からの", text)
        self.assertIn('aria-current="true">通算</a>', text)
        self.assertIn("すべての試合", text)
        self.assertIn('href="/p/Tester/JP1?mode=competitive&amp;span=all"', text)
        self.assertIn("RR の推移 · 通算", text)
        self.assertEqual(text.count('class="match '), whole + thin)
        self.assertIn("残りの試合を読み込んでいます", text, "the background has not read them yet")
        # its reading was asked for
        self.assertEqual(self.archive.player("puuid-me")["name"], "Tester")

    def test_many_matches_have_pages(self):
        base = henrik._slim_matches(json.loads(fixture("henrik_matches.json"))["data"])[0]
        many = []
        for number in range(45):
            match = json.loads(json.dumps(base))
            match["metadata"]["match_id"] = f"cccccccc-0000-4000-8000-{number:012d}"
            match["metadata"]["started_at"] = f"2026-10-{1 + number % 28:02d}T{number % 24:02d}:00:00Z"
            many.append(match)
        self.archive.keep(many)
        text = self.page("/p/Tester/JP1?span=all&full=1")
        self.assertEqual(text.count('class="match '), views.LIFE_PAGE)
        self.assertIn("1 / 3 ページ", text)
        self.assertIn('href="/p/Tester/JP1?span=all&amp;page=2#m-title"', text)
        last = self.page("/p/Tester/JP1?span=all&page=9&full=1")
        self.assertIn("3 / 3 ページ", last)
        self.assertIn('href="/p/Tester/JP1?span=all&amp;page=2#m-title" rel="prev"', last)
        self.assertEqual(last.count('class="match '), 47 - 2 * views.LIFE_PAGE)

    def test_the_part_carries_the_span(self):
        with mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": "HDEV-x"}):
            shell = self.page("/p/Tester/JP1?span=all&page=2")
        self.assertIn('data-part="/p/Tester/JP1/part?span=all&amp;page=2"', shell)
        part = self.page("/p/Tester/JP1/part?span=all")
        self.assertIn("通算", part)

    def test_one_act(self):
        # ACT I: 2026-01-07 to 03-01; ACT III (being played): from 03-01 (the fixture's seasons)
        base = henrik._slim_matches(json.loads(fixture("henrik_matches.json"))["data"])[0]
        many = []
        for number, started in enumerate(["2026-01-20", "2026-02-10", "2026-02-11", "2026-05-01", "2026-06-01"]):
            match = json.loads(json.dumps(base))
            match["metadata"]["match_id"] = f"dddddddd-0000-4000-8000-{number:012d}"
            match["metadata"]["started_at"] = f"{started}T10:00:00Z"
            many.append(match)
        self.archive.keep(many)
        first = "aaaa0000-0000-4000-8000-000000000002"
        with mock.patch.dict(os.environ, {"VAL_HENRIK_KEY": "HDEV-x"}):
            shell = self.page(f"/p/Tester/JP1?span=act&act={first}")
        self.assertIn(f'data-part="/p/Tester/JP1/part?span=act&amp;act={first}"', shell)
        text = self.page("/p/Tester/JP1?span=act&full=1")
        self.assertIn('aria-current="true">アクト</a>', text)
        self.assertIn("エピソード 9 · ACT III · 4 試合", text)  # the 2 recent matches and 2 of May and June
        self.assertIn("エピソード 9 · ACT III（2026年3月1日 から）の 4 試合を数えています。", text)
        self.assertIn("RR の推移 · エピソード 9 · ACT III", text)
        self.assertIn(
            '<option value="aaaa0000-0000-4000-8000-000000000003" selected>エピソード 9 · ACT III （いま）', text
        )
        self.assertIn(f'<option value="{first}">エピソード 9 · ACT I</option>', text)
        self.assertIn('href="/p/Tester/JP1?mode=competitive&amp;span=act"', text)
        self.assertEqual(text.count('class="match '), 4)

        old = self.page(f"/p/Tester/JP1?span=act&act={first}&full=1")
        self.assertIn("エピソード 9 · ACT I · 3 試合", old)
        self.assertIn(f'href="/p/Tester/JP1?mode=competitive&amp;span=act&amp;act={first}"', old)
        self.assertEqual(old.count('class="match '), 3)
        self.assertIn("エピソード 9 · ACT III · 4 試合", self.page("/p/Tester/JP1?span=act&act=nonsense&full=1"))

        self.assertIn("ACT I · 3 試合", self.page(f"/p/Tester/JP1/part?span=act&act={first}"))

    def test_the_recent_list_leads_on(self):
        text = self.page("/p/Tester/JP1?full=1")
        self.assertIn(
            '<a class="button quiet" href="/p/Tester/JP1?span=all#m-title">もっと前の試合（通算） →</a>', text
        )
        self.assertIn('class="narrow"', text)  # the charts drawn for a phone too
        self.assertIn('<p class="chart-days">', text)

    def test_kept_until_something_is_added(self):
        self.page("/p/Tester/JP1?span=all&full=1")
        with mock.patch.object(self.archive, "matches", side_effect=AssertionError("worked out again")):
            self.page("/p/Tester/JP1?span=all&full=1")
        self.archive.keep_thin("puuid-me", henrik._slim_stored(json.loads(fixture("henrik_stored.json"))["data"]))
        with mock.patch.object(self.archive, "matches", wraps=self.archive.matches) as matches:
            self.page("/p/Tester/JP1?span=all&full=1")
        self.assertTrue(matches.called)


class ChartsTest(unittest.TestCase):
    def test_averages(self):
        self.assertEqual(views._averages([1, 2, 3], 5), [1, 2, 3])
        self.assertEqual(views._averages(list(range(100)), 4), [12, 37, 62, 87])
        self.assertEqual(len(views._averages(list(range(1001)), views.SCORE_POINTS)), views.SCORE_POINTS)

    def test_a_long_rr_line_has_fewer_dots(self):
        app, _, _ = make_app()
        self.addCleanup(app._test_folder.cleanup)
        changes = {
            f"m{number}": {
                "change": 10,
                "tier": 15,
                "rr": number % 100,
                "elo": 1200 + number,
                "at": 1_700_000_000 + number,
            }
            for number in range(500)
        }
        with app.test_request_context():
            from flask import g

            g.lang = "ja"
            chart = views._rr_chart(changes)
        self.assertEqual(chart["games"], 500)
        self.assertLessEqual(len(chart["dots"]), views.RR_DOTS + 1)
        self.assertEqual(chart["dots"][-1]["rr"], 499 % 100)
        self.assertEqual(chart["path"].count(" L"), 499, "the line goes through every match")

    def test_an_old_match_has_its_year(self):
        old = datetime(2024, 3, 4, 21, 5, tzinfo=JST)
        self.assertEqual(i18n.when("ja", old), "2024年3月4日 21:05")
        self.assertEqual(i18n.when("en-GB", old), "4 Mar 2024 21:05")
        now = datetime.now(JST).replace(month=1, day=2, hour=3, minute=4)
        self.assertEqual(i18n.when("ja", now), "1月2日 3:04")


if __name__ == "__main__":
    unittest.main()
