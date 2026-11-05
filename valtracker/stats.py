"""Numbers worked out from HenrikDev's v4 matches: one player's line in a match, a scoreboard, a summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def rounds_played(match: dict) -> int:
    teams = [team for team in match.get("teams") or [] if isinstance(team, dict)]
    if teams:
        rounds = teams[0].get("rounds") or {}
        return _int(rounds.get("won")) + _int(rounds.get("lost"))
    return len(match.get("rounds") or [])


def started(match: dict) -> datetime | None:
    """When the match started, in Japan's time."""
    raw = str((match.get("metadata") or {}).get("started_at") or "")
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(JST)


def advanced(match: dict, player: dict) -> dict:
    """The player's numbers from the match's rounds and kills (``digest``, rounds.py), or {} without them."""
    digest = match.get("digest") or {}
    each = digest.get("players") or []
    players = [other for other in match.get("players") or [] if isinstance(other, dict)]
    for number, other in enumerate(players):
        if other is player or (player.get("puuid") and other.get("puuid") == player.get("puuid")):
            return each[number] if number < len(each) and isinstance(each[number], dict) else {}
    return {}


def line(player: dict, rounds: int, adv: dict | None = None) -> dict:
    """One player's numbers: K / D / A, ACS (score per round), ADR (damage per round), HS%, +/-, DDΔ (damage dealt
    less taken, per round), the economy rating (damage per 1000 credits spent); with ``adv`` (advanced) also KAST,
    first kills and deaths, rounds of 3 kills or more, and clutches."""
    stats = player.get("stats") or {}
    kills, deaths, assists = _int(stats.get("kills")), _int(stats.get("deaths")), _int(stats.get("assists"))
    shots = _int(stats.get("headshots")) + _int(stats.get("bodyshots")) + _int(stats.get("legshots"))
    damage = _int((stats.get("damage") or {}).get("dealt"))
    taken = _int((stats.get("damage") or {}).get("received"))
    spent = _int(((player.get("economy") or {}).get("spent") or {}).get("overall"))
    adv = adv or {}
    played = _int(adv.get("rounds"))
    return {
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "diff": kills - deaths,
        "kd": round(kills / deaths, 2) if deaths else float(kills),
        "acs": round(_int(stats.get("score")) / rounds) if rounds else 0,
        "adr": round(damage / rounds) if rounds else 0,
        "hs": round(100 * _int(stats.get("headshots")) / shots) if shots else 0,
        "shots": shots,
        "headshots": _int(stats.get("headshots")),
        "bodyshots": _int(stats.get("bodyshots")),
        "legshots": _int(stats.get("legshots")),
        "damage": damage,
        "taken": taken,
        "dd": round((damage - taken) / rounds) if rounds else 0,
        "econ": round(damage * 1000 / spent) if spent and rounds else None,
        "score": _int(stats.get("score")),
        "kast": round(100 * _int(adv.get("kast")) / played) if played else None,
        "fk": _int(adv.get("fk")) if adv else None,
        "fd": _int(adv.get("fd")) if adv else None,
        "mk": sum((adv.get("mk") or [0, 0, 0, 0])[1:]) if adv else None,
        "clutches": _int(adv.get("clutches")) if adv else None,
    }


def lobby(match: dict, player: dict, rounds: int) -> dict | None:
    """The other players' average line in the match (the yardstick of the player's own): ACS, ADR, HS%, K/D, KAST
    and first kills and deaths per round. None without rounds or others."""
    others = [other for other in match.get("players") or [] if isinstance(other, dict) and other is not player]
    others = [other for other in others if other.get("puuid") != player.get("puuid")]
    if not rounds or not others:
        return None
    lines = [line(other, rounds, advanced(match, other)) for other in others]
    found = {key: sum(item[key] for item in lines) / len(lines) for key in ("acs", "adr", "kills", "deaths")}
    shots = sum(item["shots"] for item in lines)
    found["hs"] = 100 * sum(item["headshots"] for item in lines) / shots if shots else 0
    kast = [item["kast"] for item in lines if item["kast"] is not None]
    found["kast"] = sum(kast) / len(kast) if kast else None
    if all(item["fk"] is not None for item in lines):
        found["fk"] = sum(item["fk"] for item in lines) / len(lines) / rounds
        found["fd"] = sum(item["fd"] for item in lines) / len(lines) / rounds
    return found


