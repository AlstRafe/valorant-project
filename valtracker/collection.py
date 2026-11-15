"""持ち主のコレクションのページ: riot.py の答え（uuid と数）にゲームの名前と絵（content.py）を付け、
ゲームのコレクション画面と同じように並べる（武器はゲームの順、次に 1 つの武器のスキンとガンバディ）。
並び順は装備に左右されない: 装備すると（ページを離れずにその場で）その場所に印が付く。
セット: ロードアウト全体を名前を付けて保存し（owner.py が持つ）、1 回押せばまた装備する。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from valtracker import i18n
from valtracker.content import Content, variant_name
from valtracker.riot import ITEM_TYPES, wearing
from valtracker.util import lower_id

#: タブ: コレクションの種類 -> ページでの名前（ページの言語で、i18n）。
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
#: タブの種類 -> valorant-api.com の一覧（content.py）。
LISTS = {"buddies": "buddies", "cards": "playercards", "sprays": "sprays", "titles": "playertitles", "flex": "flex"}
#: ゲームの武器の順: サイドアーム、SMG、ショットガン、ライフル、スナイパー、マシンガン、近接（安い順）。
CATEGORIES = ("Sidearm", "SMG", "Shotgun", "Rifle", "Sniper", "Heavy", "Melee")
#: ロードアウトでの銃のガンバディ（コピー、ガンバディ、レベル）。近接武器には無い。
CHARM = ("CharmInstanceID", "CharmID", "CharmLevelID")
#: ロードアウトでの銃のスキン。
SKIN = ("SkinID", "SkinLevelID", "ChromaID")
#: 保存できるセットの数。
MAX_SETS = 20
#: スキンのティア（安い順、valorant-api.com の devName）: それぞれページでの色を持つ。
TIERS = ("Select", "Deluxe", "Premium", "Exclusive", "Ultra")
VP = "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"
RADIANITE = "e59aa87c-4cbf-517a-5983-6e81511be9b7"
KINGDOM = "85ca954a-41f2-ce94-9b45-8ca3dd39a00d"
#: VALORANT ポイント、レディアナイト、キングダムクレジット: ウォレットの順（ほかはその後）。
CURRENCIES = (VP, RADIANITE, KINGDOM)
#: どのアカウントも最初から持っているエージェント（PD はその後に解放したものしか出さない）: ブリムストーン、ジェット、
#: フェニックス、セージ、ソーヴァ。
STARTERS = {
    "9f0d8ba9-4140-b941-57d3-a7ad57c6b417",
    "add6443a-41bd-e414-f6ad-e58d267f4e95",
    "eb93336a-449b-9c1b-0a54-a891f7921d69",
    "569fdd95-4d10-43ab-ca70-79becc718b46",
    "320b2a48-4d9b-a075-30f1-1f93a9b638fa",
}


def expressions(loadout: dict) -> list[dict]:
    """ロードアウトのホイールの中身（順番どおり）: ``[{"slot", "type": PD のアイテムタイプ, "id"}]``。
    playerloadout v3 はスプレーとフレックスを ``ActiveExpressions``（``TypeID``、``AssetID``。場所はそこでの位置）に、
    v2 はスプレーだけ（``Sprays``。場所は ``EquipSlotID``）。"""
    found = []
    if isinstance(loadout.get("ActiveExpressions"), list):
        for number, item in enumerate(loadout["ActiveExpressions"]):
            if isinstance(item, dict) and item.get("AssetID"):
                found.append(
                    {"slot": str(number), "type": lower_id(item.get("TypeID")), "id": lower_id(item["AssetID"])}
                )
        return found
    for item in loadout.get("Sprays") or []:
        if isinstance(item, dict) and item.get("EquipSlotID"):
            found.append(
                {"slot": str(item["EquipSlotID"]), "type": ITEM_TYPES["sprays"], "id": lower_id(item.get("SprayID"))}
            )
    return found


def melee(weapon: dict) -> bool:
    return str(weapon.get("category") or "").endswith("::Melee")


def page(found: dict, content: Content, lang: str, show: str, weapon: str = "", sets: list | None = None) -> dict:
    """タブ ``show`` を開いたときに collection.html に出す ``found``（riot.Riot.collection）。
    スキンのタブでは ``weapon``（uuid）がスキンを開いている武器（ゲームのコレクションも 1 つずつ開く）。
    ``sets``: 保存したセット（owner.py）。"""
    owned = found.get("owned") or {}
    loadout = found.get("loadout") if isinstance(found.get("loadout"), dict) else {}
    identity = loadout.get("Identity") if isinstance(loadout.get("Identity"), dict) else {}
    cards = content.index("playercards", lang)
    card = cards.get(lower_id(identity.get("PlayerCardID"))) or {}
    title = content.index("playertitles", lang).get(lower_id(identity.get("PlayerTitleID"))) or {}
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
    picked = (
        next((group for group in items if group.get("uuid") == lower_id(weapon)), None) if show == "skins" else None
    )
    if picked:
        picked["buddy"] = _buddy_choices(found, loadout, content, lang, picked)
    return {
        "name": found.get("name") or "",
        "tag": found.get("tag") or "",
        "level": identity.get("AccountLevel"),
        "card": {"small": card.get("smallArt"), "wide": card.get("wideArt")},
        "title": title.get("titleText") or "",
        "wallet": wallet_info(found.get("wallet"), content, lang),
        "tabs": [
            {"key": key, "name": i18n.text(lang, name), "count": counts[key], "current": key == show}
            for key, name in TABS.items()
        ],
        "show": show,
        "missing": show != "sets" and (show in (found.get("missing") or []) or show not in owned),
        # 装備が読めなかった（PD の playerloadout）: 装備中の印も標準の表示も出さない
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
    """``kind``（スプレー、フレックス）のアイテムを入れられるホイールの場所（ロードアウトの順）
    : ``[{"slot", "number", "name": 入っているもの}]``（どこに入れるかは持ち主が選ぶ）。
    playerloadout v3 はどの場所にもスプレーかフレックスを入れられ、v2 はスプレーだけだった。"""
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
    """ガンバディを着けられるロードアウトの武器（近接以外、ゲームの順）
    : ``[{"uuid", "name", "buddy": 着けているもの}]``。"""
    buddies = content.index("buddies", lang)
    worn = {lower_id(gun.get("ID")): gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)}
    choices = []
    for weapon in sorted(content.weapons(lang), key=weapon_order):
        gun = worn.get(lower_id(weapon.get("uuid")))
        if gun is None or melee(weapon):
            continue
        buddy = buddies.get(lower_id(gun.get("CharmLevelID"))) or buddies.get(lower_id(gun.get("CharmID"))) or {}
        choices.append(
            {
                "uuid": lower_id(weapon.get("uuid")),
                "name": weapon.get("displayName") or "",
                "buddy": buddy.get("displayName"),
            }
        )
    return choices


def _buddy_choices(found: dict, loadout: dict, content: Content, lang: str, weapon: dict) -> dict | None:
    """開いている武器のガンバディ（ゲームの武器画面と同じく変えられる）
    : ``{"items": [{"uuid", "name", "current", "free": どの武器にも着けていないコピー数, "from": 外れる武器}], "worn":
    着けているか}``。近接武器、またはロードアウトに無い武器なら None。"""
    gun = next(
        (
            gun
            for gun in loadout.get("Guns") or []
            if isinstance(gun, dict) and lower_id(gun.get("ID")) == weapon["uuid"]
        ),
        None,
    )
    known = next((item for item in content.weapons(lang) if lower_id(item.get("uuid")) == weapon["uuid"]), {})
    if gun is None or melee(known):
        return None
    index = content.index("buddies", lang)
    names = {lower_id(item.get("uuid")): item.get("displayName") or "" for item in content.weapons(lang)}
    on: dict[str, list] = {}
    for other in loadout.get("Guns") or []:
        if isinstance(other, dict):
            buddy = index.get(lower_id(other.get("CharmLevelID"))) or index.get(lower_id(other.get("CharmID")))
            if buddy:
                on.setdefault(lower_id(buddy.get("uuid")), []).append(lower_id(other.get("ID")))
    copies: Counter = Counter()
    buddies: dict[str, dict] = {}
    for level, count in ((found.get("owned") or {}).get("buddies") or {}).items():
        buddy = index.get(level)
        if buddy:
            buddies[lower_id(buddy.get("uuid"))] = buddy
            copies[lower_id(buddy.get("uuid"))] += count
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
    return {"items": items, "worn": bool(lower_id(gun.get("CharmID")))}


def snapshot(loadout: dict) -> dict:
    """セットがロードアウト（PD の playerloadout）から残すもの: 各武器のスキンとガンバディ、ホイール、カードと称号。
    ロードアウトと同じ形（riot.wearing がロードアウトと同じく読める）。"""
    guns = [
        {"ID": lower_id(gun["ID"]), **{key: gun[key] for key in SKIN + CHARM if key in gun}}
        for gun in loadout.get("Guns") or []
        if isinstance(gun, dict) and gun.get("ID")
    ]
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
    """保存したセットを装備するためのロードアウトの変更（riot.Riot.equip）: 武器にスキンとガンバディ、ホイール、カード、
    称号。セットが知らない武器はスキンをそのままにし、セットが別の武器に着けるガンバディのコピーは手放す。"""
    guns = {lower_id(gun.get("ID")): gun for gun in kept.get("Guns") or [] if isinstance(gun, dict)}
    copies = {lower_id(gun.get("CharmInstanceID")) for gun in guns.values() if gun.get("CharmInstanceID")}

    def change(loadout: dict) -> None:
        for gun in loadout.get("Guns") or []:
            if not isinstance(gun, dict):
                continue
            wanted = guns.get(lower_id(gun.get("ID")))
            if wanted is None:
                if lower_id(gun.get("CharmInstanceID")) in copies:
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
    """保存したセット（新しい順）
    : ``[{"id", "name", "at", "card", "title", "guns": [{"weapon", "skin", "buddy"}]（持ち主のスキンを着けた武器、ゲームの順）
    , "skins", "buddies", "same": 今の装備と同じか}]``。"""
    index, tiers = content.skins(lang), content.index("contenttiers", lang)
    cards, titles = content.index("playercards", lang), content.index("playertitles", lang)
    buddies = content.index("buddies", lang)
    order = {lower_id(weapon.get("uuid")): weapon_order(weapon) for weapon in content.weapons(lang)}
    names = {lower_id(weapon.get("uuid")): weapon.get("displayName") or "" for weapon in content.weapons(lang)}
    now = wearing(loadout) if loadout is not None else None
    listed = []
    for kept in reversed(sets):
        guns = []
        for gun in sorted(kept.get("Guns") or [], key=lambda gun: order.get(lower_id(gun.get("ID")), (99,))):
            known = index.get(lower_id(gun.get("SkinID")))
            skin = known[1] if known and tier_info(known[1], tiers)["key"] else None
            buddy = buddies.get(lower_id(gun.get("CharmLevelID"))) or buddies.get(lower_id(gun.get("CharmID"))) or {}
            if skin or buddy:
                guns.append(
                    {
                        "weapon": names.get(lower_id(gun.get("ID")), ""),
                        "skin": (skin or {}).get("displayName") or "",
                        "buddy": buddy.get("displayName") or "",
                    }
                )
        identity = kept.get("Identity") or {}
        card = cards.get(lower_id(identity.get("PlayerCardID"))) or {}
        title = titles.get(lower_id(identity.get("PlayerTitleID"))) or {}
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
    """タブの数: 一覧に出るアイテムの数（PD がどのレベルを挙げてもスキンは 1 つ）。読めなければ None。"""
    if kind not in owned:
        return None
    if kind == "agents":
        unlocked = set(owned["agents"]) | STARTERS
        return sum(lower_id(agent.get("uuid")) in unlocked for agent in content.agents(lang))
    if kind == "skins":
        index = content.skins(lang)
        return len({lower_id(index[item][1].get("uuid")) for item in owned["skins"] if item in index})
    index = content.index(LISTS[kind], lang)
    return len({lower_id(index[item].get("uuid")) for item in owned[kind] if item in index})


def wallet_info(balances, content: Content, lang: str) -> list[dict]:
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


def tier_info(skin: dict, tiers: dict) -> dict:
    tier = tiers.get(lower_id(skin.get("contentTierUuid"))) or {}
    dev = str(tier.get("devName") or "")
    return {
        "key": dev.lower() if dev in TIERS else "",
        "order": TIERS.index(dev) if dev in TIERS else -1,
        "name": tier.get("displayName") or "",
        "icon": tier.get("displayIcon"),
    }


def _skins(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    """ゲームの順の武器と、それぞれの所持スキン: ``[{"name", "icon", "items": [...]}]``。"""
    index = content.skins(lang)
    tiers = content.index("contenttiers", lang)
    guns = [gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)]
    equipped = {lower_id(gun.get("SkinID")): lower_id(gun.get("ChromaID")) for gun in guns}
    chromas = set(owned.get("chromas") or {})
    skins: dict[str, list] = {}
    for item in owned.get("skins") or {}:
        if item in index:
            weapon, skin = index[item]
            skins.setdefault(lower_id(weapon.get("uuid")), [])
            if all(
                lower_id(other.get("uuid")) != lower_id(skin.get("uuid"))
                for other in skins[lower_id(weapon.get("uuid"))]
            ):
                skins[lower_id(weapon.get("uuid"))].append(skin)
    worn_on = {lower_id(gun.get("ID")): gun for gun in guns}
    buddies = content.index("buddies", lang)
    groups = []
    for weapon in sorted(content.weapons(lang), key=weapon_order):
        found = skins.get(lower_id(weapon.get("uuid")))
        if not found:
            continue
        items = []
        for skin in found:
            uuid = lower_id(skin.get("uuid"))
            parts = skin.get("chromas") or []
            on = uuid in equipped
            worn = (
                next((part for part in parts if lower_id(part.get("uuid")) == equipped.get(uuid)), None) if on else None
            )
            picture = (
                (worn or {}).get("displayIcon")
                or skin.get("displayIcon")
                or next(
                    (level.get("displayIcon") for level in skin.get("levels") or [] if level.get("displayIcon")), ""
                )
                or (worn or (parts[0] if parts else {})).get("fullRender")
            )
            # 持ち主が着けられるバリアント: 1 つ目はスキンに付いてくる、ほかは解放してから
            wearable = [
                {
                    "uuid": lower_id(part.get("uuid")),
                    "name": variant_name(part.get("displayName"), skin.get("displayName") or ""),
                }
                for number, part in enumerate(parts)
                if number == 0 or lower_id(part.get("uuid")) in chromas
            ]
            for number, part in enumerate(wearable):
                part["current"] = on and part["uuid"] == equipped.get(uuid)
                if number == 0:
                    part["name"] = i18n.text(lang, "標準")
            items.append(
                {
                    "uuid": uuid,
                    "weapon": lower_id(weapon.get("uuid")),
                    "name": skin.get("displayName") or "",
                    "image": picture,
                    "tier": tier_info(skin, tiers),
                    "equipped": on,
                    "wearable": wearable if len(wearable) > 1 else [],
                    # 1 つ目のクロマはスキンに付いてくる。ほかは 1 つずつ解放する
                    "variants": 1 + sum(lower_id(part.get("uuid")) in chromas for part in parts[1:])
                    if len(parts) > 1
                    else 0,
                    "of": len(parts),
                }
            )
        items.sort(key=lambda item: (-item["tier"]["order"], item["name"]))
        worn = next((item for item in items if item["equipped"]), None)
        groups.append(
            {
                "uuid": lower_id(weapon.get("uuid")),
                "name": weapon.get("displayName") or "",
                "icon": weapon.get("displayIcon"),
                # ページでの武器の四角: 今着けているもの（標準の見た目なら武器自身の絵）
                "image": (worn or {}).get("image") or weapon.get("displayIcon"),
                "worn": (worn or {}).get("name") or "",
                # 武器のスキンの一番上: 今着けているもの（ゲームの画面と同じ）
                "now": _now(worn_on.get(lower_id(weapon.get("uuid"))), weapon, index, tiers, buddies),
                # スキンの名前を入力すると、その武器が見つかる
                "search": " ".join([weapon.get("displayName") or ""] + [item["name"] for item in items]).lower(),
                "items": items,
            }
        )
    return groups


def _now(gun: dict | None, weapon: dict, index: dict, tiers: dict, buddies: dict) -> dict:
    """武器が今着けているもの（ロードアウトの ``Guns`` の項目）: スキン、バリアントとレベル、絵、ティア、ガンバディ。
    持ち主のスキンを着けていない武器は標準のもの。"""
    gun = gun or {}
    known = index.get(lower_id(gun.get("SkinID")))
    skin = known[1] if known and lower_id(known[0].get("uuid")) == lower_id(weapon.get("uuid")) else {}
    parts = skin.get("chromas") or []
    chroma = next((part for part in parts if lower_id(part.get("uuid")) == lower_id(gun.get("ChromaID"))), None)
    levels = [level for level in skin.get("levels") or [] if isinstance(level, dict)]
    level = next(
        (
            number
            for number, item in enumerate(levels, 1)
            if lower_id(item.get("uuid")) == lower_id(gun.get("SkinLevelID"))
        ),
        0,
    )
    buddy = buddies.get(lower_id(gun.get("CharmLevelID"))) or buddies.get(lower_id(gun.get("CharmID"))) or {}
    tier = tier_info(skin, tiers)
    return {
        "name": skin.get("displayName") or "",
        "standard": not tier["key"],
        "image": (chroma or {}).get("fullRender")
        or (chroma or {}).get("displayIcon")
        or skin.get("displayIcon")
        or weapon.get("displayIcon"),
        "tier": tier,
        # 1 つ目のバリアントはスキン自身の見た目
        "variant": variant_name(chroma.get("displayName"), skin.get("displayName") or "")
        if chroma and parts and chroma is not parts[0]
        else "",
        "level": level if len(levels) > 1 else 0,
        "levels": len(levels),
        "buddy": {"name": buddy.get("displayName") or "", "icon": buddy.get("displayIcon")} if buddy else None,
    }


def weapon_order(weapon: dict) -> tuple:
    category = str(weapon.get("category") or "").rsplit("::", 1)[-1]
    return (
        CATEGORIES.index(category) if category in CATEGORIES else len(CATEGORIES),
        int(weapon.get("cost") or 0),
        str(weapon.get("displayName") or ""),
    )


def _simple(kind: str, owned, content: Content, lang: str, picture, equipped: Counter, name=None) -> list[dict]:
    """valorant-api.com の一覧（``kind``）のうち、アイテムかそのレベルが PD の挙げるもの（``owned``: {uuid: コピー数}）
    である所持アイテム: アイテムごとに 1 件、名前順。"""
    index = content.index(kind, lang)
    copies: Counter = Counter()
    items: dict[str, dict] = {}
    for item, count in (owned or {}).items():
        known = index.get(item)
        if known:
            items[lower_id(known.get("uuid"))] = known
            copies[lower_id(known.get("uuid"))] += count
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
    worn = Counter(lower_id(gun.get("CharmID")) for gun in loadout.get("Guns") or [] if isinstance(gun, dict))
    return _simple("buddies", owned.get("buddies"), content, lang, lambda item: item.get("displayIcon"), worn)


def _cards(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([lower_id((loadout.get("Identity") or {}).get("PlayerCardID"))])
    return _simple("playercards", owned.get("cards"), content, lang, lambda item: item.get("smallArt"), worn)


def _sprays(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter(item["id"] for item in expressions(loadout) if item["type"] == ITEM_TYPES["sprays"])
    picture = lambda item: item.get("fullTransparentIcon") or item.get("displayIcon")
    return _simple("sprays", owned.get("sprays"), content, lang, picture, worn)


def _titles(owned: dict, loadout: dict, content: Content, lang: str) -> list[dict]:
    worn = Counter([lower_id((loadout.get("Identity") or {}).get("PlayerTitleID"))])
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
            "owned": lower_id(agent.get("uuid")) in unlocked,
        }
        for agent in content.agents(lang)
    ]


def _saved_at(value, lang: str) -> str:
    """セットを保存した時刻（日本時間の ISO 時刻）を短く: 11月7日 2:50 / 7 Nov 2:50。"""
    try:
        return i18n.when(lang, datetime.fromisoformat(str(value or "")))
    except ValueError:
        return ""
