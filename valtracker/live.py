"""The owner's game right now (/live), from GLZ (riot.Riot.live): whether VALORANT is open; in the menus, the
party, its queue and buttons to start or stop matchmaking; at agent select, the team's picks, the roles it still
lacks and the owner's agents to pick and lock; in the match, everyone's agent, card, title, level and rank.

Names are shown only for players who do not hide theirs in the game (``Incognito``), and a level only when it is
not hidden (``HideAccountLevel``): this page tells no more than the game's own screens do.
"""

from __future__ import annotations

from datetime import datetime

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


def _rank(mmr, act: str) -> tuple[int, int]:
    """(tier, RR) in the act being played from PD's mmr answer, or (0, 0)."""
    if not isinstance(mmr, dict):
        return 0, 0
    skills = ((mmr.get("QueueSkills") or {}).get("competitive") or {}).get("SeasonalInfoBySeasonID") or {}
    for season, info in skills.items() if isinstance(skills, dict) else []:
        if act and _id(season) == act and isinstance(info, dict) and _number(info.get("CompetitiveTier")) > 2:
            return _number(info.get("CompetitiveTier")), _number(info.get("RankedRating"))
    latest = mmr.get("LatestCompetitiveUpdate") if isinstance(mmr.get("LatestCompetitiveUpdate"), dict) else {}
    if act and _id(latest.get("SeasonID")) == act:
        return _number(latest.get("TierAfterUpdate")), _number(latest.get("RankedRatingAfterUpdate"))
    return 0, 0


def _player(item: dict, look: dict, me: str, rank_key: str) -> dict:
    identity = item.get("PlayerIdentity") if isinstance(item.get("PlayerIdentity"), dict) else {}
    agent = look["agents"].get(_id(item.get("CharacterID"))) or {}
    subject = _id(item.get("Subject"))
    hidden = bool(identity.get("Incognito"))
    name = look["names"].get(subject) if not hidden else None
    badge = item.get("SeasonalBadgeInfo") if isinstance(item.get("SeasonalBadgeInfo"), dict) else {}
    tier, rr = _rank(look["ranks"].get(subject), look["act"])
    if tier <= 2:
        tier, rr = _number(item.get(rank_key) if rank_key in item else badge.get("Rank")), None
    known = look["tiers"].get(tier) or {}
    rank_name = str(known.get("tierName") or "")
    card = _id(identity.get("PlayerCardID"))
    title = look["titles"].get(_id(identity.get("PlayerTitleID"))) or {}
    return {
        "me": subject == me,
        "agent": agent.get("displayName") or "",
        "agent_icon": agent.get("displayIcon") or "",
        "agent_id": _id(item.get("CharacterID")),
        "role": _id((agent.get("role") or {}).get("uuid")),
        "state": str(item.get("CharacterSelectionState") or ""),
        "name": name[0] if name else "",
        "tag": name[1] if name else "",
        "hidden": hidden,
        # a hidden player's card and title are left out too: they could tell who it is
        "card": f"https://media.valorant-api.com/playercards/{card}/smallart.png"
        if card and not hidden and len(card) == 36
        else "",
        "title": "" if hidden else str(title.get("titleText") or ""),
        "level": None if identity.get("HideAccountLevel") else _number(identity.get("AccountLevel")) or None,
        "rank": {
            "name": rank_name.title() if rank_name.isupper() else rank_name,
            "icon": known.get("smallIcon"),
            "rr": rr,
        }
        if tier > 2
        else None,
        "team": str(item.get("TeamID") or ""),
        "owner": bool(item.get("IsOwner")),
    }


def _seconds_since(value, now: float) -> int | None:
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.year < 2000:  # GLZ's "0001-01-01": not queued
        return None
    return max(0, int(now - when.timestamp()))


def _party(party: dict, look: dict, me: str, queue_name, now: float) -> dict:
    members = [
        _player(item, look, me, "CompetitiveTier") for item in party.get("Members") or [] if isinstance(item, dict)
    ]
    making = party.get("MatchmakingData") if isinstance(party.get("MatchmakingData"), dict) else {}
    state = str(party.get("State") or "")
    current = _id(making.get("QueueID"))
    eligible = [_id(queue) for queue in party.get("EligibleQueues") or [] if isinstance(queue, str) and queue]
    if current and current not in eligible:
        eligible.insert(0, current)
    return {
        "members": members,
        "state": "matchmaking" if state == "MATCHMAKING" else "idle" if state in ("DEFAULT", "") else "starting",
        "queue": current,
        "queue_name": queue_name(current) if current else "",
        "queues": [{"id": queue, "name": queue_name(queue)} for queue in eligible],
        "waited": _seconds_since(party.get("QueueEntryTime"), now) if state == "MATCHMAKING" else None,
        "leader": any(row["me"] and row["owner"] for row in members) or len(members) <= 1,
        "restricted": _number(party.get("RestrictedSeconds")),
    }


def page(found: dict, content: Content, lang: str, me: str, owned_agents: set | None, queue_name) -> dict:
    """What live.html shows of ``found`` (riot.Riot.live); ``owned_agents``: the owner's agents (uuids) for the
    suggestions, ``queue_name(id)``: a queue's name in the page's language."""
    phase = found.get("phase") or ""
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
    agents = content.agent_index(lang)
    act = content.act(lang)
    look = {
        "agents": agents,
        "tiers": content.tiers(lang),
        "titles": content.index("playertitles", lang),
        "names": found.get("names") or {},
        "ranks": found.get("ranks") or {},
        "act": (act or {}).get("uuid") or "",
    }
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
        "running": found.get("running"),
        "party": None,
        "picker": None,
    }
    if phase == "menus" and isinstance(found.get("party"), dict):
        values["party"] = _party(found["party"], look, me, queue_name, float(found.get("at") or 0))
    if phase == "pregame":
        team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
        rows = [
            _player(item, look, me, "CompetitiveTier") for item in team.get("Players") or [] if isinstance(item, dict)
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
        mine = next((row for row in rows if row["me"]), None)
        if mine is not None:
            # the agents the owner can pick: theirs (all, when that could not be read), but not one a teammate
            # has locked
            locked = {row["agent_id"] for row in rows if row["state"] == "locked" and not row["me"]}
            values["picker"] = {
                "current": mine["agent_id"] if mine["state"] in ("selected", "locked") else "",
                "current_name": mine["agent"],
                "locked": mine["state"] == "locked",
                "agents": [
                    {
                        "id": uuid,
                        "name": agent.get("displayName") or "",
                        "icon": agent.get("displayIcon") or "",
                        "role": ((agent.get("role") or {}).get("displayName")) or "",
                        "taken": uuid in locked,
                    }
                    for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                    if owned_agents is None or uuid in owned_agents
                ],
            }
    elif phase == "ingame":
        rows = [
            _player(item, look, me, "")
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