def mates(match: dict, player: dict) -> list[dict]:
    """Who queued with the player (the same party): ``[{"puuid", "name", "tag"}]``."""
    party = player.get("party_id")
    if not party:
        return []
    return [
        {
            "puuid": str(other.get("puuid") or ""),
            "name": str(other.get("name") or ""),
            "tag": str(other.get("tag") or ""),
        }
        for other in match.get("players") or []
        if isinstance(other, dict) and other.get("party_id") == party and other.get("puuid") != player.get("puuid")
    ]


def team_result(match: dict, team_id: str) -> dict:
    """``{"won": True/False/None (a draw or no teams), "us": rounds won, "them": rounds lost, "rounds": whether
    the mode has rounds}``. Team Deathmatch and Escalation have none: only who won."""
    for team in match.get("teams") or []:
        if isinstance(team, dict) and str(team.get("team_id")) == str(team_id):
            rounds = team.get("rounds") or {}
            us, them = _int(rounds.get("won")), _int(rounds.get("lost"))
            won = team.get("won")
            if not us and not them:
                return {"won": won if isinstance(won, bool) else None, "us": 0, "them": 0, "rounds": False}
            return {"won": None if us == them else bool(won), "us": us, "them": them, "rounds": True}
    return {"won": None, "us": 0, "them": 0, "rounds": False}


def placing(match: dict, player: dict) -> dict:
    """Where the player finished among everyone: ``{"place": 1.., "of": players, "mvp": "match" / "team" / ""}``.
    Places go by kills in a match without teams (Deathmatch), else by combat score; the MVPs (the game's
    badges) only in a match with rounds."""
    players = [other for other in match.get("players") or [] if isinstance(other, dict)]
    teams = bool(team_result(match, str(player.get("team_id") or ""))["rounds"])
    measure = "score" if teams else "kills"

    def value(other: dict) -> int:
        return _int((other.get("stats") or {}).get(measure))

    mine = value(player)
    place = 1 + sum(1 for other in players if value(other) > mine)
    mvp = ""
    if teams and players and mine > 0:
        if place == 1:
            mvp = "match"
        elif all(value(other) <= mine for other in players if other.get("team_id") == player.get("team_id")):
            mvp = "team"
    return {"place": place, "of": len(players), "mvp": mvp}


def find_player(match: dict, puuid: str = "", name: str = "", tag: str = "") -> dict | None:
    for player in match.get("players") or []:
        if not isinstance(player, dict):
            continue
        if puuid and player.get("puuid") == puuid:
            return player
        if (
            not puuid
            and str(player.get("name", "")).lower() == name.lower()
            and str(player.get("tag", "")).lower() == tag.lower()
        ):
            return player
    return None


#: The numbers set beside the lobby's: (key, higher is better).
COMPARED = (("acs", True), ("adr", True), ("kd", True), ("hs", True), ("kast", True), ("fk", True), ("fd", False))


def _rate(won: int, played: int) -> int | None:
    return round(100 * won / played) if played else None


def _per_agent(played: list[dict]) -> list[dict]:
    agents: dict[str, dict] = {}
    for row in played:
        key = row["agent_id"] or row["agent_name"]
        seen = agents.setdefault(
            key,
            {"id": row["agent_id"], "name": row["agent_name"], "icon": row.get("agent_icon"), "rows": []},
        )
        seen["rows"].append(row)
    table = []
    for seen in agents.values():
        rows = seen.pop("rows")
        table.append(seen | _totals(rows))
    return sorted(table, key=lambda agent: (-agent["games"], -agent["wins"]))


