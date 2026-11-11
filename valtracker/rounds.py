"""What a match's rounds and kills tell (HenrikDev v4 ``rounds`` and ``kills``), worked out once when the match is
first read, so the rest of them (about 0.5 MB a match) can be dropped before it is kept (henrik.py).

For each player: first kills and deaths, KAST rounds (a kill, an assist, survived or traded), multi-kills, clutches
won, plants and defuses, kills by weapon. For each round: who won and how, which team attacked, the plant site, each
team's loadout, and its kills in order (compact: a player is their place in the match's ``players``), with where
the victim died and where the killer stood (heatmap.py draws them).

HenrikDev's older answers (v2) named these differently; both are read, and whatever is missing is left out.
"""

from __future__ import annotations

#: A death is traded when a team-mate kills the killer within this long.
TRADE_MS = 5000
#: How a round ended -> the short name the pages use.
ENDINGS = {
    "elimination": "elim",
    "eliminated": "elim",
    "bomb defused": "defuse",
    "bomb detonated": "detonate",
    "round timer expired": "time",
    "surrendered": "surrender",
}
#: Rounds in a half for the modes that swap sides (the red team attacks first); overtime swaps every round.
HALF = {"competitive": 12, "unrated": 12, "premier": 12, "custom": 12, "swiftplay": 4, "spikerush": 3}
#: One player's numbers before the first round. mk: rounds with 2, 3, 4 and 5 kills (an ace).
EMPTY = {"fk": 0, "fd": 0, "kast": 0, "rounds": 0, "clutches": 0, "clutch_best": 0, "plants": 0, "defuses": 0}
#: A list of kills longer than this is not kept (Deathmatch: hundreds of kills, no rounds to show them in).
MAX_KILLS = 400


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _puuid(who) -> str:
    if isinstance(who, dict):
        return str(who.get("puuid") or who.get("player_puuid") or who.get("assistant_puuid") or "").lower()
    return str(who or "").lower()


def _kill(raw: dict, place: dict) -> list | None:
    """[round, ms into the round, killer, victim, weapon key, [assistants], weapon name, where the victim died,
    where the killer stood] (players by place, -1: nobody's; places as _spot gives them)."""
    victim = place.get(_puuid(raw.get("victim") or raw.get("victim_puuid")))
    if victim is None:
        return None
    killer = place.get(_puuid(raw.get("killer") or raw.get("killer_puuid")), -1)
    weapon = raw.get("weapon") if isinstance(raw.get("weapon"), dict) else {}
    key = str(weapon.get("id") or raw.get("damage_weapon_id") or "").lower()
    name = str(weapon.get("name") or raw.get("damage_weapon_name") or "")
    helpers = []
    for helper in raw.get("assistants") or []:
        found = place.get(_puuid(helper))
        if found is not None and found != killer:
            helpers.append(found)
    moment = raw.get("time_in_round_in_ms", raw.get("kill_time_in_round"))
    victim_at = _spot(raw.get("location") if "location" in raw else raw.get("victim_death_location"))
    killer_at = None
    killer_id = _puuid(raw.get("killer") or raw.get("killer_puuid"))
    for item in raw.get("player_locations") or raw.get("player_locations_on_kill") or []:
        if isinstance(item, dict) and killer_id and _puuid(item.get("player") or item.get("player_puuid")) == killer_id:
            killer_at = _spot(item.get("location"))
            break
    return [
        _int(raw.get("round", raw.get("round_number"))),
        _int(moment),
        killer,
        victim,
        key or name,
        helpers,
        name,
        victim_at,
        killer_at,
    ]


def _spot(where) -> list | None:
    """A place in the game's units, [x, y]; None when not known (HenrikDev gives 0, 0 for that)."""
    if not isinstance(where, dict):
        return None
    x, y = where.get("x"), where.get("y")
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int | float) or not isinstance(y, int | float):
        return None
    return None if x == 0 and y == 0 else [round(x), round(y)]


def _event(raw: dict, *names: str) -> dict:
    for name in names:
        if isinstance(raw.get(name), dict):
            return raw[name]
    return {}


