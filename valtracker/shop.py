"""ストアのページ: 全員に見えるおすすめバンドル（HenrikDev）と、
持ち主には自分の Riot アカウントの今日のストア（riot.Riot.shop）。その日の 4 スキン、ナイトマーケット、バンドル、
アクセサリーを、値段・残り時間・所持済みの印つきで出す。名前と絵は content.py から。"""

from __future__ import annotations

import math

from valtracker.collection import KINGDOM, VP, tier_info, wallet_info
from valtracker.content import Content
from valtracker.riot import ITEM_TYPES
from valtracker.util import lower_id, to_int

#: PD のアイテムタイプ -> こちらの種類（riot.ITEM_TYPES の逆）。
KINDS = {uuid: kind for kind, uuid in ITEM_TYPES.items()}
#: HenrikDev のおすすめバンドル内の種類の名前 -> こちらの種類。
FEATURED_KINDS = {
    "skin_level": "skins",
    "skin_chroma": "chromas",
    "buddy": "buddies",
    "player_card": "cards",
    "spray": "sprays",
    "player_title": "titles",
    "flex": "flex",
}
#: こちらの種類 -> valorant-api.com の一覧（content.py）と、どれを絵にするか。
LISTS = {
    "buddies": ("buddies", "displayIcon"),
    "cards": ("playercards", "wideArt"),
    "sprays": ("sprays", "fullTransparentIcon"),
    "titles": ("playertitles", None),
    "flex": ("flex", "displayIcon"),
}


def item(kind: str, uuid: str, content: Content, lang: str, said: dict | None = None) -> dict:
    """売り物ひとつ: ``{"kind", "name", "image", "tier"}``、スキンなら uuid も（``"skin"``: 図鑑のページ）。
    valorant-api.com が知らないものは HenrikDev の文（``said``）を使う。"""
    uuid, said = lower_id(uuid), said or {}
    if kind in ("skins", "chromas"):
        found = content.skins(lang).get(uuid)
        if found:
            _, skin = found
            level = next((part for part in skin.get("levels") or [] if lower_id(part.get("uuid")) == uuid), {})
            chroma = next((part for part in skin.get("chromas") or [] if lower_id(part.get("uuid")) == uuid), {})
            picture = (
                chroma.get("fullRender")
                or skin.get("displayIcon")
                or level.get("displayIcon")
                or next((part.get("fullRender") for part in skin.get("chromas") or [] if part.get("fullRender")), "")
            )
            tier = tier_info(skin, content.index("contenttiers", lang))
            return {
                "kind": "skins",
                "name": skin.get("displayName") or "",
                "image": picture,
                "tier": tier,
                "skin": lower_id(skin.get("uuid")),
            }
    if kind in LISTS:
        listed, picture = LISTS[kind]
        known = content.index(listed, lang).get(uuid) or {}
        if known:
            name = known.get("titleText") if kind == "titles" else known.get("displayName")
            image = (known.get(picture) or known.get("displayIcon")) if picture else None
            return {"kind": kind, "name": name or said.get("name") or "", "image": image, "tier": None}
    return {"kind": kind, "name": said.get("name") or "", "image": said.get("image") or None, "tier": None}


def _reward(offer: dict) -> tuple[str, str]:
    """オファーの最初の報酬: （こちらの種類, uuid）。"""
    rewards = [reward for reward in (offer or {}).get("Rewards") or [] if isinstance(reward, dict)]
    if not rewards:
        return "", ""
    first = rewards[0]
    return KINDS.get(lower_id(first.get("ItemTypeID")), ""), lower_id(first.get("ItemID"))


def _owned(kind: str, uuid: str, owned: dict) -> bool:
    return bool(owned) and uuid in (owned.get(kind) or {})


def _until(at: float, seconds) -> float:
    return at + to_int(seconds) if to_int(seconds) > 0 else 0


