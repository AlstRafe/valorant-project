"""The owner's game right now (/live), from GLZ (riot.Riot.live): whether VALORANT is open; in the menus, the
party, its queue and buttons to start or stop matchmaking; at agent select, the team's picks, the roles it still
lacks and the owner's agents to pick and lock; in the match, everyone's agent, card, title, level and rank, with
each team's average rank and the act's record of the players who show their name.

Names are shown only for players who do not hide theirs in the game (``Incognito``), and a level only when it is
not hidden (``HideAccountLevel``): this page tells no more than the game's own screens do.
"""

from __future__ import annotations

from datetime import datetime

from valtracker import i18n
from valtracker.activity import FEW
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
#: How many of the owner's agents that win most on the map are named at agent select.
BEST_SHOWN = 3
#: How often (seconds) /live and the header's ライブ tab on the other pages ask where the game is, by where it is
#: (state's phase): every second once a match is found (the game's 5-second count before agent select), every two
#: while matchmaking and at agent select, seldom when nothing is about to change. Riot is asked once or twice a
#: look (riot.Riot.live), from this server: nothing runs on the owner's PC.
EVERY = {"": (20, 30), "menus": (5, 8), "queue": (2, 2), "found": (1, 1), "pregame": (2, 4), "ingame": (10, 15)}


def _id(value) -> str:
    return str(value or "").lower()


def icon(agent: dict) -> str:
    """An agent's picture for the small squares (pick, seats, roles): valorant-api.com's small one (128 px, a
    quarter of the bytes of the 256 px one) when it has it."""
    return str(agent.get("displayIconSmall") or agent.get("displayIcon") or "")


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


def _record(mmr, act: str) -> dict | None:
    """The act's competitive record from PD's mmr answer, as the game's career page has it: ``{"wins", "games",
    "win_rate", "peak": the highest tier won at this act}``, or None (no games)."""
    if not isinstance(mmr, dict) or not act:
        return None
    skills = ((mmr.get("QueueSkills") or {}).get("competitive") or {}).get("SeasonalInfoBySeasonID") or {}
    info = (
        next((info for season, info in skills.items() if _id(season) == act), None)
        if isinstance(skills, dict)
        else None
    )
    if not isinstance(info, dict):
        return None
    games = _number(info.get("NumberOfGames"))
    if games <= 0:
        return None
    wins = _number(info.get("NumberOfWinsWithPlacements") or info.get("NumberOfWins"))
    won_at = info.get("WinsByTier") if isinstance(info.get("WinsByTier"), dict) else {}
    peak = max((_number(tier) for tier, count in won_at.items() if _number(count) > 0), default=0)
    return {"wins": min(wins, games), "games": games, "win_rate": round(100 * min(wins, games) / games), "peak": peak}


def _rank_of(tier: int, look: dict) -> dict | None:
    known = look["tiers"].get(tier) or {}
    name = str(known.get("tierName") or "")
    return {"name": name.title() if name.isupper() else name, "icon": known.get("smallIcon")} if tier > 2 else None


def average(rows: list[dict], look: dict) -> dict | None:
    """The team's average rank (of the players with one), as a rank's name: how strong the lobby is."""
    tiers = [row["tier"] for row in rows if row["tier"] > 2]
    return _rank_of(round(sum(tiers) / len(tiers)), look) if len(tiers) >= 2 else None


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
        "agent_icon": icon(agent),
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
        "tier": tier if tier > 2 else 0,
        # the act's record and best rank only for the owner and the players who show their name: as public as
        # their page on this site, and nothing about the ones who hide who they are
        "record": _with_peak(_record(look["ranks"].get(subject), look["act"]), look)
        if (name or subject == me)
        else None,
        "team": str(item.get("TeamID") or ""),
        "owner": bool(item.get("IsOwner")),
    }


def _with_peak(record: dict | None, look: dict) -> dict | None:
    if record is not None:
        record["peak_rank"] = _rank_of(record["peak"], look)
    return record


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