def _round(raw: dict, place: dict, team: list) -> dict:
    plant = _event(raw, "plant", "plant_events")
    defuse = _event(raw, "defuse", "defuse_events")
    if raw.get("bomb_planted") is False:
        plant = {}
    if raw.get("bomb_defused") is False:
        defuse = {}
    planter = place.get(_puuid(plant.get("player") or plant.get("planted_by")), -1) if plant else -1
    defuser = place.get(_puuid(defuse.get("player") or defuse.get("defused_by")), -1) if defuse else -1
    loadout: dict[str, int] = {}
    for line in raw.get("stats") or raw.get("player_stats") or []:
        if not isinstance(line, dict):
            continue
        who = place.get(_puuid(line.get("player") or line.get("player_puuid")))
        economy = line.get("economy") if isinstance(line.get("economy"), dict) else {}
        if who is not None and team[who]:
            loadout[team[who]] = loadout.get(team[who], 0) + _int(economy.get("loadout_value"))
    ceremony = str(raw.get("ceremony") or raw.get("round_ceremony") or "").removeprefix("Ceremony").lower()
    return {
        "w": str(raw.get("winning_team") or ""),
        "e": ENDINGS.get(str(raw.get("result") or raw.get("end_type") or "").strip().lower(), ""),
        "c": "" if ceremony in ("", "default") else ceremony,
        "s": str(plant.get("site") or plant.get("plant_site") or ""),
        "p": planter,
        "d": defuser,
        "a": "",
        "l": loadout,
    }


def _sides(rounds: list, team: list, queue: str) -> None:
    """Which team attacked each round: the red team first, the halves of the mode; a plant is always the attackers',
    so the guess is turned round when the plants say otherwise. Without a known half, the plants alone."""
    teams = sorted({name for name in team if name})
    half = HALF.get(queue)
    if half and len(teams) == 2:
        first = "Red" if "Red" in teams else teams[0]
        other = teams[1] if first == teams[0] else teams[0]
        for number, item in enumerate(rounds):
            if number < 2 * half:
                item["a"] = first if number < half else other
            else:
                item["a"] = first if (number - 2 * half) % 2 == 0 else other
        planted = [(item["a"], team[item["p"]]) for item in rounds if item["p"] >= 0]
        if sum(guess != planter for guess, planter in planted) > sum(guess == planter for guess, planter in planted):
            for item in rounds:
                item["a"] = other if item["a"] == first else first
        return
    for item in rounds:
        item["a"] = team[item["p"]] if item["p"] >= 0 else ""


def digest(match: dict) -> dict:
    """What the pages use of the rounds and kills (see the module's words); {} when the match has none."""
    players = [player for player in match.get("players") or [] if isinstance(player, dict)]
    ids = [_puuid(player) for player in players]
    place = {puuid: number for number, puuid in enumerate(ids) if puuid}
    team = [str(player.get("team_id") or "") for player in players]
    metadata = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
    queue = (
        str((metadata.get("queue") or {}).get("id") or "").lower() if isinstance(metadata.get("queue"), dict) else ""
    )
    raw_rounds = [item for item in match.get("rounds") or [] if isinstance(item, dict)]
    rounds = [_round(item, place, team) for item in raw_rounds]
    rounds = rounds if any(item["w"] for item in rounds) else []
    kills = [
        found for item in match.get("kills") or [] if isinstance(item, dict) for found in [_kill(item, place)] if found
    ]
    kills.sort(key=lambda kill: (kill[0], kill[1]))
    if not rounds and not kills:
        return {}
    _sides(rounds, team, queue)
    weapons = {kill[4]: kill[6] for kill in kills if kill[4] and kill[6]}
    each = [dict(EMPTY, mk=[0, 0, 0, 0], weapons={}) for _ in players]
    for kill in kills:
        killer, victim = kill[2], kill[3]
        if killer >= 0 and team[killer] != team[victim] and kill[4]:
            counted = each[killer]["weapons"]
            counted[kill[4]] = counted.get(kill[4], 0) + 1
    by_round: dict[int, list] = {}
    for kill in kills:
        by_round.setdefault(kill[0], []).append(kill)
    for number, item in enumerate(rounds):
        happened = by_round.get(number, [])
        _round_players(item, happened, team, each)
        if item["p"] >= 0:
            each[item["p"]]["plants"] += 1
        if item["d"] >= 0:
            each[item["d"]]["defuses"] += 1
    sides: dict[str, dict] = {}
    for item in rounds:
        if not item["a"]:
            continue
        for name in {name for name in team if name}:
            side = sides.setdefault(name, {"atk": [0, 0], "def": [0, 0]})["atk" if item["a"] == name else "def"]
            side[1] += 1
            side[0] += 1 if item["w"] == name else 0
    # [round, ms, killer, victim, weapon, [assistants], where the victim died, where the killer stood]
    compact = [kill[:6] + kill[7:9] for kill in kills] if rounds and len(kills) <= MAX_KILLS else []
    return {"rounds": rounds, "kills": compact, "weapons": weapons, "players": each, "sides": sides}


