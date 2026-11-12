"""The app with the tests' made-up answers of the outside APIs (tests/helpers.py), for looking at the pages and for
tests/ui/audit.js: the owner signed in to Riot (so /queues and the owner's store show), made-up matches on the servers
page, pictures a browser can show, one player with the longest name there can be, a lifetime of matches for the
made-up player (pages of them, long charts), two saved sets of the owner's loadout, a ``visitor=1`` cookie that
shows a page as a visitor sees it, and ``/collection?loadout=none`` (Riot gives no loadout).

    python tests/ui/serve.py [port, default 5077] [--desktop]

--desktop: the pages as the PC app shows them (valtracker/desktop.py): signed in through a made-up Riot Client on
the PC (tests/test_desktop.py's FakeClient), not with a pasted ssid.
"""

import json
import math
import os
import random
import struct
import sys
import tempfile
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(TESTS), str(TESTS.parent)]
os.environ["VAL_HENRIK_KEY"] = ""

import helpers  # noqa: E402
from helpers import make_app  # noqa: E402

#: The made-up servers and how many matches each had in 14 days (Mumbai: too few to tell its busy hours).
SERVERS = {"Tokyo": 900, "Hong Kong": 420, "Singapore": 380, "Sydney": 260, "Mumbai": 30}


#: The longest Riot ID there can be, in wide letters (16 + 5): one made-up player has it, for long names in tables.
LONGEST = ("長い名前のプレイヤーです一二三四", "WWWWW")


def _fixture(name: str, original=helpers.fixture) -> bytes:
    data = original(name)
    if name == "henrik_matches.json":
        longest, tag = LONGEST
        data = data.replace(b'"name": "Other"', f'"name": "{longest}"'.encode())
        data = data.replace(b'"tag": "EUW"', f'"tag": "{tag}"'.encode())
    return data


def _png(size: int = 64) -> bytes:
    """A grey square that a browser can show (the tests' PNG is only its first bytes): a picture takes its room on
    the pages as the real ones do, so the layout check sees what a visitor sees."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\x88\x88\x88" * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _match(match_id: str, started: float, cluster: str, queue: str, tier: int, pick=None) -> dict:
    """A made-up match; with ``pick`` (a random.Random) ten players' numbers too, the yardstick of the scores."""
    found = {
        "metadata": {
            "match_id": match_id,
            "region": "ap",
            "cluster": cluster,
            "queue": {"id": queue},
            "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
            "game_length_in_ms": 2_100_000,
        },
        "players": [{"puuid": "", "tier": {"id": tier}}],
    }
    if pick:
        found["teams"] = [
            {"team_id": "Red", "won": True, "rounds": {"won": 13, "lost": 9}},
            {"team_id": "Blue", "won": False, "rounds": {"won": 9, "lost": 13}},
        ]
        found["players"] = []
        for number in range(10):
            kills, deaths = pick.randint(5, 28), pick.randint(8, 22)
            heads, bodies = pick.randint(5, 30), pick.randint(30, 80)
            found["players"].append(
                {
                    "puuid": "",
                    "team_id": "Red" if number < 5 else "Blue",
                    "tier": {"id": max(3, min(27, tier + pick.randint(-2, 2)))},
                    "stats": {
                        "kills": kills,
                        "deaths": deaths,
                        "assists": pick.randint(2, 12),
                        "score": kills * 200 + pick.randint(500, 2500),
                        "damage": {"dealt": kills * 140 + pick.randint(0, 900), "received": deaths * 140},
                        "headshots": heads,
                        "bodyshots": bodies,
                        "legshots": pick.randint(2, 12),
                    },
                }
            )
    return found


def seed(activity, now: float) -> None:
    """Matches over the last 14 days, most in the evening (Japan time), and waits between pairs of them."""
    pick = random.Random(4)
    listed = []
    for cluster, size in SERVERS.items():
        for number in range(size):
            while True:  # evenings busy, mornings quiet
                hour = pick.random() * 24
                if pick.random() < 0.15 + 0.85 * (0.5 + 0.5 * math.cos((hour - 22) / 12 * math.pi)) ** 2:
                    break
            started = now // 86400 * 86400 - pick.randint(0, 13) * 86400 + (hour - 9) * 3600
            started = started - 86400 if started > now else started
            tier = pick.randint(8, 24)
            listed.append(
                _match(f"{cluster}-{number}", started, cluster, "competitive", tier, pick if number < 60 else None)
            )
    activity.seen(listed)
    for number in range(60):
        start = now - pick.randint(1, 13) * 86400
        tier = pick.choice((10, 12, 17, 18, 22, 25))
        queue = "competitive" if number % 3 else "unrated"
        wait = int(pick.gauss(150 + tier * 6, 40))
        first = _match(f"pair-{number}-a", start, "Tokyo", queue, tier)
        second = _match(f"pair-{number}-b", start + 2100 + wait, "Tokyo", queue, tier)
        activity.seen([first, second], f"player-{number}")


