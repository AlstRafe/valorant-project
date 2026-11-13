"""Whole made-up matches for the screenshots (serve.py --real): ten players, every round, every kill and every hit,
in HenrikDev v4's shape (``match``) and in the game's own (``to_pd``: Riot's match-details), so that each page
shows what a real match gives it.

Nobody's match: the names are made up and the numbers come from a seeded random.Random. Only the maps, agents and
weapons are the game's (valorant-api.com), and a kill is put near one of the map's named places.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: The made-up player whose record the pages show (henrik_account.json), and who they queue with.
ME, MATE = "puuid-me", "puuid-2"
MATE_NAME = ("Ren", "0001")
#: Made-up names for the other eight (ordinary words, nobody's Riot ID on purpose).
NAMES = (
    "Sakura",
    "mochi",
    "Kitsune",
    "yuki",
    "Hikari",
    "Kaze",
    "Sora",
    "Tsubasa",
    "nagi",
    "Rin",
    "Aoi",
    "kuro",
    "Shiro",
    "Mikan",
    "tanuki",
    "Hoshi",
    "Umi",
    "Nami",
    "Kumo",
    "Yuzu",
    "Matcha",
    "Tora",
    "Suzu",
    "ぽんず",
    "おにぎり",
    "たいやき",
    "ねこまる",
    "しらたま",
    "もみじ",
)
#: How a match ends (the winner's rounds, the loser's): close ones more often than one-sided ones.
SCORES = (
    (13, 11),
    (13, 10),
    (13, 10),
    (13, 9),
    (13, 9),
    (13, 8),
    (13, 7),
    (13, 7),
    (13, 6),
    (13, 5),
    (13, 3),
    (14, 12),
)
#: What is carried in a round: the weapon, how often, what the loadout with it costs.
BOUGHT = (
    ("Vandal", 40, 4400),
    ("Phantom", 26, 4400),
    ("Operator", 6, 5700),
    ("Guardian", 4, 3650),
    ("Spectre", 8, 2600),
    ("Bulldog", 4, 3050),
    ("Judge", 3, 2850),
    ("Odin", 2, 4200),
    ("Sheriff", 5, 1600),
    ("Marshal", 2, 1750),
)
PISTOLS = (("Classic", 45, 700), ("Ghost", 30, 900), ("Sheriff", 15, 800), ("Frenzy", 10, 850))
#: Who the made-up player's friend plays, and the endings as the game's own record names them.
MATE_AGENTS = ("Sova", "Omen", "Killjoy", "Skye")
PD_ENDINGS = {"Elimination": "Eliminated"}
POD = "aresriot.aws-rclusterprod-ape1-1.ap-gp-tokyo-1"


def _order(pick, won: int, lost: int, winner: str, loser: str) -> list[str]:
    """Who won each round, so that the match ends ``won``-``lost`` with its last round (overtime: 12-12 first)."""
    if won > 13:
        first = [winner] * 12 + [loser] * 12
        pick.shuffle(first)
        return first + [winner, winner]
    first = [winner] * (won - 1) + [loser] * lost
    pick.shuffle(first)
    return [*first, winner]


def _carried(pick, table: tuple, weapons: dict) -> tuple[str, int]:
    known = [item for item in table if item[0] in weapons] or [table[0]]
    name, _, cost = pick.choices(known, [item[1] for item in known])[0]
    return name, cost


def match(pick, match_id: str, started: float, place: dict, agents: dict, weapons: dict, **how) -> dict:
    """A made-up match on ``place`` (valorant-api.com's map) in HenrikDev v4's shape. ``agents`` and ``weapons``:
    the game's, name -> uuid. ``how``: won (the made-up player's team wins), mine (their agent), mate (their friend
    plays too), queue, tier (theirs; the others are near it)."""
    won, mine, queue = how.get("won", True), how.get("mine", "Jett"), how.get("queue", "competitive")
    tier, with_mate = how.get("tier", 21), how.get("mate", True)
    my_team, their_team = pick.sample(("Red", "Blue"), 2)
    people = [{"puuid": ME, "name": "Tester", "tag": "JP1", "party_id": "party-a", "agent": mine}]
    if with_mate:
        friend = pick.choice([name for name in MATE_AGENTS if name in agents and name != mine] or [mine])
        people.append(
            {"puuid": MATE, "name": MATE_NAME[0], "tag": MATE_NAME[1], "party_id": "party-a", "agent": friend}
        )
    for name in pick.sample(NAMES, 10 - len(people)):
        number = NAMES.index(name)
        tag = "JP1" if number % 3 == 0 else f"{1000 + number * 1373 % 9000}"
        people.append({"puuid": f"made-up-{number:02d}", "name": name, "tag": tag, "party_id": f"party-{number}"})
    for number, person in enumerate(people):
        person["team_id"] = my_team if number < 5 else their_team
        person["tier"] = tier if number == 0 else max(3, min(27, tier + pick.randint(-2, 2)))
        person["level"] = 120 if number == 0 else pick.randint(24, 420)
        person["weight"] = 1.15 if number == 0 else max(0.55, pick.gauss(1.0, 0.22))
        person["aim"] = 0.5 if number == 0 else pick.uniform(0.3, 0.65)  # how many of their kills are headshots
    for side in (people[:5], people[5:]):  # five different agents a team
        taken = {person["agent"] for person in side if "agent" in person}
        free = [name for name in sorted(agents) if name not in taken]
        pick.shuffle(free)
        for person in side:
            person.setdefault("agent", free.pop() if free else mine)
    if pick.random() < 0.5:
        people[6]["party_id"] = people[5]["party_id"]  # two of the others queue together

    def who(number: int) -> dict:
        person = people[number]
        return {"puuid": person["puuid"], "name": person["name"], "tag": person["tag"], "team": person["team_id"]}

    def weapon(name: str) -> dict:
        return {"id": weapons.get(name, ""), "name": name, "type": "Weapon"}

    spots = [
        (item["location"]["x"], item["location"]["y"])
        for item in place.get("callouts") or []
        if isinstance(item.get("location"), dict)
    ]
    sites = sorted({str(item.get("superRegionName")) for item in place.get("callouts") or []} & {"A", "B", "C"})

    def near(spot: tuple, spread: float = 260.0) -> dict:
        return {"x": round(spot[0] + pick.gauss(0, spread)), "y": round(spot[1] + pick.gauss(0, spread))}

    side = {team: [number for number in range(10) if people[number]["team_id"] == team] for team in ("Red", "Blue")}
    winner, loser = (my_team, their_team) if won else (their_team, my_team)
    order = _order(pick, *pick.choice(SCORES), winner, loser)
    total = {
        number: {"score": 0, "kills": 0, "deaths": 0, "assists": 0, "spent": 0, "value": 0} for number in range(10)
    }
    shots = {number: [0, 0, 0, 0, 0] for number in range(10)}  # head, body, leg, damage dealt, damage received
    rounds, kills = [], []
    for number, round_winner in enumerate(order):
        round_loser = "Blue" if round_winner == "Red" else "Red"
        attack = ("Red" if number < 12 else "Blue") if number < 24 else ("Red", "Blue")[number % 2]
        pistol = number in (0, 12)
        carried = {player: _carried(pick, PISTOLS if pistol else BOUGHT, weapons) for player in range(10)}
        planted = bool(sites) and pick.random() < (0.8 if round_winner == attack else 0.35)
        if round_winner == attack:
            ending = "Bomb detonated" if planted and pick.random() < 0.3 else "Elimination"
            left = pick.choice((1, 1, 2)) if ending == "Bomb detonated" else 0
        elif planted:
            ending, left = "Bomb defused", pick.choice((0, 0, 0, 1, 2))
        else:
            ending = "Round timer expired" if pick.random() < 0.06 else "Elimination"
            left = 0 if ending == "Elimination" else pick.choice((1, 2, 3))
        # who dies in which order: the losers but ``left`` of them, some of the winners, never the last winner
        dying = [round_loser] * (5 - left) + [round_winner] * pick.choices((0, 1, 2, 3, 4), (12, 25, 28, 22, 13))[0]
        pick.shuffle(dying)
        if not left:
            dying.remove(round_loser)
            dying.append(round_loser)
        alive = {team: list(members) for team, members in side.items()}
        hits: dict[int, list] = {player: [] for player in range(10)}  # [receiver, damage, head, body, leg]
        made = {player: [0, 0] for player in range(10)}  # kills, assists
        died_at: dict[int, int] = {}
        alone_against = 0  # how many the last winner alive still had to beat
        for when, team in zip(sorted(pick.randint(7000, 88000) for _ in dying), dying, strict=True):
            other = "Blue" if team == "Red" else "Red"
            victim = pick.choices(alive[team], [1 / people[player]["weight"] for player in alive[team]])[0]
            killer = pick.choices(alive[other], [people[player]["weight"] for player in alive[other]])[0]
            alive[team].remove(victim)
            died_at[victim] = when
            if team == round_winner and len(alive[team]) == 1:
                alone_against = len(alive[other])
            head = pick.random() < people[killer]["aim"]
            hits[killer].append(
                [victim, pick.randint(108, 150), pick.randint(1, 2), pick.randint(0, 1), 0]
                if head
                else [victim, pick.randint(108, 150), 0, pick.randint(2, 3), int(pick.random() < 0.2)]
            )
            made[killer][0] += 1
            helpers = []
            mates = [player for player in side[other] if player != killer]
            if pick.random() < 0.45:
                helper = pick.choice(mates)
                helpers.append(helper)
                made[helper][1] += 1
                hits[helper].append([victim, pick.randint(18, 60), 0, 1, 0])
            spot = pick.choice(spots) if spots else None
            stood = sorted(spots, key=lambda item, spot=spot: (item[0] - spot[0]) ** 2 + (item[1] - spot[1]) ** 2)[1:4]
            kills.append(
                {
                    "time_in_round_in_ms": when,
                    "time_in_match_in_ms": number * 100_000 + when,
                    "round": number,
                    "killer": who(killer),
                    "victim": who(victim),
                    "assistants": [who(helper) for helper in helpers],
                    "location": near(spot) if spot else {"x": 0, "y": 0},
                    "weapon": weapon(carried[killer][0]),
                    "secondary_fire_mode": False,
                    "player_locations": [
                        {"player": who(killer), "view_radians": 0.0, "location": near(pick.choice(stood), 180.0)}
                    ]
                    if stood
                    else [],
                }
            )
        for _ in range(pick.randint(2, 5)):  # shots that hit and killed nobody
            shooter = pick.randrange(10)
            hurt = pick.choice(side["Blue" if people[shooter]["team_id"] == "Red" else "Red"])
            hits[shooter].append([hurt, pick.randint(14, 90), int(pick.random() < 0.25), 1, 0])
        plant = defuse = None
        if planted:
            planter = pick.choice(side[attack])
            wanted = min(pick.randint(18000, 52000), died_at.get(planter, 99000) - 1500)
            plant = {
                "round_time_in_ms": max(4000, wanted),
                "location": {"x": 0, "y": 0},
                "player": who(planter),
                "player_locations": [],
                "site": pick.choice(sites),
            }
        if ending == "Bomb defused":
            defuser = pick.choice(alive[round_winner])
            defuse = {
                "round_time_in_ms": 96000,
                "location": {"x": 0, "y": 0},
                "player": who(defuser),
                "player_locations": [],
            }
        if max(made[player][0] for player in side[round_winner]) == 5:
            ceremony = "CeremonyAce"
        elif len(alive[round_winner]) == 5 and not left:
            ceremony = "CeremonyFlawless"
        elif len(alive[round_winner]) == 1 and alone_against >= 2 and not alive[round_loser]:
            ceremony = "CeremonyClutch"
        else:
            ceremony = "CeremonyDefault"
        lines = []
        for player in range(10):
            dealt = sum(hit[1] for hit in hits[player])
            score = dealt + 110 * made[player][0] + 25 * made[player][1]
            value = carried[player][1] + (0 if pistol else pick.randint(2, 14) * 100)
            line = total[player]
            line["score"] += score
            line["kills"] += made[player][0]
            line["assists"] += made[player][1]
            line["deaths"] += player in died_at
            line["spent"] += round(value * 0.8)
            line["value"] += value
            for hurt, damage, head, body, leg in hits[player]:
                shots[player][0] += head
                shots[player][1] += body
                shots[player][2] += leg
                shots[player][3] += damage
                shots[hurt][4] += damage
            lines.append(
                {
                    "player": who(player),
                    "ability_casts": {"grenade": 0, "ability_1": 0, "ability_2": 0, "ultimate": 0},
                    "damage_events": [
                        {"player": who(hurt), "damage": damage, "headshots": head, "bodyshots": body, "legshots": leg}
                        for hurt, damage, head, body, leg in hits[player]
                    ],
                    "stats": {
                        "score": score,
                        "kills": made[player][0],
                        "headshots": sum(hit[2] for hit in hits[player]),
                        "bodyshots": sum(hit[3] for hit in hits[player]),
                        "legshots": sum(hit[4] for hit in hits[player]),
                    },
                    "economy": {
                        "loadout_value": value,
                        "remaining": pick.randint(0, 30) * 100,
                        "weapon": weapon(carried[player][0]),
                        "armor": None,
                    },
                    "was_afk": False,
                    "received_penalty": False,
                    "stayed_in_spawn": False,
                }
            )
        rounds.append(
            {
                "id": number,
                "result": ending,
                "ceremony": ceremony,
                "winning_team": round_winner,
                "plant": plant,
                "defuse": defuse,
                "stats": lines,
            }
        )
    played = len(order)
    players = []
    for number, person in enumerate(people):
        line = total[number]
        players.append(
            {
                "puuid": person["puuid"],
                "name": person["name"],
                "tag": person["tag"],
                "team_id": person["team_id"],
                "platform": "pc",
                "party_id": person["party_id"],
                "agent": {"id": agents.get(person["agent"], ""), "name": person["agent"]},
                "stats": {
                    "score": line["score"],
                    "kills": line["kills"],
                    "deaths": line["deaths"],
                    "assists": line["assists"],
                    "headshots": shots[number][0],
                    "bodyshots": shots[number][1],
                    "legshots": shots[number][2],
                    "damage": {"dealt": shots[number][3], "received": shots[number][4]},
                },
                "tier": {"id": person["tier"], "name": ""},
                "account_level": person["level"],
                "ability_casts": {"grenade": 0, "ability_1": 0, "ability_2": 0, "ultimate": 0},
                "economy": {
                    "spent": {"overall": line["spent"], "average": line["spent"] // played},
                    "loadout_value": {"overall": line["value"], "average": line["value"] // played},
                },
            }
        )
    teams = []
    for team in ("Red", "Blue"):
        rounds_won = order.count(team)
        score = {"won": rounds_won, "lost": played - rounds_won}
        teams.append({"team_id": team, "rounds": score, "won": team == winner, "premier_roster": None})
    return {
        "metadata": {
            "match_id": match_id,
            "map": {"id": str(place.get("uuid") or ""), "name": str(place.get("displayName") or "")},
            "game_version": "made-up",
            "game_length_in_ms": played * 98_000 + 150_000,
            "started_at": datetime.fromtimestamp(started, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "is_completed": True,
            "queue": {"id": queue, "name": queue.title(), "mode_type": "Standard"},
            "season": {"id": "s", "short": "e10a1"},
            "platform": "pc",
            "region": "ap",
            "cluster": "Tokyo",
        },
        "players": players,
        "observers": [],
        "coaches": [],
        "teams": teams,
        "rounds": rounds,
        "kills": kills,
    }


def is_win(found: dict) -> bool:
    """Whether the made-up player's team won ``found`` (a match of ``match``)."""
    team = next(player["team_id"] for player in found["players"] if player["puuid"] == ME)
    return any(side["team_id"] == team and side["won"] for side in found["teams"])


