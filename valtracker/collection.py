"""The owner's collection page: riot.py's answer (uuids and counts) with the game's names and pictures (content.py),
laid out like the game's own collection screen (weapons in its order, then one weapon's skins and its buddy). The
order of the items does not follow what is worn: wearing one (in place, without leaving the page) marks it where it
is. Sets: the whole loadout saved under a name (owner.py keeps them) and worn again in one press."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from valtracker import i18n
from valtracker.content import Content, variant_name
from valtracker.riot import ITEM_TYPES, wearing

#: The tabs: the collection's kind -> its name on the page (in the page's language: i18n).
TABS = {
    "skins": "スキン",
    "buddies": "ガンバディー",
    "cards": "カード",
    "sprays": "スプレー",
    "titles": "タイトル",
    "flex": "フレックス",
    "agents": "エージェント",
    "sets": "セット",
}
#: The tabs' kinds -> valorant-api.com's list of them (content.py).
LISTS = {"buddies": "buddies", "cards": "playercards", "sprays": "sprays", "titles": "playertitles", "flex": "flex"}
#: The game's order of weapons: sidearms, SMGs, shotguns, rifles, snipers, machine guns, melee (cheapest first).
CATEGORIES = ("Sidearm", "SMG", "Shotgun", "Rifle", "Sniper", "Heavy", "Melee")
#: A gun's buddy in the loadout (the copy, the buddy, its level); melee weapons have none.
CHARM = ("CharmInstanceID", "CharmID", "CharmLevelID")
#: A gun's skin in the loadout.
SKIN = ("SkinID", "SkinLevelID", "ChromaID")
#: How many sets can be saved.
MAX_SETS = 20
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


def expressions(loadout: dict) -> list[dict]:
    """What the loadout's wheel holds, in its order: ``[{"slot", "type": PD's item type, "id"}]``. playerloadout v3
    lists sprays and flex as ``ActiveExpressions`` (``TypeID``, ``AssetID``; a place is its position there), v2 had
    only sprays (``Sprays``, a place is its ``EquipSlotID``)."""
    found = []
    if isinstance(loadout.get("ActiveExpressions"), list):
        for number, item in enumerate(loadout["ActiveExpressions"]):
            if isinstance(item, dict) and item.get("AssetID"):
                found.append({"slot": str(number), "type": _id(item.get("TypeID")), "id": _id(item["AssetID"])})
        return found
    for item in loadout.get("Sprays") or []:
        if isinstance(item, dict) and item.get("EquipSlotID"):
            found.append(
                {"slot": str(item["EquipSlotID"]), "type": ITEM_TYPES["sprays"], "id": _id(item.get("SprayID"))}
            )
    return found


def melee(weapon: dict) -> bool:
    return str(weapon.get("category") or "").endswith("::Melee")


def page(found: dict, content: Content, lang: str, show: str, weapon: str = "", sets: list | None = None) -> dict:
    """What collection.html shows of ``found`` (riot.Riot.collection) with the tab ``show`` open; on the skins'
    tab, ``weapon`` (a uuid) is the weapon whose skins are open, as the game's collection opens one at a time.
    ``sets``: the saved sets (owner.py)."""
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
    counts = {kind: _count(kind, owned, content, lang) for kind in TABS if kind != "sets"}
    counts["sets"] = len(sets or [])
    if show == "sets":
        items = _sets(sets or [], loadout if found.get("loadout") is not None else None, content, lang)
    else:
        items = makers[show](owned, loadout, content, lang) if show in owned else []
    picked = next((group for group in items if group.get("uuid") == _id(weapon)), None) if show == "skins" else None
    if picked:
        picked["buddy"] = _buddy_choices(found, loadout, content, lang, picked)
    return {
        "name": found.get("name") or "",
        "tag": found.get("tag") or "",
        "level": identity.get("AccountLevel"),
        "card": {"small": card.get("smallArt"), "wide": card.get("wideArt")},
        "title": title.get("titleText") or "",
        "wallet": _wallet(found.get("wallet"), content, lang),
        "tabs": [
            {"key": key, "name": i18n.text(lang, name), "count": counts[key], "current": key == show}
            for key, name in TABS.items()
        ],
        "show": show,
        "missing": show != "sets" and (show in (found.get("missing") or []) or show not in owned),
        # what is worn could not be read (PD's playerloadout): nothing is marked worn, and nothing called standard
        "worn_read": isinstance(found.get("loadout"), dict),
        "items": items,
        "picked": picked,
        "problem_detail": found.get("problem") or "",
        "wheel": wheel(loadout, content, lang, show) if show in ("sprays", "flex") else [],
        "guns": _gun_choices(loadout, content, lang) if show == "buddies" else [],
        "full": len(sets or []) >= MAX_SETS,
        "max_sets": MAX_SETS,
    }


def wheel(loadout: dict, content: Content, lang: str, kind: str) -> list[dict]:
    """The wheel's places an item of ``kind`` (sprays, flex) can go to, in the loadout's order: ``[{"slot",
    "number", "name": what is in it}]`` (the owner picks where it goes). playerloadout v3 lets any place hold a
    spray or a flex; v2 had sprays only."""
    sprays, flex = content.index("sprays", lang), content.index("flex", lang)
    v3 = isinstance(loadout.get("ActiveExpressions"), list)
    if not v3 and kind != "sprays":
        return []
    places = []
    for number, item in enumerate(expressions(loadout), 1):
        if item["type"] not in (ITEM_TYPES["sprays"], ITEM_TYPES["flex"]):
            continue
        known = (sprays if item["type"] == ITEM_TYPES["sprays"] else flex).get(item["id"]) or {}
        places.append({"slot": item["slot"], "number": number, "name": known.get("displayName") or ""})
    return places


def _gun_choices(loadout: dict, content: Content, lang: str) -> list[dict]:
    """The loadout's weapons a buddy can go on (not melee), in the game's order: ``[{"uuid", "name", "buddy": the
    one on it}]``."""
    buddies = content.index("buddies", lang)
    worn = {_id(gun.get("ID")): gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)}
    choices = []
    for weapon in sorted(content.weapons(lang), key=_weapon_order):
        gun = worn.get(_id(weapon.get("uuid")))
        if gun is None or melee(weapon):
            continue
        buddy = buddies.get(_id(gun.get("CharmLevelID"))) or buddies.get(_id(gun.get("CharmID"))) or {}
        choices.append(
            {
                "uuid": _id(weapon.get("uuid")),
                "name": weapon.get("displayName") or "",
                "buddy": buddy.get("displayName"),
            }
        )
    return choices


def _buddy_choices(found: dict, loadout: dict, content: Content, lang: str, weapon: dict) -> dict | None:
    """The open weapon's buddy, as the game's weapon screen lets it be changed: ``{"items": [{"uuid", "name",
    "current", "free": copies on no weapon, "from": the weapon it comes off}], "worn": whether one is on}``, or
    None (a melee weapon, or one the loadout does not have)."""
    gun = next(
        (gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict) and _id(gun.get("ID")) == weapon["uuid"]),
        None,
    )
    known = next((item for item in content.weapons(lang) if _id(item.get("uuid")) == weapon["uuid"]), {})
    if gun is None or melee(known):
        return None
    index = content.index("buddies", lang)
    names = {_id(item.get("uuid")): item.get("displayName") or "" for item in content.weapons(lang)}
    on: dict[str, list] = {}
    for other in loadout.get("Guns") or []:
        if isinstance(other, dict):
            buddy = index.get(_id(other.get("CharmLevelID"))) or index.get(_id(other.get("CharmID")))
            if buddy:
                on.setdefault(_id(buddy.get("uuid")), []).append(_id(other.get("ID")))
    copies: Counter = Counter()
    buddies: dict[str, dict] = {}
    for level, count in ((found.get("owned") or {}).get("buddies") or {}).items():
        buddy = index.get(level)
        if buddy:
            buddies[_id(buddy.get("uuid"))] = buddy
            copies[_id(buddy.get("uuid"))] += count
    items = []
    for uuid, buddy in sorted(buddies.items(), key=lambda pair: pair[1].get("displayName") or ""):
        wearing_it = on.get(uuid, [])
        current = weapon["uuid"] in wearing_it
        free = max(0, copies[uuid] - len(wearing_it))
        items.append(
            {
                "uuid": uuid,
                "name": buddy.get("displayName") or "",
                "current": current,
                "free": free,
                "from": names.get(wearing_it[0], "") if wearing_it and not free and not current else "",
            }
        )
    return {"items": items, "worn": bool(_id(gun.get("CharmID")))}


def snapshot(loadout: dict) -> dict:
    """What a set keeps of a loadout (PD's playerloadout): each weapon's skin and buddy, the wheel and the card and
    title, in the loadout's own shape (so riot.wearing reads it as it reads a loadout)."""
    guns = []
    for gun in loadout.get("Guns") or []:
        if isinstance(gun, dict) and gun.get("ID"):
            guns.append({"ID": _id(gun["ID"]), **{key: gun[key] for key in SKIN + CHARM if key in gun}})
    kept: dict = {"Guns": guns}
    if isinstance(loadout.get("ActiveExpressions"), list):
        kept["ActiveExpressions"] = [
            {"TypeID": item.get("TypeID"), "AssetID": item.get("AssetID")}
            for item in loadout["ActiveExpressions"]
            if isinstance(item, dict)
        ]
    elif isinstance(loadout.get("Sprays"), list):
        kept["Sprays"] = [dict(item) for item in loadout["Sprays"] if isinstance(item, dict)]
    identity = loadout.get("Identity") if isinstance(loadout.get("Identity"), dict) else {}
    kept["Identity"] = {key: identity[key] for key in ("PlayerCardID", "PlayerTitleID") if identity.get(key)}
    return kept


def wear_set(kept: dict):
    """The change to the loadout (riot.Riot.equip) that wears a saved set: its skins and buddies on its weapons,
    its wheel, card and title. A weapon the set does not know keeps its skin, and gives up a buddy copy the set puts
    on another weapon."""
    guns = {_id(gun.get("ID")): gun for gun in kept.get("Guns") or [] if isinstance(gun, dict)}
    copies = {_id(gun.get("CharmInstanceID")) for gun in guns.values() if gun.get("CharmInstanceID")}

    def change(loadout: dict) -> None:
        for gun in loadout.get("Guns") or []:
            if not isinstance(gun, dict):
                continue
            wanted = guns.get(_id(gun.get("ID")))
            if wanted is None:
                if _id(gun.get("CharmInstanceID")) in copies:
                    for key in CHARM:
                        gun.pop(key, None)
                continue
            gun.update({key: wanted[key] for key in SKIN if key in wanted})
            for key in CHARM:
                if wanted.get("CharmInstanceID"):
                    gun[key] = wanted.get(key)
                else:
                    gun.pop(key, None)
        if isinstance(kept.get("ActiveExpressions"), list) and isinstance(loadout.get("ActiveExpressions"), list):
            loadout["ActiveExpressions"] = [dict(item) for item in kept["ActiveExpressions"]]
        elif isinstance(kept.get("Sprays"), list) and isinstance(loadout.get("Sprays"), list):
            loadout["Sprays"] = [dict(item) for item in kept["Sprays"]]
        if isinstance(loadout.get("Identity"), dict):
            loadout["Identity"].update(kept.get("Identity") or {})

    return change


def _sets(sets: list, loadout: dict | None, content: Content, lang: str) -> list[dict]:
    """The saved sets, newest first: ``[{"id", "name", "at", "card", "title", "guns": [{"weapon", "skin",
    "buddy"}] (the weapons with a skin of the owner's, in the game's order), "skins", "buddies", "same": whether it
    is what is worn now}]``."""
    index, tiers = content.skins(lang), content.index("contenttiers", lang)
    cards, titles = content.index("playercards", lang), content.index("playertitles", lang)
    buddies = content.index("buddies", lang)
    order = {_id(weapon.get("uuid")): _weapon_order(weapon) for weapon in content.weapons(lang)}
    names = {_id(weapon.get("uuid")): weapon.get("displayName") or "" for weapon in content.weapons(lang)}
    now = wearing(loadout) if loadout is not None else None
    listed = []
    for kept in reversed(sets):
        guns = []
        for gun in sorted(kept.get("Guns") or [], key=lambda gun: order.get(_id(gun.get("ID")), (99,))):
            known = index.get(_id(gun.get("SkinID")))
            skin = known[1] if known and _tier(known[1], tiers)["key"] else None
            buddy = buddies.get(_id(gun.get("CharmLevelID"))) or buddies.get(_id(gun.get("CharmID"))) or {}
            if skin or buddy:
                guns.append(
                    {
                        "weapon": names.get(_id(gun.get("ID")), ""),
                        "skin": (skin or {}).get("displayName") or "",
                        "buddy": buddy.get("displayName") or "",
                    }
                )
        identity = kept.get("Identity") or {}
        card = cards.get(_id(identity.get("PlayerCardID"))) or {}
        title = titles.get(_id(identity.get("PlayerTitleID"))) or {}
        listed.append(
            {
                "id": kept.get("id") or "",
                "name": kept.get("name") or "",
                "when": _saved_at(kept.get("at"), lang),
                "card": card.get("smallArt"),
                "title": title.get("titleText") or "",
                "guns": guns,
                "skins": sum(bool(gun["skin"]) for gun in guns),
                "buddies": sum(bool(gun["buddy"]) for gun in guns),
                "same": now is not None and wearing(kept) == now,
                "search": str(kept.get("name") or "").lower(),
            }
        )
    return listed


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
    worn_on = {_id(gun.get("ID")): gun for gun in guns}
    buddies = content.index("buddies", lang)
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
                    part["name"] = i18n.text(lang, "標準")
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
        items.sort(key=lambda item: (-item["tier"]["order"], item["name"]))
        worn = next((item for item in items if item["equipped"]), None)
        groups.append(
            {
                "uuid": _id(weapon.get("uuid")),
                "name": weapon.get("displayName") or "",
                "icon": weapon.get("displayIcon"),
                # the weapon's square on the page: what it wears now (the standard look: the weapon's own picture)
                "image": (worn or {}).get("image") or weapon.get("displayIcon"),
                "worn": (worn or {}).get("name") or "",
                # at the top of the weapon's skins: what it wears now, as the game's own screen shows it
                "now": _now(worn_on.get(_id(weapon.get("uuid"))), weapon, index, tiers, buddies),
                # typing a skin's name finds its weapon
                "search": " ".join([weapon.get("displayName") or ""] + [item["name"] for item in items]).lower(),
                "items": items,
            }
        )
    return groups


def _now(gun: dict | None, weapon: dict, index: dict, tiers: dict, buddies: dict) -> dict:
    """What a weapon wears now (the loadout's ``Guns`` entry): the skin, its variant and level, its picture,
    tier and buddy. A weapon without a skin of the owner's wears its standard one."""
    gun = gun or {}
    known = index.get(_id(gun.get("SkinID")))
    skin = known[1] if known and _id(known[0].get("uuid")) == _id(weapon.get("uuid")) else {}
    parts = skin.get("chromas") or []
    chroma = next((part for part in parts if _id(part.get("uuid")) == _id(gun.get("ChromaID"))), None)
    levels = [level for level in skin.get("levels") or [] if isinstance(level, dict)]
    level = next(
        (number for number, item in enumerate(levels, 1) if _id(item.get("uuid")) == _id(gun.get("SkinLevelID"))), 0
    )
    buddy = buddies.get(_id(gun.get("CharmLevelID"))) or buddies.get(_id(gun.get("CharmID"))) or {}
    tier = _tier(skin, tiers)
    return {
        "name": skin.get("displayName") or "",
        "standard": not tier["key"],
        "image": (chroma or {}).get("fullRender")
        or (chroma or {}).get("displayIcon")
        or skin.get("displayIcon")
        or weapon.get("displayIcon"),
        "tier": tier,
        # the first variant is the skin's own look
        "variant": variant_name(chroma.get("displayName"), skin.get("displayName") or "")
        if chroma and parts and chroma is not parts[0]
        else "",
        "level": level if len(levels) > 1 else 0,
        "levels": len(levels),
        "buddy": {"name": buddy.get("displayName") or "", "icon": buddy.get("displayIcon")} if buddy else None,
    }


def _weapon_order(weapon: dict) -> tuple:
    category = str(weapon.get("category") or "").rsplit("::", 1)[-1]
    return (
        CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES),
        int(weapon.get("cost") or 0),
        str(weapon.get("displayName") or ""),
    )


