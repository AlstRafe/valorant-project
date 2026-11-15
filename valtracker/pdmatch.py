"""The game's own match records (Riot's PD: ``match-history`` and ``match-details``) in HenrikDev v4's shape, so that
the rest of the site (henrik._slim_match, rounds.py, stats.py, heatmap.py) reads them as it reads HenrikDev's.

PD names every player of a match, even one who hides their name in the game. This site never shows a hidden name,
and PD does not say who hides theirs, so every name is left out here except the one of the player whose record is
being read (the visitor searched for them by that name).
"""

from __future__ import annotations

from datetime import UTC, datetime

from valtracker.util import lower_id, to_int

#: PD's queue ids -> the kind of match (HenrikDev's ``mode_type``): any other is a game of rounds ("Standard").
MODE_TYPES = {
    "deathmatch": "Deathmatch",
    "hurm": "TeamDeathmatch",
    "ggteam": "Escalation",
    "snowball": "Snowball",
    "skirmish_1v1": "Skirmish",
    "skirmish_2v2": "Skirmish",
}
#: The city in a game server's name (PD's ``gamePodId``: "aresriot.aws-...-1.ap-gp-tokyo-1") -> the name
#: HenrikDev gives it (views.CLUSTERS translates them); any other is written as it is.
CITIES = {
    "tokyo": "Tokyo",
    "hongkong": "Hong Kong",
    "singapore": "Singapore",
    "sydney": "Sydney",
    "mumbai": "Mumbai",
    "seoul": "Seoul",
    "frankfurt": "Frankfurt",
    "london": "London",
    "paris": "Paris",
    "stockholm": "Stockholm",
    "madrid": "Madrid",
    "warsaw": "Warsaw",
    "istanbul": "Istanbul",
    "bahrain": "Bahrain",
    "telaviv": "Tel Aviv",
    "ashburn": "N. Virginia",
    "virginia": "N. Virginia",
    "oregon": "Oregon",
    "norcal": "N. California",
    "california": "N. California",
    "texas": "Texas",
    "dallas": "Texas",
    "chicago": "Illinois",
    "illinois": "Illinois",
    "atlanta": "Georgia",
    "georgia": "Georgia",
    "mexicocity": "Mexico City",
    "mexico": "Mexico City",
    "miami": "Miami",
    "santiago": "Santiago",
    "saopaulo": "São Paulo",
}


def city(pod) -> str:
    """The city of a game server's name ("" when it has none)."""
    last = str(pod or "").rsplit(".", 1)[-1].lower()
    parts = [part for part in last.split("-") if part and not part.isdigit()]
    if len(parts) >= 3 and parts[1] == "gp":
        parts = parts[2:]  # "ap-gp-tokyo" -> "tokyo"
    name = "".join(parts)
    return CITIES.get(name, name.title() if name else "")


def history(answer) -> dict:
    """PD's match-history -> ``{"total", "matches": [{"id", "at" (unix seconds), "queue"}]}``, newest first."""
    answer = answer if isinstance(answer, dict) else {}
    found = [
        {
            "id": lower_id(item["MatchID"]),
            "at": to_int(item.get("GameStartTime")) // 1000,
            "queue": lower_id(item.get("QueueID")),
        }
        for item in answer.get("History") or []
        if isinstance(item, dict) and item.get("MatchID")
    ]
    return {"total": to_int(answer.get("Total"), len(found)), "matches": found}


def rr_updates(answer) -> dict:
    """PD's competitiveupdates -> henrik._slim_rr's shape: ``{match id: {"change", "tier", "rr", "elo", "at"}}``
    (a placement match, which moves no RR, has no tier before it and is left out)."""
    answer = answer if isinstance(answer, dict) else {}
    found = {}
    for item in answer.get("Matches") or []:
        if not isinstance(item, dict) or not item.get("MatchID"):
            continue
        tier = to_int(item.get("TierAfterUpdate"))
        if tier < 3 or not to_int(item.get("TierBeforeUpdate")):
            continue
        rr = to_int(item.get("RankedRatingAfterUpdate"))
        found[lower_id(item["MatchID"])] = {
            "change": to_int(item.get("RankedRatingEarned")),
            "tier": tier,
            "rr": rr,
            "elo": (tier - 3) * 100 + rr,
            "at": to_int(item.get("MatchStartTime")) // 1000,
        }
    return found


