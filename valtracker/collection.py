"""The owner's collection page: riot.py's answer (uuids and counts) with the game's names and pictures (content.py),
laid out like the game's own collection screen (weapons in its order, what is equipped first)."""

from __future__ import annotations

from collections import Counter

from valtracker.content import Content, variant_name

#: The tabs: the collection's kind -> its name on the page (the owner's page: Japanese only).
TABS = {
    "skins": "スキン",
    "buddies": "ガンバディー",
    "cards": "カード",
    "sprays": "スプレー",
    "titles": "タイトル",
    "flex": "フレックス",
    "agents": "エージェント",
}
#: The tabs' kinds -> valorant-api.com's list of them (content.py).
LISTS = {"buddies": "buddies", "cards": "playercards", "sprays": "sprays", "titles": "playertitles", "flex": "flex"}
#: The game's order of weapons: sidearms, SMGs, shotguns, rifles, snipers, machine guns, melee (cheapest first).
CATEGORIES = ("Sidearm", "SMG", "Shotgun", "Rifle", "Sniper", "Heavy", "Melee")
#: The tiers of skins, cheapest first (valorant-api.com's devName): each has its colour on the page.
TIERS = ("Select", "Deluxe", "Premium", "Exclusive", "Ultra")
#: VALORANT Points, Radianite, Kingdom Credits: the wallet's order (anything else after them).
CURRENCIES = (
    "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741",
    "e59aa87c-4cbf-517a-5983-6e81511be9b7",
    "85ca954a-41f2-ce94-9b45-8ca3dd39a00d",
)
#: The agents every account has from the start (PD lists only the ones unlocked since): Brimstone, Jett,
#: Phoenix, Sage, Sova.
STARTERS = {
    "9f0d8ba9-4140-b941-57d3-a7ad57c6b417",
    "add6443a-41bd-e414-f6ad-e58d267f4e95",
    "eb93336a-449b-9c1b-0a54-a891f7921d69",
    "569fdd95-4d10-43ab-ca70-79becc718b46",
    "320b2a48-4d9b-a075-30f1-1f93a9b638fa",
}


def _id(value) -> str:
    return str(value or "").lower()


def page(found: dict, content: Content, lang: str, show: str) -> dict:
    """What collection.html shows of ``found`` (riot.Riot.collection) with the tab ``show`` open."""
    owned = found.get("owned") or {}
    loadout = found.get("loadout") if isinstance(found.get("loadout"), dict) else {}
    identity = loadout.get("Identity") if isinstance(loadout.get("Identity"), dict) else {}
    cards = content.index("playercards", lang)
    card = cards.get(_id(identity.get("PlayerCardID"))) or {}
    title = content.index("playertitles", lang).get(_id(identity.get("PlayerTitleID"))) or {}
    makers = {
        "skins": _skins,
        "buddies": _buddies,
        "cards": _cards,
        "sprays": _sprays,
        "titles": _titles,
        "flex": _flex,
        "agents": _agents,
    }
    counts = {kind: _count(kind, owned, content, lang) for kind in TABS}
    return {
        "name": found.get("name") or "",
        "tag": found.get("tag") or "",
        "level": identity.get("AccountLevel"),
        "card": {"small": card.get("smallArt"), "wide": card.get("wideArt")},
        "title": title.get("titleText") or "",
        "wallet": _wallet(found.get("wallet"), content, lang),
        "tabs": [
            {"key": key, "name": name, "count": counts[key], "current": key == show} for key, name in TABS.items()
        ],
        "show": show,
        "missing": show in (found.get("missing") or []) or show not in owned,
        "items": makers[show](owned, loadout, content, lang) if show in owned else [],
        "problem_detail": found.get("problem") or "",
        "spray_slots": _spray_slots(loadout, content, lang) if show == "sprays" else [],
    }


def _spray_slots(loadout: dict, content: Content, lang: str) -> list[dict]:
    """The spray wheel's places, in the loadout's order: ``[{"slot": EquipSlotID, "number", "name": the spray in
    it}]`` (the owner picks where a spray goes)."""
    index = content.index("sprays", lang)
    slots = []
    for spray in loadout.get("Sprays") or []:
        if isinstance(spray, dict) and spray.get("EquipSlotID"):
            known = index.get(_id(spray.get("SprayID"))) or {}
            slots.append(
                {"slot": str(spray["EquipSlotID"]), "number": len(slots) + 1, "name": known.get("displayName") or ""}
            )
    return slots


def _count(kind: str, owned: dict, content: Content, lang: str):
    """The number on a tab: the items its list shows (a skin once, whatever levels of it PD names), or None when
    they could not be read."""
    if kind not in owned:
        return None
    if kind == "agents":
        unlocked = set(owned["agents"]) | STARTERS
        return sum(_id(agent.get("uuid")) in unlocked for agent in content.agents(lang))
    if kind == "skins":
        index = content.skins(lang)
        return len({_id(index[item][1].get("uuid")) for item in owned["skins"] if item in index})
    index = content.index(LISTS[kind], lang)
    return len({_id(index[item].get("uuid")) for item in owned[kind] if item in index})


def _wallet(balances, content: Content, lang: str) -> list[dict]:
    if not isinstance(balances, dict):
        return []
    known = content.index("currencies", lang)
    order = sorted(balances, key=lambda key: (CURRENCIES.index(key) if key in CURRENCIES else len(CURRENCIES), key))
    return [
        {
            "name": (known.get(key) or {}).get("displayName") or key,
            "icon": (known.get(key) or {}).get("displayIcon"),
            "amount": balances[key],
        }
        for key in order
        if key in known or key in CURRENCIES
    ]


