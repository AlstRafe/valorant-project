"""持ち主の進み具合（/progress）: アクトのバトルパス、イベントのパス、エージェントのコントラクト、ミッション。
PD の contracts（riot.Riot.contracts）に、valorant-api.com の名前と各レベルの XP（content.py）を合わせる。"""

from __future__ import annotations

from valtracker.content import Content
from valtracker.util import as_list, lower_id, to_int


def contract(item: dict, definition: dict, agents: dict) -> dict:
    """コントラクトひとつの進み具合（レベル、次のレベルまでの XP、全 XP に対する割合）。"""
    levels = [to_int(xp) for xp in as_list(definition.get("levels"))]
    reached = min(max(to_int(item.get("ProgressionLevelReached")), 0), len(levels))
    toward = max(to_int(item.get("ProgressionTowardsNextLevel")), 0)
    main = len(levels) - to_int(definition.get("epilogue"))
    total = sum(levels)
    done = sum(levels[:reached]) + (min(toward, levels[reached]) if reached < len(levels) else 0)
    agent = agents.get(lower_id(definition.get("of"))) if definition.get("type") == "Agent" else None
    return {
        "name": (agent or {}).get("displayName") or definition.get("displayName") or "",
        "icon": (agent or {}).get("displayIcon") or definition.get("displayIcon") or "",
        "type": definition.get("type") or "",
        "reached": reached,
        "main": main if main > 0 else len(levels),
        "toward": toward if reached < len(levels) else 0,
        "next": levels[reached] if reached < len(levels) else 0,
        "share": round(100 * done / total) if total else 0,
        "complete": bool(levels) and reached >= len(levels),
        "epilogue": reached > main > 0,
    }


def page(found: dict, content: Content, lang: str, now: float, when) -> dict:
    """progress.html に出すもの。``when(iso)``: 持ち主の時計での時刻（tools.moment_info）。"""
    answer = found.get("contracts") if isinstance(found.get("contracts"), dict) else {}
    known = content.index("contracts", lang)
    agents = content.agent_index(lang)
    act = content.act(lang, now) or {}
    shown = []
    for item in as_list(answer.get("Contracts")):
        if not isinstance(item, dict):
            continue
        definition = known.get(lower_id(item.get("ContractDefinitionID")))
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
    missions = [item for item in as_list(answer.get("Missions")) if isinstance(item, dict)]
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