def to_v4(details, region: str, maps: dict, puuid: str = "", name: str = "", tag: str = "") -> dict:
    """PD's match-details -> a HenrikDev v4 match (what henrik._slim_match reads). ``maps``: valorant-api.com's
    maps by their address name ("ascent"; content.Content.map_index). Only ``puuid`` keeps a name: ``name``,
    ``tag``. {} for what is not a finished match."""
    if not isinstance(details, dict) or not isinstance(details.get("matchInfo"), dict):
        return {}
    info = details["matchInfo"]
    match_id = lower_id(info.get("matchId"))
    if not match_id:
        return {}
    queue = lower_id(info.get("queueID")) or "custom"
    address = str(info.get("mapId") or "").rsplit("/", 1)[-1].lower()
    known = maps.get(address) or {}
    start = to_int(info.get("gameStartMillis"))
    players_raw = [item for item in details.get("players") or [] if isinstance(item, dict) and item.get("subject")]
    players_raw = [item for item in players_raw if not item.get("isObserver")]
    rounds_raw = [item for item in details.get("roundResults") or [] if isinstance(item, dict)]
    shots, dealt, taken, spent = _from_rounds(rounds_raw)
    players = []
    for item in players_raw:
        subject = lower_id(item["subject"])
        line = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        head, body, leg = shots.get(subject, (0, 0, 0))
        mine = subject == lower_id(puuid)
        players.append(
            {
                "puuid": subject,
                "name": name if mine else "",
                "tag": tag if mine else "",
                "team_id": str(item.get("teamId") or ""),
                "party_id": lower_id(item.get("partyId")),
                "agent": {"id": lower_id(item.get("characterId")), "name": ""},
                "stats": {
                    "score": to_int(line.get("score")),
                    "kills": to_int(line.get("kills")),
                    "deaths": to_int(line.get("deaths")),
                    "assists": to_int(line.get("assists")),
                    "headshots": head,
                    "bodyshots": body,
                    "legshots": leg,
                    "damage": {"dealt": dealt.get(subject, 0), "received": taken.get(subject, 0)},
                },
                "tier": {"id": to_int(item.get("competitiveTier")), "name": ""},
                "account_level": to_int(item.get("accountLevel")),
                "economy": {"spent": {"overall": spent.get(subject, 0)}},
            }
        )
    teams = []
    for item in details.get("teams") or []:
        if not isinstance(item, dict) or not item.get("teamId"):
            continue
        won, played = to_int(item.get("roundsWon")), to_int(item.get("roundsPlayed"))
        teams.append(
            {
                "team_id": str(item["teamId"]),
                "rounds": {"won": won, "lost": max(0, played - won)},
                "won": bool(item.get("won")),
            }
        )
    if MODE_TYPES.get(queue) in ("Deathmatch", "Escalation", "Snowball"):
        teams = []  # a team of one each, or rounds that are not rounds: HenrikDev gives none either
    return {
        "metadata": {
            "match_id": match_id,
            "map": {"id": lower_id(known.get("uuid")), "name": str(known.get("displayName") or address.title())},
            "game_length_in_ms": to_int(info.get("gameLengthMillis")),
            "started_at": datetime.fromtimestamp(start / 1000, UTC).isoformat().replace("+00:00", "Z") if start else "",
            "is_completed": bool(info.get("isCompleted", True)),
            "queue": {"id": queue, "name": "", "mode_type": MODE_TYPES.get(queue, "Standard")},
            "season": {"id": lower_id(info.get("seasonId")), "short": ""},
            "region": region,
            "cluster": city(info.get("gamePodId")),
        },
        "players": players,
        "teams": teams,
        "rounds": [_round(item) for item in rounds_raw],
        "kills": _kills(details, rounds_raw),
    }


