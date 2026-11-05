"""The owner's progress (/progress): the act's battle pass, the events' passes, the agents' contracts and the
missions, from PD's contracts (riot.Riot.contracts) with valorant-api.com's names and each level's XP (content.py).
"""

from __future__ import annotations

from valtracker.content import Content


def _id(value) -> str:
    return str(value or "").lower()


def _number(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def contract(item: dict, definition: dict, agents: dict) -> dict:
    """One contract: how far it is (levels, XP to the next level, the share of all its XP)."""
    levels = [_number(xp) for xp in definition.get("levels") or []]
    reached = min(max(_number(item.get("ProgressionLevelReached")), 0), len(levels))
    toward = max(_number(item.get("ProgressionTowardsNextLevel")), 0)
    main = len(levels) - _number(definition.get("epilogue"))
    total = sum(levels)
    done = sum(levels[:reached]) + (min(toward, levels[reached]) if reached < len(levels) else 0)
    agent = agents.get(_id(definition.get("of"))) if definition.get("type") == "Agent" else None
    return {
        "name": (agent or {}).get("displayName") or definition.get("displayName") or "",
        "icon": (agent or {}).get("displayIcon") or definition.get("displayIcon") or "",
        "type": definition.get("type") or "",
        "reached": reached,
        "main": main if main > 0 else len(levels),
        "all": len(levels),
        "toward": toward if reached < len(levels) else 0,
        "next": levels[reached] if reached < len(levels) else 0,
        "share": round(100 * done / total) if total else 0,
        "complete": bool(levels) and reached >= len(levels),
        "epilogue": reached > main > 0,
    }


def page(found: dict, content: Content, lang: str, now: float, when) -> dict:
    """What progress.html shows; ``when(iso)``: a moment in the owner's clock (tools._when)."""
    answer = found.get("contracts") if isinstance(found.get("contracts"), dict) else {}
    known = content.index("contracts", lang)
    agents = content.agent_index(lang)
    act = content.act(lang, now) or {}
    shown = []
    for item in answer.get("Contracts") or []:
        if not isinstance(item, dict):
            continue
        definition = known.get(_id(item.get("ContractDefinitionID")))
        if definition:
            shown.append((definition, contract(item, definition, agents)))
    battle_pass = next(
        (entry for definition, entry in shown if entry["type"] == "Season" and definition.get("of") == act.get("uuid")),
        None,
    )
    events = [
        entry
        for _, entry in shown
        if entry["type"] == "Event" and not entry["complete"] and (entry["reached"] or entry["toward"])
    ]
    agent_contracts = sorted(
        (entry for _, entry in shown if entry["type"] == "Agent"),
        key=lambda entry: (entry["complete"], -entry["share"], entry["name"]),
    )
    missions = [item for item in answer.get("Missions") or [] if isinstance(item, dict)]
    metadata = answer.get("MissionMetadata") if isinstance(answer.get("MissionMetadata"), dict) else {}
    ends = sorted(str(item.get("ExpirationTime")) for item in missions if item.get("ExpirationTime"))
    return {
        "act": act.get("name") or "",
        "battle_pass": battle_pass,
        "events": events,
        "agents": agent_contracts,
        "missions": {
            "count": len(missions),
            "done": sum(1 for item in missions if item.get("Complete")),
            "ends": when(ends[0]) if ends else None,
            "refill": when(metadata["WeeklyRefillTime"]) if metadata.get("WeeklyRefillTime") else None,
        },
        "missing": found.get("missing") or [],
        "problem_detail": found.get("problem") or "",
        "read": bool(answer),
    }