def _tier(skin: dict, tiers: dict) -> dict:
    tier = tiers.get(_id(skin.get("contentTierUuid"))) or {}
    dev = str(tier.get("devName") or "")
    return {
        "key": dev.lower() if dev in TIERS else "",
        "order": TIERS.index(dev) if dev in TIERS else -1,
        "name": tier.get("displayName") or "",
        "icon": tier.get("displayIcon"),
    }


def _skins(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    """Weapons in the game's order, each with its owned skins: ``[{"name", "icon", "items": [...]}]``."""
    index = content.skins(lang)
    tiers = content.index("contenttiers", lang)
    guns = [gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)]
    equipped = {_id(gun.get("SkinID")): _id(gun.get("ChromaID")) for gun in guns}
    chromas = set(owned.get("chromas") or {})
    skins: dict[str, list] = {}
    for item in owned.get("skins") or {}:
        if item in index:
            weapon, skin = index[item]
            skins.setdefault(_id(weapon.get("uuid")), [])
            if all(_id(other.get("uuid")) != _id(skin.get("uuid")) for other in skins[_id(weapon.get("uuid"))]):
                skins[_id(weapon.get("uuid"))].append(skin)
    groups = []
    for weapon in sorted(content.weapons(lang), key=_weapon_order):
        found = skins.get(_id(weapon.get("uuid")))
        if not found:
            continue
        items = []
        for skin in found:
            uuid = _id(skin.get("uuid"))
            parts = skin.get("chromas") or []
            on = uuid in equipped
            worn = next((part for part in parts if _id(part.get("uuid")) == equipped.get(uuid)), None) if on else None
            picture = (
                (worn or {}).get("displayIcon")
                or skin.get("displayIcon")
                or next(
                    (level.get("displayIcon") for level in skin.get("levels") or [] if level.get("displayIcon")), ""
                )
                or (worn or (parts[0] if parts else {})).get("fullRender")
            )
            # the variants the owner can wear: the first comes with the skin, the others once unlocked
            wearable = [
                {
                    "uuid": _id(part.get("uuid")),
                    "name": variant_name(part.get("displayName"), skin.get("displayName") or ""),
                }
                for number, part in enumerate(parts)
                if number == 0 or _id(part.get("uuid")) in chromas
            ]
            for number, part in enumerate(wearable):
                part["current"] = on and part["uuid"] == equipped.get(uuid)
                if number == 0:
                    part["name"] = "標準"
            items.append(
                {
                    "uuid": uuid,
                    "weapon": _id(weapon.get("uuid")),
                    "name": skin.get("displayName") or "",
                    "image": picture,
                    "tier": _tier(skin, tiers),
                    "equipped": on,
                    "wearable": wearable if len(wearable) > 1 else [],
                    # the first chroma comes with the skin; the others are unlocked one by one
                    "variants": 1 + sum(_id(part.get("uuid")) in chromas for part in parts[1:])
                    if len(parts) > 1
                    else 0,
                    "of": len(parts),
                }
            )
        items.sort(key=lambda item: (not item["equipped"], -item["tier"]["order"], item["name"]))
        groups.append({"name": weapon.get("displayName") or "", "icon": weapon.get("displayIcon"), "items": items})
    return groups


def _weapon_order(weapon: dict) -> tuple:
    category = str(weapon.get("category") or "").rsplit("::", 1)[-1]
    return (
        CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES),
        int(weapon.get("cost") or 0),
        str(weapon.get("displayName") or ""),
    )


def _simple(kind: str, owned, content: Content, lang: str, picture, equipped: Counter, name=None) -> list[dict]:
    """Owned items of a valorant-api.com list (``kind``) whose items, or their levels, are what PD names
    (``owned``: {uuid: copies}): one entry per item, equipped first."""
    index = content.index(kind, lang)
    copies: Counter = Counter()
    items: dict[str, dict] = {}
    for item, count in (owned or {}).items():
        known = index.get(item)
        if known:
            items[_id(known.get("uuid"))] = known
            copies[_id(known.get("uuid"))] += count
    listed = [
        {
            "uuid": uuid,
            "name": (name(known) if name else "") or known.get("displayName") or "",
            "image": picture(known),
            "copies": copies[uuid],
            "equipped": equipped[uuid],
        }
        for uuid, known in items.items()
    ]
    listed.sort(key=lambda item: (not item["equipped"], item["name"]))
    return listed


def _buddies(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(_id(gun.get("CharmID")) for gun in loadout.get("Guns") or [] if isinstance(gun, dict))
    return _simple("buddies", owned.get("buddies"), content, lang, lambda item: item.get("displayIcon"), worn)


def _cards(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([_id((loadout.get("Identity") or {}).get("PlayerCardID"))])
    return _simple("playercards", owned.get("cards"), content, lang, lambda item: item.get("smallArt"), worn)


def _sprays(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(_id(spray.get("SprayID")) for spray in loadout.get("Sprays") or [] if isinstance(spray, dict))
    picture = lambda item: item.get("fullTransparentIcon") or item.get("displayIcon")
    return _simple("sprays", owned.get("sprays"), content, lang, picture, worn)


def _titles(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([_id((loadout.get("Identity") or {}).get("PlayerTitleID"))])
    words = lambda item: item.get("titleText")
    return _simple("playertitles", owned.get("titles"), content, lang, lambda item: None, worn, words)


def _flex(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    return _simple("flex", owned.get("flex"), content, lang, lambda item: item.get("displayIcon"), Counter())


def _agents(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    unlocked = set(owned.get("agents") or {}) | STARTERS
    return [
        {
            "name": agent.get("displayName") or "",
            "image": agent.get("displayIcon"),
            "owned": _id(agent.get("uuid")) in unlocked,
        }
        for agent in content.agents(lang)
    ]