def _from_rounds(rounds_raw: list) -> tuple[dict, dict, dict, dict]:
    """Each player's shots that hit (head, body, leg), damage dealt and taken, and credits spent, over the rounds."""
    shots: dict[str, tuple[int, int, int]] = {}
    dealt: dict[str, int] = {}
    taken: dict[str, int] = {}
    spent: dict[str, int] = {}
    for item in rounds_raw:
        for line in item.get("playerStats") or []:
            if not isinstance(line, dict):
                continue
            subject = lower_id(line.get("subject"))
            economy = line.get("economy") if isinstance(line.get("economy"), dict) else {}
            spent[subject] = spent.get(subject, 0) + to_int(economy.get("spent"))
            for hit in line.get("damage") or []:
                if not isinstance(hit, dict):
                    continue
                head, body, leg = shots.get(subject, (0, 0, 0))
                shots[subject] = (
                    head + to_int(hit.get("headshots")),
                    body + to_int(hit.get("bodyshots")),
                    leg + to_int(hit.get("legshots")),
                )
                dealt[subject] = dealt.get(subject, 0) + to_int(hit.get("damage"))
                receiver = lower_id(hit.get("receiver"))
                taken[receiver] = taken.get(receiver, 0) + to_int(hit.get("damage"))
    return shots, dealt, taken, spent


def _round(item: dict) -> dict:
    planter, defuser = lower_id(item.get("bombPlanter")), lower_id(item.get("bombDefuser"))
    return {
        "winning_team": str(item.get("winningTeam") or ""),
        "result": str(item.get("roundResult") or ""),
        "ceremony": str(item.get("roundCeremony") or ""),
        "plant": {"site": str(item.get("plantSite") or ""), "player": {"puuid": planter}} if planter else None,
        "defuse": {"player": {"puuid": defuser}} if defuser else None,
        "stats": [
            {
                "player": {"puuid": lower_id(line.get("subject"))},
                "stats": _shots(line),
                "economy": {
                    "loadout_value": to_int((line.get("economy") or {}).get("loadoutValue"))
                    if isinstance(line.get("economy"), dict)
                    else 0
                },
            }
            for line in item.get("playerStats") or []
            if isinstance(line, dict)
        ],
    }


def _shots(line: dict) -> dict:
    """A player's hits of one round by where they landed (PD counts them per opponent hit)."""
    hit = [item for item in line.get("damage") or [] if isinstance(item, dict)]
    return {name: sum(to_int(item.get(name)) for item in hit) for name in ("headshots", "bodyshots", "legshots")}


def _kills(details: dict, rounds_raw: list) -> list:
    """Every kill (the match's own list, or each round's when it has none) in v4's shape."""
    raw = [item for item in details.get("kills") or [] if isinstance(item, dict)]
    if not raw:
        for number, item in enumerate(rounds_raw):
            for line in item.get("playerStats") or []:
                for kill in (line.get("kills") or []) if isinstance(line, dict) else []:
                    if isinstance(kill, dict):
                        raw.append(dict(kill, round=kill.get("round", number)))
    found = []
    for kill in raw:
        damage = kill.get("finishingDamage") if isinstance(kill.get("finishingDamage"), dict) else {}
        item = str(damage.get("damageItem") or "")
        found.append(
            {
                "round": to_int(kill.get("round")),
                "time_in_round_in_ms": to_int(kill.get("roundTime")),
                "killer": {"puuid": lower_id(kill.get("killer"))},
                "victim": {"puuid": lower_id(kill.get("victim"))},
                "assistants": [{"puuid": lower_id(helper)} for helper in kill.get("assistants") or []],
                # a weapon's uuid; an ability's slot ("Ultimate"), which valorant-api.com's arsenal does not know
                "weapon": {"id": item.lower(), "name": "" if damage.get("damageType") == "Weapon" else item},
                "location": kill.get("victimLocation"),
                "player_locations": [
                    {"player": {"puuid": lower_id(spot.get("subject"))}, "location": spot.get("location")}
                    for spot in kill.get("playerLocations") or []
                    if isinstance(spot, dict)
                ],
            }
        )
    return found