def seed_lifetime(app, now: float) -> None:
    """The made-up player's lifetime (archive.py): 60 more of their matches over two years, 25 stored lines older
    than those (no rounds) and the RR of 80 competitive matches, so the lifetime page has pages and long charts."""
    from valtracker import henrik

    pick = random.Random(7)
    base = henrik._slim_matches(json.loads(helpers.fixture("henrik_matches.json"))["data"])
    matches, changes, elo = [], {}, 1650
    for number in range(60):
        match = json.loads(json.dumps(base[number % len(base)]))
        started = now - (number + 1) * 11 * 86400 - pick.randint(0, 80000)
        match["metadata"]["match_id"] = f"dddddddd-0000-4000-8000-{number:012d}"
        match["metadata"]["started_at"] = datetime.fromtimestamp(started, UTC).isoformat()
        for player in match["players"]:
            player["stats"]["kills"] = pick.randint(6, 30)
            player["stats"]["deaths"] = pick.randint(8, 22)
        matches.append(match)
    for number in range(80):
        change = pick.choice((-1, 1)) * pick.randint(12, 28)
        elo += change
        changes[f"dddddddd-0000-4000-8000-{number:012d}"] = {
            "change": change,
            "tier": elo // 100 + 3,
            "rr": elo % 100,
            "elo": elo,
            "at": int(now - (number + 1) * 8 * 86400),
        }
    lines = []
    for number in range(25):
        started = now - (700 + number * 9) * 86400
        lines.append(
            {
                "id": f"eeeeeeee-0000-4000-8000-{number:012d}",
                "season": "e5a1",
                "started": datetime.fromtimestamp(started, UTC).isoformat().replace("+00:00", "Z"),
                "at": int(started),
                "queue": "competitive",
                "cluster": "Tokyo",
                "map": "Ascent",
                "map_id": "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319",
                "us": 13,
                "them": pick.randint(3, 11) if number % 3 else 14,
                "agent": "11111111-1111-4111-8111-111111111111",
                "tier": 14,
                "won": bool(number % 3),
                "rounds": 24,
                "kills": pick.randint(10, 25),
                "deaths": pick.randint(10, 20),
                "assists": pick.randint(2, 9),
                "score": pick.randint(3500, 6500),
                "head": pick.randint(10, 30),
                "body": pick.randint(40, 70),
                "leg": pick.randint(2, 10),
                "made": pick.randint(2500, 4000),
                "received": pick.randint(2500, 3800),
            }
        )
    archive = app.config["VAL_ARCHIVE"]
    archive.keep(matches)
    archive.keep_thin("puuid-me", lines)
    archive.keep_rr("puuid-me", changes)


def seed_sets(app) -> None:
    """Two saved sets on the collection's セット tab: what is worn now (the longest name there can be) and another."""
    from valtracker.collection import snapshot

    now = snapshot(app.riot.pd["loadout"])
    other = json.loads(json.dumps(now))
    other["Guns"][0] |= {"SkinID": "aa000000-0000-4000-8000-000000000002", "ChromaID": ""}
    for key in ("CharmInstanceID", "CharmID", "CharmLevelID"):
        other["Guns"][0].pop(key, None)
    sets = [
        {
            "id": "set-1",
            "name": "ランク用のいちばん気に入っている装備セットそのいち三十字",
            "at": "2026-11-06T21:05+09:00",
            **now,
        },
        {"id": "set-2", "name": "アンレート", "at": "2026-11-07T01:40+09:00", **other},
    ]
    app.config["VAL_SETS"].save(sets=sets)


def full_lobby(app) -> None:
    """A whole match on /live, as the owner meets it: five in agent select, five against five in the match (the
    fixture has three, enough for the tests). Long names, a player who hides theirs, one without a rank."""
    pd = app.riot.pd
    agents = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
    names = (
        "ながいなまえのプレイヤーさん",
        "Sova Main",
        "xX_Duelist_Xx",
        "初心者です",
        "TenZ fan",
        "Controller",
        "last one",
    )
    season = next(iter(next(iter(pd["mmr"].values()))["QueueSkills"]["competitive"]["SeasonalInfoBySeasonID"]))
    for number, name in enumerate(names):
        subject = f"b2222222-0000-4000-8000-{number:012d}"
        hidden = number == 3
        tier = (14, 17, 19, 0, 22, 16, 18)[number]
        identity = {"AccountLevel": 20 + number * 37, "Incognito": hidden, "HideAccountLevel": number == 5}
        pd["names"].append({"Subject": subject, "GameName": name, "TagLine": f"{number + 100}"})
        played = {"CompetitiveTier": tier, "RankedRating": 11 * number, "NumberOfWinsWithPlacements": 9 + number}
        played |= {"NumberOfGames": 20 + number, "WinsByTier": {str(tier + 1): 2}}
        pd["mmr"][subject] = {
            "QueueSkills": {"competitive": {"SeasonalInfoBySeasonID": {season: played} if tier else {}}},
            "LatestCompetitiveUpdate": {"SeasonID": season, "TierAfterUpdate": tier, "RankedRatingAfterUpdate": 11},
        }
        pd["coregame"]["Players"].append(
            {
                "Subject": subject,
                "TeamID": "Red" if number < 4 else "Blue",
                "CharacterID": agents[number % 2],
                "PlayerIdentity": identity,
                "SeasonalBadgeInfo": {"Rank": tier},
            }
        )
        if number < 2:
            pd["pregame"]["AllyTeam"]["Players"].append(
                {
                    "Subject": subject,
                    "CharacterID": agents[number % 2] if number else "",
                    "CharacterSelectionState": "selected" if number else "",
                    "CompetitiveTier": tier,
                    "PlayerIdentity": identity,
                }
            )