def _simple(kind: str, owned, content: Content, lang: str, picture, equipped: Counter, name=None) -> list[dict]:
    """Owned items of a valorant-api.com list (``kind``) whose items, or their levels, are what PD names
    (``owned``: {uuid: copies}): one entry per item, by name."""
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
    listed.sort(key=lambda item: item["name"])
    return listed


def _buddies(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(_id(gun.get("CharmID")) for gun in loadout.get("Guns") or [] if isinstance(gun, dict))
    return _simple("buddies", owned.get("buddies"), content, lang, lambda item: item.get("displayIcon"), worn)


def _cards(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([_id((loadout.get("Identity") or {}).get("PlayerCardID"))])
    return _simple("playercards", owned.get("cards"), content, lang, lambda item: item.get("smallArt"), worn)


def _sprays(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(item["id"] for item in expressions(loadout) if item["type"] == ITEM_TYPES["sprays"])
    picture = lambda item: item.get("fullTransparentIcon") or item.get("displayIcon")
    return _simple("sprays", owned.get("sprays"), content, lang, picture, worn)


def _titles(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([_id((loadout.get("Identity") or {}).get("PlayerTitleID"))])
    words = lambda item: item.get("titleText")
    return _simple("playertitles", owned.get("titles"), content, lang, lambda item: None, worn, words)


def _flex(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(item["id"] for item in expressions(loadout) if item["type"] == ITEM_TYPES["flex"])
    return _simple("flex", owned.get("flex"), content, lang, lambda item: item.get("displayIcon"), worn)


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


def _saved_at(value, lang: str) -> str:
    """When a set was saved (an ISO time in Japan's), short: 11月7日 2:50 / 7 Nov 2:50."""
    try:
        return i18n.when(lang, datetime.fromisoformat(str(value or "")))
    except ValueError:
        return ""