def _totals(rows: list[dict]) -> dict:
    """Games, wins and losses, win rate, K/D, ACS, ADR and HS% over some of the rows."""
    wins = sum(1 for row in rows if row["result"]["won"] is True)
    losses = sum(1 for row in rows if row["result"]["won"] is False)
    kills = sum(row["line"]["kills"] for row in rows)
    deaths = sum(row["line"]["deaths"] for row in rows)
    shots = sum(row["line"]["shots"] for row in rows)
    with_rounds = [row for row in rows if row.get("rounds")]
    return {
        "games": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, wins + losses) or 0,
        "kd": round(kills / deaths, 2) if deaths else float(kills),
        "acs": round(sum(row["line"]["acs"] for row in with_rounds) / len(with_rounds)) if with_rounds else None,
        "adr": round(sum(row["line"]["adr"] for row in with_rounds) / len(with_rounds)) if with_rounds else None,
        "hs": round(100 * sum(row["line"]["headshots"] for row in rows) / shots) if shots else 0,
    }


def _per_map(played: list[dict]) -> list[dict]:
    maps: dict[str, list] = {}
    for row in played:
        name = (row.get("map") or {}).get("name") or ""
        if name and row.get("rounds"):
            maps.setdefault(name, []).append(row)
    table = []
    for name, rows in maps.items():
        sides = _sides(rows)
        image = (rows[0].get("map") or {}).get("image")
        table.append({"name": name, "image": image, **_totals(rows), **sides})
    return sorted(table, key=lambda item: (-item["games"], -item["wins"], item["name"]))


def _sides(rows: list[dict]) -> dict:
    """Rounds won on attack and on defence: ``{"atk": [won, played], "def": [...], "atk_rate", "def_rate"}``."""
    found = {"atk": [0, 0], "def": [0, 0]}
    for row in rows:
        for side in ("atk", "def"):
            won, played = (row.get("side") or {}).get(side) or (0, 0)
            found[side][0] += won
            found[side][1] += played
    return found | {"atk_rate": _rate(*found["atk"]), "def_rate": _rate(*found["def"])}


def _versus_lobby(rows: list[dict]) -> list[dict]:
    """The player's averages beside their lobbies' (the other players of the same matches)."""
    rows = [row for row in rows if row.get("lobby")]
    if not rows:
        return []
    compared = []
    for key, higher in COMPARED:
        pairs = []
        for row in rows:
            mine, theirs = row["line"].get(key), row["lobby"].get(key)
            if key in ("fk", "fd") and mine is not None:
                mine = mine / row["rounds"]
            if mine is not None and theirs is not None:
                pairs.append((mine, theirs))
        if not pairs:
            continue
        mine = sum(pair[0] for pair in pairs) / len(pairs)
        theirs = sum(pair[1] for pair in pairs) / len(pairs)
        relative = (mine - theirs) / theirs if theirs else 0.0
        compared.append(
            {
                "key": key,
                "mine": mine,
                "lobby": theirs,
                "better": (relative > 0.05) if higher else (relative < -0.05),
                "worse": (relative < -0.05) if higher else (relative > 0.05),
                "score": relative if higher else -relative,  # how much better than the lobby (worse: below 0)
            }
        )
    return compared


def _mates(rows: list[dict]) -> list[dict]:
    """Who queued with the player most, and how those games went."""
    found: dict[str, dict] = {}
    for row in rows:
        for mate in row.get("mates") or []:
            seen = found.setdefault(mate["puuid"] or mate["name"], dict(mate, games=0, wins=0, losses=0))
            seen["games"] += 1
            seen["wins"] += 1 if row["result"]["won"] is True else 0
            seen["losses"] += 1 if row["result"]["won"] is False else 0
    listed = sorted(found.values(), key=lambda mate: (-mate["games"], -mate["wins"], mate["name"].lower()))
    return [dict(mate, win_rate=_rate(mate["wins"], mate["wins"] + mate["losses"])) for mate in listed[:6]]


