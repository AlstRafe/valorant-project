"""The app with the tests' made-up answers of the outside APIs (tests/helpers.py), for looking at the pages and for
tests/ui/audit.js: the owner signed in to Riot (so /queues and the owner's store show), made-up matches on the servers
page, pictures a browser can show, one player with the longest name there can be, a lifetime of matches for the
made-up player (pages of them, long charts), two saved sets of the owner's loadout, a ``visitor=1`` cookie that
shows a page as a visitor sees it, and ``/collection?loadout=none`` (Riot gives no loadout).

    python tests/ui/serve.py [port, default 5077] [--desktop] [--real]

--desktop: the pages as the PC app shows them (valtracker/desktop.py): signed in through a made-up Riot Client on
the PC (tests/test_desktop.py's FakeClient), not with a pasted ssid.

--real: the game's own names and pictures (valorant-api.com is asked for real, so this needs the network) around
the made-up players and matches: the pages as an owner sees them, for the screenshots (tests/ui/shots.py). The
fixtures' made-up ids are swapped for the real things they stand for, and the made-up player's matches are whole
ones (played.py: five against five, on several maps). ``?update=1`` on any address says that a newer PC app is out.
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
import played  # noqa: E402
from helpers import make_app  # noqa: E402

from valtracker import release  # noqa: E402

#: The made-up servers and how many matches each had in 14 days (Mumbai: too few to tell its busy hours).
SERVERS = {"Tokyo": 900, "Hong Kong": 420, "Singapore": 380, "Sydney": 260, "Mumbai": 30}


#: The longest Riot ID there can be, in wide letters (16 + 5): one made-up player has it, for long names in tables.
LONGEST = ("長い名前のプレイヤーです一二三四", "WWWWW")


JETT, SOVA = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
#: --real: where the game's names and pictures are asked for real.
REAL_SITES = ("https://valorant-api.com/", "https://media.valorant-api.com/")
#: --real: the made-up skins -> the real ones they stand for (their English names).
REAL_SKINS = {
    "aa000000-0000-4000-8000-000000000001": "Prime Vandal",
    "aa000000-0000-4000-8000-000000000002": "Reaver Vandal",
    "aa000000-0000-4000-8000-000000000009": "Standard Vandal",
    "ac000000-0000-4000-8000-000000000001": "Reaver Knife",
    "ab000000-0000-4000-8000-000000000001": "Prime Classic",
}
#: --real: more skins for the day's store (it has four), and who plays what in the made-up lobby.
REAL_STORE = ("Oni Phantom", "Ion Sheriff", "Glitchpop Frenzy")
REAL_LOBBY = ("Jett", "Omen", "Killjoy", "Sage", "Raze", "Viper", "Cypher", "Breach", "Reyna", "Skye")
#: --real: the maps the made-up matches are played on (those of these that the game has now), and what the made-up
#: player plays and how often.
REAL_MAPS = ("Ascent", "Haven", "Bind", "Lotus", "Sunset", "Split", "Icebox", "Pearl", "Abyss", "Breeze", "Corrode")
REAL_MINE = (("Jett", 5), ("Reyna", 2), ("Omen", 2), ("Sova", 1))
#: --real: made-up id -> real id (real_ids), the real things found by their English names (maps: a list of
#: valorant-api.com's), and the fixtures that are made anew (made_up_record).
SWAP: dict[str, str] = {}
FOUND: dict = {"agents": {}, "skins": {}, "weapons": {}, "maps": []}
MADE: dict[str, bytes] = {}


def real(made_up: str) -> str:
    """The real id a made-up one stands for (--real), else itself."""
    return SWAP.get(made_up, made_up)


def real_ids(fetch) -> None:
    """Fills SWAP and FOUND from valorant-api.com (``fetch``: net.fetch): each made-up agent, skin (with its levels
    and variants), buddy, card, spray, title, bundle, act and contract of the fixtures gets a real one of its kind."""

    def ask(kind: str) -> list:
        status, body = fetch(f"https://valorant-api.com/v1/{kind}{'&' if '?' in kind else '?'}language=en-US", {})
        try:
            data = json.loads(body)["data"]
        except (ValueError, KeyError, TypeError):
            data = None
        if status != 200 or not isinstance(data, list):
            raise SystemExit(f"--real: valorant-api.com did not answer {kind} (HTTP {status})")
        return data

    made = json.loads(helpers.fixture("content_collection.json"))
    agents = FOUND["agents"] = {item["displayName"]: item["uuid"] for item in ask("agents?isPlayableCharacter=true")}
    SWAP[JETT], SWAP[SOVA] = agents["Jett"], agents["Sova"]
    arsenal = ask("weapons")
    FOUND["weapons"] = {weapon["displayName"]: weapon["uuid"] for weapon in arsenal}
    skins = FOUND["skins"] = {skin["displayName"]: skin for weapon in arsenal for skin in weapon["skins"]}
    # the maps of five against five: they have sites, a minimap and named places
    maps = {item["displayName"]: item for item in ask("maps") if item.get("callouts") and item.get("displayIcon")}
    sited = {name: item for name, item in maps.items() if "Sites" in str(item.get("tacticalDescription"))} or maps
    FOUND["maps"] = [sited[name] for name in REAL_MAPS if name in sited][:7] or list(sited.values())[:7]
    if not FOUND["maps"]:
        raise SystemExit("--real: valorant-api.com names no map with a minimap and named places")
    for weapon in made["weapons"]:
        for skin in weapon["skins"]:
            true = skins.get(REAL_SKINS.get(skin["uuid"], ""))
            if not true:
                continue
            SWAP[skin["uuid"]] = true["uuid"]
            for part in ("levels", "chromas"):
                for one, other in zip(skin.get(part) or [], true.get(part) or [], strict=False):
                    SWAP[one["uuid"]] = other["uuid"]
            if str(skin.get("themeUuid") or "").startswith("a1") and true.get("themeUuid"):
                SWAP[skin["themeUuid"]] = true["themeUuid"]
    # an offer of the store and a buddy of the bundle that the fixtures leave unknown
    extra = skins.get(REAL_STORE[0]) or next(iter(skins.values()))
    SWAP["77777777-0000-4000-8000-000000000000"] = extra["levels"][0]["uuid"]

    def pair(kind: str, true_items: list, part: str = "") -> None:
        for one, other in zip(made[kind], true_items, strict=False):
            SWAP[one["uuid"]] = other["uuid"]
            for sub, true_sub in zip(one.get(part) or [], other.get(part) or [], strict=False):
                SWAP[sub["uuid"]] = true_sub["uuid"]

    buddies = [item for item in ask("buddies") if item.get("displayIcon") and item.get("levels")]
    pair("buddies", buddies, "levels")
    if len(buddies) > 2:
        SWAP["bb000000-0000-4000-8000-000000000099"] = buddies[2]["levels"][0]["uuid"]
    cards = [item for item in ask("playercards") if item.get("smallArt") and item.get("wideArt")]
    pair("playercards", cards)
    if len(cards) > 2:
        SWAP["33333333-3333-4333-8333-333333333333"] = cards[2]["uuid"]  # the made-up player's card (HenrikDev)
    pair("sprays", [item for item in ask("sprays") if item.get("displayIcon")], "levels")
    pair("playertitles", [item for item in ask("playertitles") if item.get("titleText")])
    pair("flex", [item for item in ask("flex") if item.get("displayIcon")])
    bundles = ask("bundles")
    pair("bundles", [item for item in bundles if item.get("displayName") == "Prime"] or bundles)
    # the act being played (else the latest), the one before it, and its episode
    acts = [item for item in ask("seasons") if "Act" in str(item.get("type")) and item.get("startTime")]
    acts.sort(key=lambda item: item["startTime"])
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    current = next((item for item in acts if item["startTime"] <= now < str(item.get("endTime"))), acts[-1])
    before = acts[max(0, acts.index(current) - 1)]
    SWAP["aaaa0000-0000-4000-8000-000000000003"] = SWAP["aaaaaaaa-1111-4222-8333-000000000000"] = current["uuid"]
    SWAP["aaaa0000-0000-4000-8000-000000000002"] = before["uuid"]
    if current.get("parentUuid"):
        SWAP["aaaa0000-0000-4000-8000-000000000001"] = current["parentUuid"]
    contracts = ask("contracts")

    def contract(kind: str, relation: str = "") -> dict | None:
        of_kind = [item for item in contracts if (item.get("content") or {}).get("relationType") == kind]
        exact = [item for item in of_kind if item["content"].get("relationUuid") == relation]
        return (exact or of_kind or [None])[-1]

    wanted = (("Season", current["uuid"]), ("Season", before["uuid"]), ("Agent", agents["Jett"]), ("Event", ""))
    for number, (kind, relation) in enumerate(wanted, 1):
        true = contract(kind, relation)
        if true:
            SWAP[f"c0000000-0000-4000-8000-{number:012d}"] = true["uuid"]
    places = ", ".join(item["displayName"] for item in FOUND["maps"])
    print(f"--real: {len(SWAP)} made-up ids stand for real ones; the act: {current.get('displayName')}", flush=True)
    print(f"--real: the made-up matches are played on {places}", flush=True)


def _mine(pick) -> str:
    """--real: the agent the made-up player plays in a match."""
    known = [item for item in REAL_MINE if item[0] in FOUND["agents"]] or [(sorted(FOUND["agents"])[0], 1)]
    return pick.choices([name for name, _ in known], [often for _, often in known])[0]


def made_up_record(now: float) -> int:
    """--real: what HenrikDev answers about the made-up player (MADE): twelve whole matches of the last days
    (played.py) and the RR that each competitive one moved, ending at the rank of henrik_mmr.json. Returns the
    player's rating before the oldest of them (seed_lifetime goes on from there)."""
    pick = random.Random(11)
    current = json.loads(_fixture("henrik_mmr.json"))["data"]["current"]
    elo = current["elo"]
    matches, history = [], []
    for number, hours in enumerate((0.8, 1.7, 2.6, 21.5, 22.4, 23.3, 45.9, 46.8, 70.2, 71.1, 94.6, 119.3)):
        queue = "unrated" if number in (4, 9) else "competitive"
        victory = number == 0 or pick.random() < 0.55
        found = played.match(
            pick,
            f"aaaaaaaa-0000-4000-8000-{number + 1:012d}",
            now - hours * 3600,
            pick.choice(FOUND["maps"]),
            FOUND["agents"],
            FOUND["weapons"],
            won=victory,
            mine=_mine(pick),
            mate=pick.random() < 0.6,
            queue=queue,
        )
        matches.append(found)
        if queue != "competitive":
            continue
        change = current["last_change"] if number == 0 else pick.randint(16, 26) if victory else -pick.randint(12, 22)
        history.append(
            {
                "currenttier": elo // 100 + 3,
                "currenttierpatched": "",
                "images": {},
                "match_id": found["metadata"]["match_id"],
                "map": found["metadata"]["map"],
                "season_id": "s2",
                "ranking_in_tier": elo % 100,
                "mmr_change_to_last_game": change,
                "elo": elo,
                "date": "",
                "date_raw": int(now - hours * 3600),
            }
        )
        elo -= change
    MADE["henrik_matches.json"] = json.dumps({"status": 200, "data": matches}).encode()
    MADE["henrik_mmr_history.json"] = json.dumps(
        {"status": 200, "name": "Tester", "tag": "JP1", "data": history}
    ).encode()
    return elo


