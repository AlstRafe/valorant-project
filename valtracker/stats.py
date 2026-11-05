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


def line(player: dict, rounds: int) -> dict:
    """One player's numbers: K / D / A, ACS (score per round), ADR (damage per round), HS% and +/-."""
    stats = player.get("stats") or {}
    kills, deaths, assists = _int(stats.get("kills")), _int(stats.get("deaths")), _int(stats.get("assists"))
    shots = _int(stats.get("headshots")) + _int(stats.get("bodyshots")) + _int(stats.get("legshots"))
    damage = _int((stats.get("damage") or {}).get("dealt"))
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
        "damage": damage,
        "score": _int(stats.get("score")),
    }


def team_result(match: dict, team_id: str) -> dict:
    """``{"won": True/False/None (a draw or no teams), "us": rounds won, "them": rounds lost}``."""
    for team in match.get("teams") or []:
        if isinstance(team, dict) and str(team.get("team_id")) == str(team_id):
            rounds = team.get("rounds") or {}
            us, them = _int(rounds.get("won")), _int(rounds.get("lost"))
            won = team.get("won")
            return {"won": None if us == them else bool(won), "us": us, "them": them}
    return {"won": None, "us": 0, "them": 0}


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


def summary(rows: list[dict]) -> dict:
    """Over the matches shown (the standard games, when there are any): wins and losses, win rate, average K/D/A, ACS, HS%, and the agents played most."""
    played = [row for row in rows if row.get("line")]
    played = [row for row in played if row.get("standard", True)] or played
    if not played:
        return {}
    count = len(played)
    wins = sum(1 for row in played if row["result"]["won"] is True)
    losses = sum(1 for row in played if row["result"]["won"] is False)
    kills = sum(row["line"]["kills"] for row in played)
    deaths = sum(row["line"]["deaths"] for row in played)
    shots = sum(row["line"]["shots"] for row in played)
    agents: dict[str, dict] = {}
    for row in played:
        key = row["agent_id"] or row["agent_name"]
        seen = agents.setdefault(
            key,
            {"id": row["agent_id"], "name": row["agent_name"], "icon": row.get("agent_icon"), "games": 0, "wins": 0},
        )
        seen["games"] += 1
        seen["wins"] += 1 if row["result"]["won"] is True else 0
    return {
        "games": count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100 * wins / (wins + losses)) if wins + losses else 0,
        "kills": round(kills / count, 1),
        "deaths": round(deaths / count, 1),
        "assists": round(sum(row["line"]["assists"] for row in played) / count, 1),
        "kd": round(kills / deaths, 2) if deaths else float(kills),
        "acs": round(sum(row["line"]["acs"] for row in played) / count),
        "hs": round(100 * sum(row["line"]["headshots"] for row in played) / shots) if shots else 0,
        "agents": sorted(agents.values(), key=lambda agent: -agent["games"])[:3],
    }
