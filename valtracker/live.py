"""The owner's match being played (/live): at agent select, the team's picks and which roles it still lacks (with
the owner's agents for them); in the match, everyone's agent, level and rank. From GLZ (riot.Riot.live).

Names are shown only for players who do not hide theirs in the game (``Incognito``): this page tells no more
than the game's own screen does.
"""

from __future__ import annotations

from valtracker.content import Content

#: valorant-api.com's roles, in the order a team usually fills them: smokes first, then information, a sentinel
#: and an entry.
CONTROLLER = "4ee40330-ecdd-4f2f-98a8-eb1243428373"
INITIATOR = "1b47567f-8f7b-444b-aae3-b0c634622d10"
SENTINEL = "5fc02f99-4091-4486-a531-98459a07e894"
DUELIST = "dbe8757e-9e92-4ed4-b39f-9dfc589691d4"
ROLES = (CONTROLLER, INITIATOR, SENTINEL, DUELIST)
#: Agents suggested for a missing role at most.
SUGGESTED = 4


def _id(value) -> str:
    return str(value or "").lower()


def _number(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _player(item: dict, agents: dict, tiers: dict, names: dict, me: str, rank_key: str) -> dict:
    identity = item.get("PlayerIdentity") if isinstance(item.get("PlayerIdentity"), dict) else {}
    agent = agents.get(_id(item.get("CharacterID"))) or {}
    subject = _id(item.get("Subject"))
    name = names.get(subject) if not identity.get("Incognito") else None
    badge = item.get("SeasonalBadgeInfo") if isinstance(item.get("SeasonalBadgeInfo"), dict) else {}
    tier = _number(item.get(rank_key) if rank_key in item else badge.get("Rank"))
    known = tiers.get(tier) or {}
    rank_name = str(known.get("tierName") or "")
    return {
        "me": subject == me,
        "agent": agent.get("displayName") or "",
        "agent_icon": agent.get("displayIcon") or "",
        "role": _id((agent.get("role") or {}).get("uuid")),
        "state": str(item.get("CharacterSelectionState") or ""),
        "name": name[0] if name else "",
        "tag": name[1] if name else "",
        "level": None if identity.get("HideAccountLevel") else _number(identity.get("AccountLevel")) or None,
        "rank": {
            "name": rank_name.title() if rank_name.isupper() else rank_name,
            "icon": known.get("smallIcon"),
        }
        if tier > 2
        else None,
        "team": str(item.get("TeamID") or ""),
    }


def page(found: dict, content: Content, lang: str, me: str, owned_agents: set | None, queue_name) -> dict:
    """What live.html shows of ``found`` (riot.Riot.live); ``owned_agents``: the owner's agents (uuids) for the
    suggestions, ``queue_name(id)``: a queue's name in the page's language."""
    phase = found.get("phase") or ""
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
    agents = content.agent_index(lang)
    tiers = content.tiers(lang)
    names = found.get("names") or {}
    index = content.map_index(lang)
    address = str(match.get("MapID") or "").rsplit("/", 1)[-1].lower()
    known_map = index.get(address) or {}
    values: dict = {
        "phase": phase,
        "map": {"name": known_map.get("displayName") or "", "image": known_map.get("listViewIcon") or ""},
        "queue": "",
        "players": [],
        "teams": [],
        "missing_roles": [],
        "left": None,
    }
    if phase == "pregame":
        team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
        rows = [
            _player(item, agents, tiers, names, me, "CompetitiveTier")
            for item in team.get("Players") or []
            if isinstance(item, dict)
        ]
        values["players"] = rows
        values["queue"] = queue_name(str(match.get("QueueID") or ""))
        values["left"] = _number(match.get("PhaseTimeRemainingNS")) // 1_000_000_000 or None
        values["enemy_locked"] = _number(match.get("EnemyTeamLockCount"))
        values["enemy_size"] = _number(match.get("EnemyTeamSize")) or 5
        picked = {row["role"] for row in rows if row["state"] in ("selected", "locked") and not row["me"]}
        taken = {_id(item.get("CharacterID")) for item in team.get("Players") or [] if isinstance(item, dict)}
        roles = {_id((agent.get("role") or {}).get("uuid")): agent.get("role") for agent in agents.values()}
        for role in ROLES:
            if role in picked or role not in roles:
                continue
            choices = [
                {"name": agent.get("displayName") or "", "icon": agent.get("displayIcon") or ""}
                for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                if _id((agent.get("role") or {}).get("uuid")) == role
                and uuid not in taken
                and (owned_agents is None or uuid in owned_agents)
            ]
            values["missing_roles"].append(
                {"name": (roles[role] or {}).get("displayName") or "", "agents": choices[:SUGGESTED]}
            )
    elif phase == "ingame":
        rows = [
            _player(item, agents, tiers, names, me, "")
            for item in match.get("Players") or []
            if isinstance(item, dict) and not item.get("IsCoach")
        ]
        mine = next((row["team"] for row in rows if row["me"]), "")
        groups: dict[str, list] = {}
        for row in rows:
            groups.setdefault(row["team"], []).append(row)
        values["teams"] = [
            {"ours": team == mine, "players": players}
            for team, players in sorted(groups.items(), key=lambda pair: pair[0] != mine)
        ]
        making = match.get("MatchmakingData") if isinstance(match.get("MatchmakingData"), dict) else {}
        values["queue"] = queue_name(str(making.get("QueueID") or ""))
    return values