def _fixture(name: str, original=helpers.fixture) -> bytes:
    if name in MADE:
        return MADE[name]
    data = original(name)
    if name == "henrik_matches.json":
        longest, tag = LONGEST
        data = data.replace(b'"name": "Other"', f'"name": "{longest}"'.encode())
        data = data.replace(b'"tag": "EUW"', f'"tag": "{tag}"'.encode())
    if SWAP and not name.startswith("content_"):
        text = data.decode("utf-8")
        # the names that test the pages (the longest there can be, one with HTML in it) read as ordinary ones
        ordinary = (
            (LONGEST[0], "Hikari"),
            (LONGEST[1], "JP2"),
            ("<b>Foe</b>", "Kaze"),
            ("Teammate", played.MATE_NAME[0]),
        )
        for odd, plain in ordinary:
            text = text.replace(odd, plain)
        for made_up, true in SWAP.items():
            text = text.replace(made_up, true).replace(made_up.upper(), true.upper())
        data = text.encode("utf-8")
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

    def evening() -> float:
        """A start in the last 14 days: evenings busy, mornings quiet."""
        while True:
            hour = pick.random() * 24
            if pick.random() < 0.15 + 0.85 * (0.5 + 0.5 * math.cos((hour - 22) / 12 * math.pi)) ** 2:
                break
        started = now // 86400 * 86400 - pick.randint(0, 13) * 86400 + (hour - 9) * 3600
        return started - 86400 if started > now else started

    for cluster, size in SERVERS.items():
        for number in range(size):
            started = evening()
            tier = pick.randint(8, 24)
            listed.append(
                _match(f"{cluster}-{number}", started, cluster, "competitive", tier, pick if number < 60 else None)
            )
    activity.seen(listed)
    for number in range(60):
        start = evening() - 3600  # at any hour, as the rest: all at this hour of the day made it the busiest
        tier = pick.choice((10, 12, 17, 18, 22, 25))
        queue = "competitive" if number % 3 else "unrated"
        wait = int(pick.gauss(150 + tier * 6, 40))
        first = _match(f"pair-{number}-a", start, "Tokyo", queue, tier)
        second = _match(f"pair-{number}-b", start + 2100 + wait, "Tokyo", queue, tier)
        activity.seen([first, second], f"player-{number}")