def _round_players(item: dict, happened: list, team: list, each: list) -> None:
    """One round's first kill and death, KAST, multi-kills and clutch, added to each player's."""
    enemies = [kill for kill in happened if kill[2] >= 0 and team[kill[2]] != team[kill[3]]]
    if enemies:
        each[enemies[0][2]]["fk"] += 1
        each[enemies[0][3]]["fd"] += 1
    killed: dict[int, int] = {}
    for kill in enemies:
        killed[kill[2]] = killed.get(kill[2], 0) + 1
    for player, count in killed.items():
        if count >= 2:
            each[player]["mk"][min(count, 5) - 2] += 1
    dead = {kill[3]: kill for kill in happened}
    helped = {helper for kill in enemies for helper in kill[5]}
    for player in range(len(each)):
        if not team[player]:
            continue
        each[player]["rounds"] += 1
        death = dead.get(player)
        traded = bool(death) and any(
            other[3] == death[2]
            and other[2] >= 0
            and team[other[2]] == team[player]
            and 0 <= other[1] - death[1] <= TRADE_MS
            for other in enemies
        )
        if player in killed or player in helped or death is None or traded:
            each[player]["kast"] += 1
    # a clutch: the last one alive of a team, against at least one, and the team won the round (a spike they
    # planted before falling counts too)
    alive = {name: {player for player in range(len(team)) if team[player] == name} for name in set(team) if name}
    last: dict[str, tuple[int, int]] = {}
    for kill in happened:
        for members in alive.values():
            members.discard(kill[3])
        for name, members in alive.items():
            others = sum(len(rest) for other, rest in alive.items() if other != name)
            if len(members) == 1 and others and name not in last:
                last[name] = (next(iter(members)), others)
    for name, (player, against) in last.items():
        if item["w"] == name:
            each[player]["clutches"] += 1
            each[player]["clutch_best"] = max(each[player]["clutch_best"], against)


#: The game's units are centimetres: a kill's distance in metres is the floor distance between the two (x, y) / 100.
METRE = 100


def extras(match: dict, me: int) -> dict:
    """What one player's rounds say beyond the digest's sums, read from its compact rounds and kills (so a match kept
    before these were counted has them too): ``{"flawless": rounds the team won without losing anyone, "clutch":
    {against: [won, tried]} (the moment the player was the last of their team, against 1-5), "far": {weapon key:
    [metres, kills, longest]} (the player's kills with both places known)}``; {} without rounds and kills."""
    digest = match.get("digest") if isinstance(match.get("digest"), dict) else {}
    played, kills = digest.get("rounds") or [], digest.get("kills") or []
    players = [player for player in match.get("players") or [] if isinstance(player, dict)]
    if not played or not kills or not 0 <= me < len(players):
        return {}
    team = [str(player.get("team_id") or "") for player in players]
    mine = team[me]
    if not mine:
        return {}
    by_round: dict[int, list] = {}
    for kill in kills:
        if isinstance(kill, list) and len(kill) >= 4 and 0 <= _int(kill[3], -1) < len(team):
            by_round.setdefault(_int(kill[0]), []).append(kill)
    flawless, clutch, far = 0, {}, {}
    for number, item in enumerate(played):
        happened = by_round.get(number, [])
        won = isinstance(item, dict) and item.get("w") == mine
        if won and not any(team[kill[3]] == mine for kill in happened):
            flawless += 1
        alive = {name: {player for player in range(len(team)) if team[player] == name} for name in set(team) if name}
        for kill in happened:
            for members in alive.values():
                members.discard(kill[3])
            if alive.get(mine) == {me}:
                against = sum(len(rest) for other, rest in alive.items() if other != mine)
                if against:
                    seen = clutch.setdefault(min(against, 5), [0, 0])
                    seen[0] += 1 if won else 0
                    seen[1] += 1
                break
            if not alive.get(mine):
                break
    for kill in kills:
        if not isinstance(kill, list) or len(kill) < 8 or kill[2] != me or not kill[4]:
            continue
        if not 0 <= _int(kill[3], -1) < len(team) or team[kill[3]] == mine:
            continue
        victim_at, killer_at = kill[6], kill[7]
        if not (isinstance(victim_at, list) and isinstance(killer_at, list) and len(victim_at) == len(killer_at) == 2):
            continue
        metres = ((victim_at[0] - killer_at[0]) ** 2 + (victim_at[1] - killer_at[1]) ** 2) ** 0.5 / METRE
        seen = far.setdefault(str(kill[4]), [0.0, 0, 0.0])
        seen[0] += metres
        seen[1] += 1
        seen[2] = max(seen[2], metres)
    return {"flawless": flawless, "clutch": clutch, "far": far}