def left(seconds: float) -> dict:
    """残り ``{"hours", "minutes"}``（分単位で切り上げ）。"""
    minutes = max(0, math.ceil(seconds / 60))
    return {"hours": minutes // 60, "minutes": minutes % 60}


def page(found: dict, content: Content, lang: str, owned: dict | None = None) -> dict:
    """shop.html に出す持ち主のストア（riot.Riot.shop）。``owned``（コレクションのもの）で所持済みに印を付ける。"""
    front = found.get("storefront") if isinstance(found.get("storefront"), dict) else {}
    at = float(found.get("at") or 0)
    owned = owned or {}
    panel = front.get("SkinsPanelLayout") if isinstance(front.get("SkinsPanelLayout"), dict) else {}
    daily = []
    offers = [offer for offer in panel.get("SingleItemStoreOffers") or [] if isinstance(offer, dict)]
    for offer in offers:
        kind, uuid = _reward(offer)
        daily.append(item(kind or "skins", uuid, content, lang) | {"price": to_int((offer.get("Cost") or {}).get(VP))})
    if not offers:  # 古い形の答え: 値段なしのスキン uuid
        daily = [item("skins", uuid, content, lang) | {"price": 0} for uuid in panel.get("SingleItemOffers") or []]
    night = None
    bonus = front.get("BonusStore") if isinstance(front.get("BonusStore"), dict) else None
    if bonus:
        night = []
        for offer in bonus.get("BonusStoreOffers") or []:
            if not isinstance(offer, dict):
                continue
            kind, uuid = _reward(offer.get("Offer"))
            night.append(
                item(kind or "skins", uuid, content, lang)
                | {
                    "base": to_int(((offer.get("Offer") or {}).get("Cost") or {}).get(VP)),
                    "price": to_int((offer.get("DiscountCosts") or {}).get(VP)),
                    "discount": to_int(offer.get("DiscountPercent")),
                    "seen": bool(offer.get("IsSeen")),
                    "owned": _owned(kind or "skins", uuid, owned),
                }
            )
    featured = front.get("FeaturedBundle") if isinstance(front.get("FeaturedBundle"), dict) else {}
    bundles = featured.get("Bundles") or ([featured["Bundle"]] if isinstance(featured.get("Bundle"), dict) else [])
    known = content.index("bundles", lang)
    listed = []
    for bundle in bundles:
        if not isinstance(bundle, dict):
            continue
        info = known.get(lower_id(bundle.get("DataAssetID"))) or {}
        things = []
        for entry in bundle.get("Items") or []:
            if not isinstance(entry, dict):
                continue
            thing = entry.get("Item") if isinstance(entry.get("Item"), dict) else {}
            kind = KINDS.get(lower_id(thing.get("ItemTypeID")), "")
            things.append(
                item(kind, lower_id(thing.get("ItemID")), content, lang)
                | {
                    "price": to_int(entry.get("DiscountedPrice")),
                    "base": to_int(entry.get("BasePrice")),
                    "amount": to_int(thing.get("Amount")) or 1,
                    "owned": _owned(kind, lower_id(thing.get("ItemID")), owned),
                }
            )
        listed.append(
            {
                "name": info.get("displayName") or "",
                "image": info.get("displayIcon2") or info.get("displayIcon"),
                "price": to_int((bundle.get("TotalDiscountedCost") or {}).get(VP)) or sum(t["price"] for t in things),
                "base": to_int((bundle.get("TotalBaseCost") or {}).get(VP)),
                "until": _until(at, bundle.get("DurationRemainingInSeconds")),
                "items": things,
            }
        )
    store = front.get("AccessoryStore") if isinstance(front.get("AccessoryStore"), dict) else {}
    accessories = []
    for offer in store.get("AccessoryStoreOffers") or []:
        if not isinstance(offer, dict):
            continue
        kind, uuid = _reward(offer.get("Offer"))
        if kind:
            accessories.append(
                item(kind, uuid, content, lang)
                | {
                    "price": to_int(((offer.get("Offer") or {}).get("Cost") or {}).get(KINGDOM)),
                    "owned": _owned(kind, uuid, owned),
                }
            )
    for entry in daily:
        entry["owned"] = False  # ゲームは所持済みのものを出さない
    currencies = content.index("currencies", lang)
    return {
        "name": found.get("name") or "",
        "wallet": wallet_info(found.get("wallet"), content, lang),
        "daily": daily,
        "daily_until": _until(at, panel.get("SingleItemOffersRemainingDurationInSeconds")),
        "night": night,
        "night_until": _until(at, (bonus or {}).get("BonusStoreRemainingDurationInSeconds")),
        "bundles": listed,
        "accessories": accessories,
        "accessories_until": _until(at, store.get("AccessoryStoreRemainingDurationInSeconds")),
        "vp_icon": (currencies.get(VP) or {}).get("displayIcon"),
        "kingdom_icon": (currencies.get(KINGDOM) or {}).get("displayIcon"),
        "missing": found.get("missing") or [],
        "problem_detail": found.get("problem") or "",
    }


def featured(bundles: list, content: Content, lang: str) -> list[dict]:
    """HenrikDev のおすすめバンドル（henrik.Henrik.featured）に、訪問者の言語でのゲームの名前を付ける。"""
    known = content.index("bundles", lang)
    listed = []
    for bundle in bundles or []:
        info = known.get(lower_id(bundle.get("uuid"))) or {}
        things = [
            item(FEATURED_KINDS.get(entry.get("type"), ""), entry.get("uuid"), content, lang, entry)
            | {"price": entry.get("price") or 0, "base": entry.get("base") or 0, "amount": entry.get("amount") or 1}
            for entry in bundle.get("items") or []
        ]
        listed.append(
            {
                "name": info.get("displayName") or "",
                "image": info.get("displayIcon2") or info.get("displayIcon"),
                "price": bundle.get("price") or sum(thing["price"] for thing in things),
                "base": sum(thing["base"] for thing in things),
                "until": bundle.get("until") or 0,
                "items": things,
            }
        )
    return listed