def seed_lifetime(app, now: float, rating: int = 0) -> None:
    """The made-up player's lifetime (archive.py): 60 more of their matches over two years, 25 stored lines older
    than those (no rounds) and the RR of 80 competitive matches, so the lifetime page has pages and long charts.
    --real (``rating``: theirs before the latest matches, made_up_record): whole matches, the RR as they went."""
    from valtracker import henrik

    pick = random.Random(7)
    matches, changes, elo = [], {}, rating or 1650
    if rating:
        whole = []
        for number in range(60):
            started = now - (number + 1) * 11 * 86400 - pick.randint(0, 80000)
            queue = "unrated" if number % 6 == 5 else "competitive"
            found = played.match(
                pick,
                f"dddddddd-0000-4000-8000-{number:012d}",
                started,
                pick.choice(FOUND["maps"]),
                FOUND["agents"],
                FOUND["weapons"],
                won=pick.random() < 0.54,
                mine=_mine(pick),
                mate=pick.random() < 0.5,
                queue=queue,
            )
            whole.append(found)
            if queue == "competitive":  # newest first: the rating after each match, then the one before it
                change = pick.randint(16, 26) if played.is_win(found) else -pick.randint(12, 22)
                at = int(started)
                changes[found["metadata"]["match_id"]] = {
                    "change": change,
                    "tier": elo // 100 + 3,
                    "rr": elo % 100,
                    "elo": elo,
                    "at": at,
                }
                elo -= change
        matches = henrik._slim_matches(whole)
    else:
        base = henrik._slim_matches(json.loads(helpers.fixture("henrik_matches.json"))["data"])
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
        place = pick.choice(FOUND["maps"]) if rating else {}
        lines.append(
            {
                "id": f"eeeeeeee-0000-4000-8000-{number:012d}",
                "season": "e5a1",
                "started": datetime.fromtimestamp(started, UTC).isoformat().replace("+00:00", "Z"),
                "at": int(started),
                "queue": "competitive",
                "cluster": "Tokyo",
                "map": place.get("displayName", "Ascent"),
                "map_id": place.get("uuid", "7eaecc1b-4337-bbf6-6ab9-04b8f06b3319"),
                "us": 13,
                "them": pick.randint(3, 11) if number % 3 else 14,
                "agent": FOUND["agents"][_mine(pick)] if rating else real(JETT),
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
    other["Guns"][0] |= {"SkinID": real("aa000000-0000-4000-8000-000000000002"), "ChromaID": ""}
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
    # --real: ten agents as in a real match, and the owner has every agent to pick from
    agents = [FOUND["agents"][name] for name in REAL_LOBBY if name in FOUND["agents"]] or [real(JETT), real(SOVA)]
    if FOUND["agents"]:
        pd["entitlements"]["agents"] = sorted(FOUND["agents"].values())
    names = ("ながいなまえのプレイヤーさん", "Sakura", "xX_Hayate_Xx", "初心者です", "mochi", "Kitsune", "yuki")
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
                "CharacterID": agents[number % len(agents)],
                "PlayerIdentity": identity,
                "SeasonalBadgeInfo": {"Rank": tier},
            }
        )
        if number < 2:
            pd["pregame"]["AllyTeam"]["Players"].append(
                {
                    "Subject": subject,
                    "CharacterID": agents[number % len(agents)] if number else "",
                    "CharacterSelectionState": "selected" if number else "",
                    "CompetitiveTier": tier,
                    "PlayerIdentity": identity,
                }
            )