def page(
    found: dict, content: Content, lang: str, me: str, owned_agents: set | None, queue_name, agent_stats=None
) -> dict:
    """What live.html shows of ``found`` (riot.Riot.live); ``owned_agents``: the owner's agents (uuids) for the
    suggestions, ``queue_name(id)``: a queue's name in the page's language, ``agent_stats(map uuid, queue id)``:
    how each agent does on the map in the matches this site has seen (activity.Activity.agents)."""
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
        "average": None,
        "every": EVERY[where(found)][0],
        # the agents' pictures, fetched by the browser while matchmaking: agent select then shows them at once
        "warm": icons(content, lang) if where(found) in ("queue", "found") else [],
    }
    if phase == "menus" and isinstance(found.get("party"), dict):
        values["party"] = _party(found["party"], look, me, queue_name, float(found.get("at") or 0))
    if phase == "pregame":
        team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
        rows = [
            _player(item, look, me, "CompetitiveTier") for item in team.get("Players") or [] if isinstance(item, dict)
        ]
        values["players"] = rows
        values["average"] = average(rows, look)
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
                {"name": agent.get("displayName") or "", "icon": icon(agent)}
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
            seen = {"matches": 0, "agents": []}
            if agent_stats and known_map.get("uuid"):
                seen = agent_stats(_id(known_map["uuid"]), _id(match.get("QueueID")))
            here = {item["agent"]: item for item in seen["agents"] if item["picks"] >= FEW}
            values["picker"] = {
                "current": mine["agent_id"] if mine["state"] in ("selected", "locked") else "",
                "current_name": mine["agent"],
                "locked": mine["state"] == "locked",
                "agents": [
                    {
                        "id": uuid,
                        "name": agent.get("displayName") or "",
                        "icon": icon(agent),
                        "role": ((agent.get("role") or {}).get("displayName")) or "",
                        "taken": uuid in locked,
                        "win": here[uuid]["win"] if uuid in here else None,
                    }
                    for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                    if owned_agents is None or uuid in owned_agents
                ],
                "matches": seen["matches"],
            }
            # the owner's agents that win most on this map (of those picked often enough to say)
            choices = [item for item in values["picker"]["agents"] if item["win"] is not None and not item["taken"]]
            values["picker"]["best"] = sorted(choices, key=lambda item: -item["win"])[:BEST_SHOWN]
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
            {"ours": team == mine, "players": players, "average": average(players, look)}
            for team, players in sorted(groups.items(), key=lambda pair: pair[0] != mine)
        ]
        making = match.get("MatchmakingData") if isinstance(match.get("MatchmakingData"), dict) else {}
        values["queue"] = queue_name(str(making.get("QueueID") or ""))
    return values


#: Where the owner's game is, in a few words (the header's ライブ tab, the top page's 自分 box).
STATE_WORDS = {
    "menus": "メニュー",
    "queue": "マッチング中",
    "found": "試合が見つかりました",
    "pregame": "エージェント選択中",
    "ingame": "対戦中",
    "unknown": "ゲームの状態を読めません",
    "closed": "VALORANT は開いていません",
}


def where(found: dict) -> str:
    """Where the owner's game is (riot.Riot.live's answer): "menus", "queue" (matchmaking), "found" (the match is
    found: the game's 5-second count before agent select), "pregame", "ingame" or "" (closed or not known)."""
    phase = found.get("phase") or ""
    party = found.get("party") if isinstance(found.get("party"), dict) else {}
    if phase == "menus":
        party_state = str(party.get("State") or "").upper()
        return "queue" if party_state == "MATCHMAKING" else "found" if "STARTING" in party_state else "menus"
    return phase if phase in EVERY else ""


def state(found: dict, content: Content, lang: str) -> dict:
    """Where the owner's game is, in a few words, for the header's ライブ tab on every page (/live/state):
    ``{"phase": where(found), "words", "map", "match": the match's id (agent select and the match), "running",
    "every": seconds until the tab asks again, "icons": the agents' pictures to have ready before agent select
    (matchmaking and a found match only)}``, in the page's language (``lang``)."""
    phase = where(found)
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
    place = ""
    if phase in ("pregame", "ingame"):
        address = str(match.get("MapID") or "").rsplit("/", 1)[-1].lower()
        place = (content.map_index(lang).get(address) or {}).get("displayName") or ""
    running = found.get("running")
    words = STATE_WORDS.get(phase) or STATE_WORDS["unknown" if running is None else "closed"]
    words = i18n.text(lang, words)
    return {
        "phase": phase,
        "words": i18n.text(lang, "{state}（{map}）", state=words, map=place) if place else words,
        "map": place,
        "match": _id(match.get("ID") or match.get("MatchID")) if phase in ("pregame", "ingame") else "",
        "running": running,
        "every": EVERY[phase][1],
        "icons": icons(content, lang) if phase in ("queue", "found") else [],
    }


def icons(content: Content, lang: str) -> list[str]:
    """Every agent's small picture (icon), for the browser to fetch while the party is matchmaking: agent select
    then shows its squares at once (the pictures are kept by the browser for a month)."""
    return sorted({icon(agent) for agent in content.agent_index(lang).values() if icon(agent)})