def to_pd(found: dict, place: dict, season: str, subject: str) -> dict:
    """``found`` (a match of ``match``) as the game's own record of it (Riot's match-details, what pdmatch.to_v4
    reads), the made-up player being ``subject`` (the owner signed in)."""

    def named(puuid: str) -> str:
        return subject if puuid == ME else puuid

    metadata = found["metadata"]
    start = int(datetime.fromisoformat(metadata["started_at"].replace("Z", "+00:00")).timestamp() * 1000)
    played = len(found["rounds"])
    players = [
        {
            "subject": named(player["puuid"]),
            "gameName": player["name"],
            "tagLine": player["tag"],
            "teamId": player["team_id"],
            "partyId": player["party_id"],
            "characterId": player["agent"]["id"],
            "stats": {
                "score": player["stats"]["score"],
                "roundsPlayed": played,
                "kills": player["stats"]["kills"],
                "deaths": player["stats"]["deaths"],
                "assists": player["stats"]["assists"],
                "playtimeMillis": metadata["game_length_in_ms"],
            },
            "competitiveTier": player["tier"]["id"],
            "isObserver": False,
            "playerCard": "",
            "accountLevel": player["account_level"],
        }
        for player in found["players"]
    ]
    results = []
    for item in found["rounds"]:
        lines = []
        for line in item["stats"]:
            puuid = line["player"]["puuid"]
            lines.append(
                {
                    "subject": named(puuid),
                    "kills": [
                        {
                            "gameTime": kill["time_in_match_in_ms"],
                            "roundTime": kill["time_in_round_in_ms"],
                            "killer": named(kill["killer"]["puuid"]),
                            "victim": named(kill["victim"]["puuid"]),
                            "victimLocation": kill["location"],
                            "assistants": [named(helper["puuid"]) for helper in kill["assistants"]],
                            "playerLocations": [
                                {
                                    "subject": named(spot["player"]["puuid"]),
                                    "viewRadians": spot["view_radians"],
                                    "location": spot["location"],
                                }
                                for spot in kill["player_locations"]
                            ],
                            "finishingDamage": {
                                "damageType": "Weapon",
                                "damageItem": kill["weapon"]["id"].upper(),
                                "isSecondaryFireMode": False,
                            },
                        }
                        for kill in found["kills"]
                        if kill["round"] == item["id"] and kill["killer"]["puuid"] == puuid
                    ],
                    "damage": [
                        {
                            "receiver": named(hit["player"]["puuid"]),
                            "damage": hit["damage"],
                            "headshots": hit["headshots"],
                            "bodyshots": hit["bodyshots"],
                            "legshots": hit["legshots"],
                        }
                        for hit in line["damage_events"]
                    ],
                    "score": line["stats"]["score"],
                    "economy": {
                        "loadoutValue": line["economy"]["loadout_value"],
                        "weapon": line["economy"]["weapon"]["id"].upper(),
                        "armor": "",
                        "remaining": line["economy"]["remaining"],
                        "spent": round(line["economy"]["loadout_value"] * 0.8),
                    },
                }
            )
        results.append(
            {
                "roundNum": item["id"],
                "roundResult": PD_ENDINGS.get(item["result"], item["result"]),
                "roundCeremony": item["ceremony"],
                "winningTeam": item["winning_team"],
                "bombPlanter": named(item["plant"]["player"]["puuid"]) if item["plant"] else None,
                "bombDefuser": named(item["defuse"]["player"]["puuid"]) if item["defuse"] else None,
                "plantSite": item["plant"]["site"] if item["plant"] else "",
                "playerStats": lines,
            }
        )
    return {
        "matchInfo": {
            "matchId": metadata["match_id"],
            "mapId": str(place.get("mapUrl") or ""),
            "gamePodId": POD,
            "gameVersion": "made-up",
            "gameLengthMillis": metadata["game_length_in_ms"],
            "gameStartMillis": start,
            "provisioningFlowID": "Matchmaking",
            "isCompleted": True,
            "queueID": metadata["queue"]["id"],
            "isRanked": metadata["queue"]["id"] == "competitive",
            "seasonId": season,
            "completionState": "Completed",
        },
        "players": players,
        "teams": [
            {
                "teamId": team["team_id"],
                "won": team["won"],
                "roundsPlayed": played,
                "roundsWon": team["rounds"]["won"],
                "numPoints": team["rounds"]["won"],
            }
            for team in found["teams"]
        ],
        "roundResults": results,
    }