def last_match(app) -> None:
    """Riot's record of the match the owner is in on /live?phase=ingame: once it is "over" (/live?phase=menus, or
    the game closed), /live shows its result (owner._last_result). --real: a whole match (played.py), on the map
    and with the agent of the match that /live?phase=ingame shows."""
    pd = app.riot.pd
    match_id = pd["coregame"]["MatchID"].lower()
    if FOUND["maps"]:
        place = next((item for item in FOUND["maps"] if item["displayName"] == "Ascent"), FOUND["maps"][0])
        how = {"won": True, "mine": "Sova", "queue": "competitive"}
        found = played.match(
            random.Random(5), match_id, time.time() - 2400, place, FOUND["agents"], FOUND["weapons"], **how
        )
        record = played.to_pd(found, place, real("aaaaaaaa-1111-4222-8333-000000000000"), helpers.PUUID)
    else:
        record = json.dumps(pd["matches"]["bbbbbbbb-0000-4000-8000-000000000001"]).replace(pd["player"], helpers.PUUID)
        record = json.loads(record)
        record["matchInfo"] |= {"matchId": match_id, "gameStartMillis": int(time.time() - 2400) * 1000}
    pd["matches"][match_id] = record
    pd["player"] = helpers.PUUID
    pd["updates"]["Matches"][0]["MatchID"] = match_id


