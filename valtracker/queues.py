"""The game's queue settings (riot.Riot.queues: GLZ customgameconfigs) read for people: which maps each mode plays
now, and who may queue for competitive together (the rank gap, the party sizes the top ranks may have).

Riot's names: ``Queues[].MapWeights`` (``"<map>:<weight>"``, weight 0 = not played), ``PartySkillDisparity
CompetitiveTiersCeilings`` ({tier: the highest tier a party with that tier in it may have}), ``HighSkillTier`` and
``MaxPartySizeHighSkill`` (from that tier up, a party may have at most that many, unless it is a full party and
``AllowFullPartyBypassSkillRestrictions``), ``InvalidPartySizes``, ``MaxPartySize``. Anything missing is left out.
"""

from __future__ import annotations

#: The modes the page lists, in the game's order (QueueID).
ORDER = ("competitive", "unrated", "swiftplay", "spikerush", "deathmatch", "ggteam", "hurm", "premier")
#: The lowest rank tier (Iron 1); 0-2 are "unranked" in the tables.
IRON = 3


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _maps(queue: dict, index: dict) -> list[dict]:
    """The maps a queue plays now: ``[{"name", "image"}]`` in valorant-api.com's names."""
    found = []
    for entry in queue.get("MapWeights") or []:
        text = ":".join(entry) if isinstance(entry, list) else str(entry)
        where, _, weight = text.rpartition(":")
        where = where or text
        if weight and _int(weight, 1) <= 0:
            continue
        known = index.get(where.rsplit("/", 1)[-1].lower()) or index.get(where.lower()) or {}
        name = known.get("displayName") or where.rsplit("/", 1)[-1]
        if name and all(item["name"] != name for item in found):
            found.append({"name": name, "image": known.get("listViewIcon")})
    return sorted(found, key=lambda item: item["name"])


def settings(config: dict, map_index: dict) -> dict:
    """``{"queues": [{"id", "maps", "party": (min, max), "invalid": [...]}], "competitive": rules or None}``."""
    queues = {
        str(item.get("QueueID") or "").lower(): item
        for item in (config or {}).get("Queues") or []
        if isinstance(item, dict) and item.get("Enabled", True)
    }
    listed = []
    for key in sorted(queues, key=lambda key: (ORDER.index(key) if key in ORDER else len(ORDER), key)):
        queue = queues[key]
        maps = _maps(queue, map_index)
        if not maps:
            continue
        listed.append(
            {
                "id": key,
                "maps": maps,
                "party": (_int(queue.get("MinPartySize"), 1), _int(queue.get("MaxPartySize"), 5)),
                "invalid": sorted(_int(size) for size in queue.get("InvalidPartySizes") or []),
            }
        )
    competitive = queues.get("competitive")
    return {"queues": listed, "competitive": rules(competitive) if competitive else None}


def rules(queue: dict) -> dict:
    ceilings = {
        _int(tier): _int(top) for tier, top in (queue.get("PartySkillDisparityCompetitiveTiersCeilings") or {}).items()
    }
    return {
        "ceilings": {tier: top for tier, top in sorted(ceilings.items()) if tier >= IRON},
        "high_tier": _int(queue.get("HighSkillTier")),
        "high_size": _int(queue.get("MaxPartySizeHighSkill")),
        "bypass": bool(queue.get("AllowFullPartyBypassSkillRestrictions")),
        "max": _int(queue.get("MaxPartySize"), 5),
        "invalid": sorted(_int(size) for size in queue.get("InvalidPartySizes") or []),
    }


def check(tiers: list[int], found: dict) -> dict:
    """Whether a party of these ranks (tier numbers, 0 = not ranked yet) may queue for competitive:
    ``{"ok": bool or None (cannot tell), "why": a reason key, "values": for its words}``."""
    size = len(tiers)
    ranked = [tier for tier in tiers if tier >= IRON]
    if size < 2:
        return {"ok": None, "why": "few", "values": {}}
    if size > found["max"] or size in found["invalid"]:
        return {"ok": False, "why": "size", "values": {"size": size}}
    full = size == 5 and found["bypass"]
    if found["high_tier"] and found["high_size"] and any(tier >= found["high_tier"] for tier in ranked):
        if size > found["high_size"] and not full:
            return {"ok": False, "why": "high", "values": {"size": found["high_size"], "tier": found["high_tier"]}}
    if full:
        return {"ok": True, "why": "full", "values": {}}
    if len(ranked) < 2:
        return {"ok": True, "why": "unranked", "values": {}}
    low, high = min(ranked), max(ranked)
    top = found["ceilings"].get(low)
    if top is None:
        return {"ok": None, "why": "unknown", "values": {}}
    if high > top:
        return {"ok": False, "why": "gap", "values": {"low": low, "high": high, "top": top}}
    return {"ok": True, "why": "gap_ok", "values": {"low": low, "top": top}}
