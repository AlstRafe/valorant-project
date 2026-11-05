"""The store page: the featured bundles everyone sees (HenrikDev), and for the owner today's store from their own Riot
account (riot.Riot.shop): the four skins of the day, the night market, the bundles and the accessories, each with
its price and the time left, and what is already owned marked. Names and pictures come from content.py.
"""

from __future__ import annotations

import math

from valtracker.collection import _tier, _wallet
from valtracker.content import Content
from valtracker.riot import ITEM_TYPES

VP = "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"
KINGDOM = "85ca954a-41f2-ce94-9b45-8ca3dd39a00d"
#: PD's item types -> our kinds (riot.ITEM_TYPES the other way round).
KINDS = {uuid: kind for kind, uuid in ITEM_TYPES.items()}
#: HenrikDev's names of the kinds in a featured bundle -> ours.
FEATURED_KINDS = {
    "skin_level": "skins",
    "skin_chroma": "chromas",
    "buddy": "buddies",
    "player_card": "cards",
    "spray": "sprays",
    "player_title": "titles",
    "flex": "flex",
}
#: Our kinds -> valorant-api.com's list (content.py) and what of an item is its picture.
LISTS = {
    "buddies": ("buddies", "displayIcon"),
    "cards": ("playercards", "wideArt"),
    "sprays": ("sprays", "fullTransparentIcon"),
    "titles": ("playertitles", None),
    "flex": ("flex", "displayIcon"),
}


def _id(value) -> str:
    return str(value or "").lower()