def four_offers(app) -> None:
    """--real: the day's store has four skins, as the game's (the fixture has two)."""
    panel = app.riot.pd["storefront"]["SkinsPanelLayout"]
    money = next(iter(panel["SingleItemStoreOffers"][0]["Cost"]))
    for name, price in zip(REAL_STORE[1:], (1775, 2175), strict=False):
        skin = FOUND["skins"].get(name)
        if not skin:
            continue
        level = skin["levels"][0]["uuid"]
        reward = {"ItemTypeID": "e7c63390-eda7-46e0-bb7a-a6abdacd2433", "ItemID": level, "Quantity": 1}
        offer = {"OfferID": level, "IsDirectPurchase": True, "Cost": {money: price}, "Rewards": [reward]}
        panel["SingleItemOffers"].append(level)
        panel["SingleItemStoreOffers"].append(offer)


def desktop_client(folder: Path):
    """The Riot Client of the PC app, made up: signed in, VALORANT open. Its events are not listened to, so where
    the game is comes from the made-up Riot servers (/live?phase=...), as on the site."""
    from test_desktop import FakeClient

    from valtracker.localclient import LocalClient

    lockfile = folder / "lockfile"
    lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
    return LocalClient(lockfile, folder / "ShooterGame.log", ask=FakeClient())


