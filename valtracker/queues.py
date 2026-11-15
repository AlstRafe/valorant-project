"""ゲームのキュー設定（riot.Riot.queues: GLZ の customgameconfigs）を人が読める形に: 各モードが今どのマップで遊べるか、
コンペティティブに一緒に入れるのは誰か（ランク差、上位ランクのパーティー人数）。

Riot の名前: ``Queues[].MapWeights``（``"<map>:<weight>"``、weight 0 は出ない）、
``PartySkillDisparityCompetitiveTiersCeilings``（{tier: その tier がいるパーティーで許される最高 tier}）、
``HighSkillTier`` と ``MaxPartySizeHighSkill``（その tier 以上がいるとパーティーはその人数まで。
ただしフルパーティーで ``AllowFullPartyBypassSkillRestrictions`` なら別）、``InvalidPartySizes``、``MaxPartySize``。
無いものは省く。"""

from __future__ import annotations

from valtracker.util import to_int

#: ページに並べるモード（ゲームの順、QueueID）。
ORDER = ("competitive", "unrated", "swiftplay", "spikerush", "deathmatch", "ggteam", "hurm", "premier")
#: 一番下のランク tier（アイアン 1）。表では 0〜2 が「ランクなし」。
IRON = 3


def _maps(queue: dict, index: dict) -> list[dict]:
    """キューの今のマップ: ``[{"name", "image"}]``（valorant-api.com の名前）。"""
    found = []
    for entry in queue.get("MapWeights") or []:
        text = ":".join(entry) if isinstance(entry, list) else str(entry)
        where, _, weight = text.rpartition(":")
        where = where or text
        if weight and to_int(weight, 1) <= 0:
            continue
        known = index.get(where.rsplit("/", 1)[-1].lower()) or index.get(where.lower()) or {}
        name = known.get("displayName") or where.rsplit("/", 1)[-1]
        if name and all(item["name"] != name for item in found):
            found.append({"name": name, "image": known.get("listViewIcon")})
    return sorted(found, key=lambda item: item["name"])


def settings(config: dict, map_index: dict) -> dict:
    """``{"queues": [{"id", "maps", "party": (最少, 最多), "invalid": [...]}], "competitive": ルールか None}``。"""
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
                "party": (to_int(queue.get("MinPartySize"), 1), to_int(queue.get("MaxPartySize"), 5)),
                "invalid": sorted(to_int(size) for size in queue.get("InvalidPartySizes") or []),
            }
        )
    competitive = queues.get("competitive")
    return {"queues": listed, "competitive": rules(competitive) if competitive else None}


def rules(queue: dict) -> dict:
    ceilings = {
        to_int(tier): to_int(top)
        for tier, top in (queue.get("PartySkillDisparityCompetitiveTiersCeilings") or {}).items()
    }
    return {
        "ceilings": {tier: top for tier, top in sorted(ceilings.items()) if tier >= IRON},
        "high_tier": to_int(queue.get("HighSkillTier")),
        "high_size": to_int(queue.get("MaxPartySizeHighSkill")),
        "bypass": bool(queue.get("AllowFullPartyBypassSkillRestrictions")),
        "max": to_int(queue.get("MaxPartySize"), 5),
        "invalid": sorted(to_int(size) for size in queue.get("InvalidPartySizes") or []),
    }


def check(tiers: list[int], found: dict) -> dict:
    """このランク（tier の番号、0 はまだランクなし）
    のパーティーでコンペティティブに入れるか: ``{"ok": bool か None（わからない）
    , "why": 理由のキー, "values": 文に入れる値}``。"""
    size = len(tiers)
    ranked = [tier for tier in tiers if tier >= IRON]
    if size < 2:
        return {"ok": None, "why": "few", "values": {}}
    if size > found["max"] or size in found["invalid"]:
        return {"ok": False, "why": "size", "values": {"size": size}}
    full = size == 5 and found["bypass"]
    high = found["high_tier"] and found["high_size"] and any(tier >= found["high_tier"] for tier in ranked)
    if high and size > found["high_size"] and not full:
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