def summary(rows: list[dict]) -> dict:
    """Over the matches shown (the standard games, when there are any): wins and losses, win rate, average K/D/A,
    ACS, ADR, HS%, KAST, DDΔ, first kills and deaths, multi-kills, clutches, the time played, where the shots
    landed, by agent, by map (and side), the weapons' kills, the player beside their lobbies, and their party."""
    played = [row for row in rows if row.get("line")]
    played = [row for row in played if row.get("standard", True)] or played
    if not played:
        return {}
    count = len(played)
    totals = _totals(played)
    kills = sum(row["line"]["kills"] for row in played)
    deaths = sum(row["line"]["deaths"] for row in played)
    with_rounds = [row for row in played if row.get("rounds")]
    detailed = [row for row in with_rounds if row.get("adv")]
    rounds = sum(row["rounds"] for row in with_rounds)
    heads = sum(row["line"]["headshots"] for row in played)
    bodies = sum(row["line"]["bodyshots"] for row in played)
    legs = sum(row["line"]["legshots"] for row in played)
    shots = heads + bodies + legs
    weapons: dict[str, int] = {}
    for row in played:
        for key, number in (row.get("weapons") or {}).items():
            weapons[key] = weapons.get(key, 0) + number
    weapon_kills = sum(weapons.values())
    multi = [sum((row["adv"].get("mk") or [0, 0, 0, 0])[index] for row in detailed) for index in range(4)]
    agents = _per_agent(played)
    return {
        "games": count,
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_rate": totals["win_rate"],
        "kills": round(kills / count, 1),
        "deaths": round(deaths / count, 1),
        "assists": round(sum(row["line"]["assists"] for row in played) / count, 1),
        "kd": totals["kd"],
        # ACS, ADR and DDΔ are per round: a mode without rounds has none
        "acs": totals["acs"],
        "adr": totals["adr"],
        "dd": round(sum(row["line"]["damage"] - row["line"]["taken"] for row in with_rounds) / rounds)
        if rounds
        else None,
        "hs": totals["hs"],
        "kast": (
            round(100 * sum(row["adv"]["kast"] for row in detailed) / sum(row["adv"]["rounds"] for row in detailed))
            if detailed and sum(row["adv"]["rounds"] for row in detailed)
            else None
        ),
        "fk": sum(row["adv"]["fk"] for row in detailed) if detailed else None,
        "fd": sum(row["adv"]["fd"] for row in detailed) if detailed else None,
        "multi": multi if detailed else None,  # rounds with 2, 3, 4 and 5 kills
        "clutches": sum(row["adv"]["clutches"] for row in detailed) if detailed else None,
        "clutch_best": max((row["adv"]["clutch_best"] for row in detailed), default=0),
        "plants": sum(row["adv"]["plants"] for row in detailed) if detailed else None,
        "defuses": sum(row["adv"]["defuses"] for row in detailed) if detailed else None,
        "minutes": round(sum(row.get("length") or 0 for row in played) / 60000),
        "aim": {
            "head": round(100 * heads / shots),
            "body": round(100 * bodies / shots),
            "leg": 100 - round(100 * heads / shots) - round(100 * bodies / shots),
        }
        if shots
        else None,
        "decided": totals["wins"] + totals["losses"],  # 0: no wins or losses to speak of (Deathmatch)
        "left_out": len([row for row in rows if row.get("line")]) - count,
        "form": [
            "win" if row["result"]["won"] is True else "loss" if row["result"]["won"] is False else "draw"
            for row in played
        ],
        "agents": agents[:3],
        "agent_table": agents,
        "maps": _per_map(played),
        "sides": _sides(with_rounds),
        "weapons": [
            {"key": key, "kills": number, "share": round(100 * number / weapon_kills)}
            for key, number in sorted(weapons.items(), key=lambda item: -item[1])[:6]
        ],
        "lobby": _versus_lobby(with_rounds),
        "mates": _mates(rows),
    }