def made_up_friends(app, local, now: float) -> None:
    """The owner's friends in the made-up Riot Client (the PC app's /friends): a few in VALORANT in every place a
    presence can name (in both shapes the game has used), one in another game, the rest offline. The cards, maps
    and ranks are the content's own (the real ones with --real)."""
    import base64

    content = app.config["VAL_CONTENT"]
    cards = [item["uuid"] for item in content._list("playercards", "ja") if isinstance(item, dict) and item.get("uuid")]
    maps = sorted({str(item.get("mapUrl") or "") for item in content.map_index("ja").values()} - {""})
    tiers = sorted(tier for tier in content.tiers("ja") if tier > 2)
    client = local.ask
    mine = client.presences[0]
    party = "9a000000-0000-4000-8000-000000000001"  # the owner's (test_desktop.PARTY)
    other = "9b000000-0000-4000-8000-000000000002"

    def pick(items: list, index: int, default=""):
        return items[index % len(items)] if items else default

    playing = [
        # name, tag, the presence's fields, whether in the shape of before 2024
        ("Mochi", "JP1", {"sessionLoopState": "MENUS", "partyId": party, "partySize": 2, "maxPartySize": 5}, False),
        (
            "Kazehaya",
            "0715",
            {
                "sessionLoopState": "INGAME",
                "queueId": "competitive",
                "partyOwnerMatchScoreAllyTeam": 9,
                "partyOwnerMatchScoreEnemyTeam": 7,
                "partyId": other,
                "partySize": 3,
                "maxPartySize": 5,
            },
            False,
        ),
        (
            "ねこまる",
            "neko",
            {
                "sessionLoopState": "INGAME",
                "queueId": "competitive",
                "partyOwnerMatchScoreAllyTeam": 9,
                "partyOwnerMatchScoreEnemyTeam": 7,
                "partyId": other,
                "partySize": 3,
                "maxPartySize": 5,
            },
            True,
        ),
        ("Ramune", "soda", {"sessionLoopState": "PREGAME", "queueId": "unrated", "partySize": 1}, False),
        (
            "Shiro",
            "4649",
            {"sessionLoopState": "MENUS", "partyState": "MATCHMAKING", "queueId": "swiftplay", "partySize": 2},
            False,
        ),
        (
            "Tsubame",
            "sky",
            {"sessionLoopState": "MENUS", "partySize": 2, "maxPartySize": 5, "partyAccessibility": "OPEN"},
            False,
        ),
        ("Yuzu", "JP2", {"sessionLoopState": "INGAME", "provisioningFlow": "ShootingRange", "partySize": 1}, False),
        ("Hinata", "sun", {"sessionLoopState": "MENUS", "isIdle": True, "partySize": 1}, False),
    ]
    friends, presences = [], [mine]
    for index, (name, tag, fields, flat) in enumerate(playing):
        puuid = f"0d0d0d0d-1111-4222-8333-{index:012d}"
        fields = {
            "matchMap": pick(maps, index),
            "competitiveTier": pick(tiers, index * 3 + 9, 0),
            "accountLevel": 40 + index * 37,
            "playerCardId": pick(cards, index),
            **fields,
        }
        if flat:
            data = fields
        else:
            match = ("sessionLoopState", "queueId", "matchMap", "provisioningFlow")
            player = ("competitiveTier", "accountLevel", "playerCardId")
            data = {
                "isValid": True,
                "matchPresenceData": {key: fields[key] for key in match if key in fields},
                "playerPresenceData": {key: fields[key] for key in player if key in fields},
                "partyPresenceData": {
                    key: value for key, value in fields.items() if key.startswith(("party", "maxParty"))
                },
                "isIdle": bool(fields.get("isIdle")),
            }
            # the scores stay at the top, as the game writes them
            data |= {key: value for key, value in fields.items() if key.startswith("partyOwnerMatchScore")}
        friends.append(
            {"puuid": puuid, "game_name": name, "game_tag": tag, "note": "高校の友だち" if index == 4 else ""}
        )
        presences.append(
            {
                "puuid": puuid,
                "product": "valorant",
                "state": "away" if fields.get("isIdle") else "chat",
                "private": base64.b64encode(json.dumps(data).encode()).decode(),
            }
        )
    friends.append({"puuid": "0e0e0e0e-1111-4222-8333-000000000001", "game_name": "Lemon", "game_tag": "LoL"})
    presences.append({"puuid": friends[-1]["puuid"], "product": "league_of_legends", "state": "chat"})
    gone = ("Aoi#blue", "Sora#1203", "Kuro#9999", "Minato#port", "Rin#JP7", "Haru#0401", "Nagi#calm", "Tora#8888")
    for index, riot_id in enumerate(gone):
        name, _, tag = riot_id.partition("#")
        friends.append(
            {
                "puuid": f"0f0f0f0f-1111-4222-8333-{index:012d}",
                "game_name": name,
                "game_tag": tag,
                "last_online_ts": int((now - 3600 * (2 + index * index * 9)) * 1000),
            }
        )
    client.friends, client.presences = friends, presences