def last_match(app) -> None:
    """Riot's record of the match the owner is in on /live?phase=ingame: once it is "over" (/live?phase=menus, or
    the game closed), /live shows its result (owner._last_result)."""
    pd = app.riot.pd
    match_id = pd["coregame"]["MatchID"].lower()
    record = json.dumps(pd["matches"]["bbbbbbbb-0000-4000-8000-000000000001"]).replace(pd["player"], helpers.PUUID)
    record = json.loads(record)
    record["matchInfo"] |= {"matchId": match_id, "gameStartMillis": int(time.time() - 2400) * 1000}
    pd["matches"][match_id] = record
    pd["player"] = helpers.PUUID
    pd["updates"]["Matches"][0]["MatchID"] = match_id


def desktop_client(folder: Path):
    """The Riot Client of the PC app, made up: signed in, VALORANT open. Its events are not listened to, so where
    the game is comes from the made-up Riot servers (/live?phase=...), as on the site."""
    from test_desktop import FakeClient

    from valtracker.localclient import LocalClient

    lockfile = folder / "lockfile"
    lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
    return LocalClient(lockfile, folder / "ShooterGame.log", ask=FakeClient())


def main() -> None:
    flags = [arg for arg in sys.argv[1:] if arg.startswith("--")]
    numbers = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    port = int(numbers[0]) if numbers else 5077
    helpers.PNG = _png()
    helpers.fixture = _fixture
    local = None
    if "--desktop" in flags:
        client_folder = tempfile.TemporaryDirectory()
        local = desktop_client(Path(client_folder.name))
    app, fetch, _ = make_app(local=local)
    fetch.esports_page = True  # the matches to come from Riot's esports site, the results from HenrikDev
    if local is None:
        app.config["VAL_RIOT"].sign_in("ssid-1")
    seed(app.config["VAL_ACTIVITY"], time.time())
    seed_lifetime(app, time.time())
    # a few days of the owner's store, and a skin wanted that is in today's (diary.py)
    diary = app.config["VAL_DIARY"]
    for back in range(1, 4):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - back * 86400))
        diary.note(day, [{"uuid": "ac000000-0000-4000-8000-000000000001", "price": 3550}], None)
    diary.want("ac000000-0000-4000-8000-000000000001", True)
    seed_sets(app)
    full_lobby(app)
    last_match(app)
    inner = app.wsgi_app
    next_live: list[str] = []

    def wsgi(environ, start):
        if "visitor=1" in environ.get("HTTP_COOKIE", ""):
            environ["homepage.visitor"] = True
        # /live?phase=menus, queue (the menus, matchmaking), found (the match found), pregame or ingame: where the
        # owner's made-up game is (helpers.FakeRiot); none: the game is closed. A button pressed on /live (POST
        # /live/act, then /live) keeps the phase of the page it was pressed on.
        if environ.get("PATH_INFO") == "/live/act":
            pressed_on = environ.get("HTTP_REFERER", "").partition("?")[2]
            next_live.append(pressed_on.removeprefix("phase=") if pressed_on.startswith("phase=") else "")
        if environ.get("PATH_INFO") == "/live":
            asked = environ.get("QUERY_STRING", "")
            phase = asked.removeprefix("phase=") if asked or not next_live else next_live.pop()
            next_live.clear()
            game = {"queue": "menus", "found": "menus"}.get(phase, phase)
            app.riot.phase = game if game in ("menus", "pregame", "ingame") else ""
            party = app.riot.pd["party"]
            party["State"] = {"queue": "MATCHMAKING", "found": "MATCHMADE_GAME_STARTING"}.get(phase, "DEFAULT")
            # agent select's timer counts down, as in the game: the page changes at every look
            app.riot.pd["pregame"]["PhaseTimeRemainingNS"] = int(85 - time.time() % 80) * 1_000_000_000
            party["QueueEntryTime"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 95))
                if phase == "queue"
                else "0001-01-01T00:00:00Z"
            )
            app.config["VAL_RIOT"].cache.put("live", None, 0)
        # /collection?...&loadout=none: Riot gives no loadout (what is worn cannot be read)
        if environ.get("PATH_INFO", "").startswith("/collection"):
            version = 1 if "loadout=none" in environ.get("QUERY_STRING", "") else 3
            if app.riot.loadout_version != version:
                app.riot.loadout_version = version
                app.config["VAL_RIOT"].cache.put("collection", None, 0)
        return inner(environ, start)

    app.wsgi_app = wsgi
    app.run("127.0.0.1", port, threaded=True)


if __name__ == "__main__":
    main()