def _number(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def item(kind: str, uuid: str, content: Content, lang: str, said: dict | None = None) -> dict:
    """One thing on sale: ``{"kind", "name", "image", "tier"}`` (HenrikDev's own words, ``said``, when
    valorant-api.com does not know it)."""
    uuid, said = _id(uuid), said or {}
    if kind in ("skins", "chromas"):
        found = content.skins(lang).get(uuid)
        if found:
            weapon, skin = found
            level = next((part for part in skin.get("levels") or [] if _id(part.get("uuid")) == uuid), {})
            chroma = next((part for part in skin.get("chromas") or [] if _id(part.get("uuid")) == uuid), {})
            picture = (
                chroma.get("fullRender")
                or skin.get("displayIcon")
                or level.get("displayIcon")
                or next((part.get("fullRender") for part in skin.get("chromas") or [] if part.get("fullRender")), "")
            )
            tier = _tier(skin, content.index("contenttiers", lang))
            return {"kind": "skins", "name": skin.get("displayName") or "", "image": picture, "tier": tier}
    if kind in LISTS:
        listed, picture = LISTS[kind]
        known = content.index(listed, lang).get(uuid) or {}
        if known:
            name = known.get("titleText") if kind == "titles" else known.get("displayName")
            image = (known.get(picture) or known.get("displayIcon")) if picture else None
            return {"kind": kind, "name": name or said.get("name") or "", "image": image, "tier": None}
    return {"kind": kind, "name": said.get("name") or "", "image": said.get("image") or None, "tier": None}


def _reward(offer: dict) -> tuple[str, str, int]:
    """An offer's first reward: (our kind, its uuid, how many)."""
    rewards = [reward for reward in (offer or {}).get("Rewards") or [] if isinstance(reward, dict)]
    if not rewards:
        return "", "", 0
    first = rewards[0]
    return KINDS.get(_id(first.get("ItemTypeID")), ""), _id(first.get("ItemID")), _number(first.get("Quantity")) or 1


def _owned(kind: str, uuid: str, owned: dict) -> bool:
    return bool(owned) and uuid in (owned.get(kind) or {})


def _until(at: float, seconds) -> float:
    return at + _number(seconds) if _number(seconds) > 0 else 0


def left(seconds: float) -> dict:
    """``{"hours", "minutes"}`` left (rounded up to the minute)."""
    minutes = max(0, math.ceil(seconds / 60))
    return {"hours": minutes // 60, "minutes": minutes % 60}


def page(found: dict, content: Content, lang: str, owned: dict | None = None) -> dict:
    """What shop.html shows of the owner's store (riot.Riot.shop); ``owned`` (the collection's) marks what they
    have already."""
    front = found.get("storefront") if isinstance(found.get("storefront"), dict) else {}
    at = float(found.get("at") or 0)
    owned = owned or {}
    panel = front.get("SkinsPanelLayout") if isinstance(front.get("SkinsPanelLayout"), dict) else {}
    daily = []
    offers = [offer for offer in panel.get("SingleItemStoreOffers") or [] if isinstance(offer, dict)]
    for offer in offers:
        kind, uuid, _ = _reward(offer)
        daily.append(item(kind or "skins", uuid, content, lang) | {"price": _number((offer.get("Cost") or {}).get(VP))})
    if not offers:  # an older answer: the skins' uuids without their prices
        daily = [item("skins", uuid, content, lang) | {"price": 0} for uuid in panel.get("SingleItemOffers") or []]
    night = None
    bonus = front.get("BonusStore") if isinstance(front.get("BonusStore"), dict) else None
    if bonus:
        night = []
        for offer in bonus.get("BonusStoreOffers") or []:
            if not isinstance(offer, dict):
                continue
            kind, uuid, _ = _reward(offer.get("Offer"))
            night.append(
                item(kind or "skins", uuid, content, lang)
                | {
                    "base": _number(((offer.get("Offer") or {}).get("Cost") or {}).get(VP)),
                    "price": _number((offer.get("DiscountCosts") or {}).get(VP)),
                    "discount": _number(offer.get("DiscountPercent")),
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
        info = known.get(_id(bundle.get("DataAssetID"))) or {}
        things = []
        for entry in bundle.get("Items") or []:
            if not isinstance(entry, dict):
                continue
            thing = entry.get("Item") if isinstance(entry.get("Item"), dict) else {}
            kind = KINDS.get(_id(thing.get("ItemTypeID")), "")
            things.append(
                item(kind, _id(thing.get("ItemID")), content, lang)
                | {
                    "price": _number(entry.get("DiscountedPrice")),
                    "base": _number(entry.get("BasePrice")),
                    "amount": _number(thing.get("Amount")) or 1,
                    "owned": _owned(kind, _id(thing.get("ItemID")), owned),
                }
            )
        listed.append(
            {
                "name": info.get("displayName") or "",
                "image": info.get("displayIcon2") or info.get("displayIcon"),
                "price": _number((bundle.get("TotalDiscountedCost") or {}).get(VP)) or sum(t["price"] for t in things),
                "base": _number((bundle.get("TotalBaseCost") or {}).get(VP)),
                "until": _until(at, bundle.get("DurationRemainingInSeconds")),
                "items": things,
            }
        )
    store = front.get("AccessoryStore") if isinstance(front.get("AccessoryStore"), dict) else {}
    accessories = []
    for offer in store.get("AccessoryStoreOffers") or []:
        if not isinstance(offer, dict):
            continue
        kind, uuid, amount = _reward(offer.get("Offer"))
        if kind:
            accessories.append(
                item(kind, uuid, content, lang)
                | {
                    "price": _number(((offer.get("Offer") or {}).get("Cost") or {}).get(KINGDOM)),
                    "owned": _owned(kind, uuid, owned),
                }
            )
    for entry in daily:
        entry["owned"] = False  # the game does not offer what is owned
    currencies = content.index("currencies", lang)
    return {
        "name": found.get("name") or "",
        "tag": found.get("tag") or "",
        "wallet": _wallet(found.get("wallet"), content, lang),
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
    """HenrikDev's featured bundles (henrik.Henrik.featured) with the game's names in the visitor's language."""
    known = content.index("bundles", lang)
    listed = []
    for bundle in bundles or []:
        info = known.get(_id(bundle.get("uuid"))) or {}
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