def main() -> None:
    flags = [arg for arg in sys.argv[1:] if arg.startswith("--")]
    numbers = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    port = int(numbers[0]) if numbers else 5077
    helpers.PNG = _png()
    helpers.fixture = _fixture
    rating = 0
    if "--real" in flags:
        from valtracker import net

        class RealPictures(helpers.FakeFetch):
            """The game's names and pictures from valorant-api.com itself; the rest made up as ever."""

            def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
                return net.fetch(url, headers) if url.startswith(REAL_SITES) else super().__call__(url, headers)

        helpers.FakeFetch = RealPictures
        real_ids(net.fetch)
        rating = made_up_record(time.time())
    local = None
    if "--desktop" in flags:
        client_folder = tempfile.TemporaryDirectory()
        local = desktop_client(Path(client_folder.name))
    app, fetch, _ = make_app(local=local)
    fetch.esports_page = True  # the matches to come from Riot's esports site, the results from HenrikDev
    if local is None:
        app.config["VAL_RIOT"].sign_in("ssid-1")
    seed(app.config["VAL_ACTIVITY"], time.time())
    seed_lifetime(app, time.time(), rating)
    # a few days of the owner's store, and a skin wanted that is in today's (diary.py)
    diary = app.config["VAL_DIARY"]
    for back in range(1, 4):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - back * 86400))
        diary.note(day, [{"uuid": real("ac000000-0000-4000-8000-000000000001"), "price": 3550}], None)
    diary.want(real("ac000000-0000-4000-8000-000000000001"), True)
    seed_sets(app)
    full_lobby(app)
    last_match(app)
    if local is not None:
        made_up_friends(app, local, time.time())
    if "--real" in flags:
        four_offers(app)
        if local is not None:  # the settings page as the built app's on its owner's PC, not this run's temporary folder
            app.config["VAL_BUILD"] = {"commit": "5c0ffee" + "0" * 33, "time": 0, "date": "2026-11-12 21:40"}
            app.context_processor(lambda: {"data_folder": r"C:\Users\Owner\AppData\Local\VAL Tracker"})
    inner = app.wsgi_app
    next_live: list[str] = []

    def wsgi(environ, start):
        if "visitor=1" in environ.get("HTTP_COOKIE", ""):
            environ["homepage.visitor"] = True
        # ?update=1: a newer PC app is out (desktop.look_for_updates found one)
        newer = {"commit": "f" * 40, "date": "2026-11-13 09:00", "url": release.PAGE, "ready": True}
        app.config["VAL_UPDATE"] = newer if "update=1" in environ.get("QUERY_STRING", "") else None
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
