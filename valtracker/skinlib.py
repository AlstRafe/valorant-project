"""スキン図鑑: ゲームの全武器スキン（/skins）を武器・ティア・名前で探せ、スキンごとのページ（/skins/<uuid>）
にはバリアント（クロマ）、レベルとその動画、同じシリーズのスキン、訪問者の評価（ratings.py）を出す。全員共通、
訪問者の言語で、valorant-api.com から（content.py）。"""

from __future__ import annotations

import re
import secrets

from flask import Flask, abort, current_app, g, redirect, render_template, request, url_for

from valtracker import security
from valtracker.collection import TIERS, tier_info, weapon_order
from valtracker.content import Content, variant_name
from valtracker.util import lower_id
from valtracker.views import tr

#: 訪問者が何も選んでいないときに出す武器（スキンが一番買われる武器）。
VANDAL = "9c82e19d-4575-0200-1a81-3eacf00cf872"
#: 見るスキンが無いシリーズ: 武器の標準の見た目と「Random Favorite Skin」。
NOT_SKINS = {"5a629df4-4765-0214-bd40-fbb96542941f", "0d7a5bfb-4850-098e-1821-d989bbfd58a8"}
#: レベルで増えるもの（valorant-api.com の levelItem の "::" の後）-> 名前（訳す）。
LEVEL_ITEMS = {
    "VFX": "エフェクト",
    "Animation": "アニメーション",
    "Finisher": "フィニッシャー",
    "SoundEffects": "サウンド",
    "KillCounter": "キルカウンター",
    "InspectAndKill": "インスペクトとキル演出",
    "AttackerDefenderSwap": "攻守で変わる見た目",
    "TopFrag": "トップフラグの演出",
    "KillBanner": "キルバナー",
    "KillEffect": "キル演出",
    "Transformation": "変形",
    "FishAnimation": "アニメーション",
}
#: 1 ページに並べるスキンの最大数（全武器の検索ではもっと見つかることがあり、そのときは絞るようページに出す）。
SHOWN = 160
VOTER_COOKIE = "val_voter"
_VOTER = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _words(value) -> str:
    r"""valorant-api.com の名前には改行が入ることがある（クロマの "Variant 1\nRed"）。"""
    return " ".join(str(value or "").split())


def _picture(skin: dict) -> str:
    chromas = skin.get("chromas") or []
    return (
        skin.get("displayIcon")
        or next((level.get("displayIcon") for level in skin.get("levels") or [] if level.get("displayIcon")), "")
        or next((part.get("fullRender") for part in chromas if part.get("fullRender")), "")
        or ""
    )


def _is_skin(weapon: dict, skin: dict) -> bool:
    return (
        lower_id(skin.get("uuid")) != lower_id(weapon.get("defaultSkinUuid"))
        and lower_id(skin.get("themeUuid")) not in NOT_SKINS
        and bool(_picture(skin))
    )


def library(content: Content, lang: str) -> list[dict]:
    """全スキン（ゲームの武器の順、良いティアから）
    : ``[{"uuid", "name", "image", "tier", "weapon": {"uuid", "name"}, "theme", "videos"}]``。"""
    tiers = content.index("contenttiers", lang)
    found = []
    for weapon in sorted(content.weapons(lang), key=weapon_order):
        about = {"uuid": lower_id(weapon.get("uuid")), "name": weapon.get("displayName") or ""}
        skins = [skin for skin in weapon.get("skins") or [] if _is_skin(weapon, skin)]
        entries = [
            {
                "uuid": lower_id(skin.get("uuid")),
                "name": _words(skin.get("displayName")),
                "image": _picture(skin),
                "tier": tier_info(skin, tiers),
                "weapon": about,
                "theme": lower_id(skin.get("themeUuid")),
                "videos": sum(
                    1 for part in (skin.get("levels") or []) + (skin.get("chromas") or []) if part.get("streamedVideo")
                ),
            }
            for skin in skins
        ]
        entries.sort(key=lambda entry: (-entry["tier"]["order"], entry["name"]))
        found += entries
    return found


def _list_page(content: Content, lang: str) -> dict:
    every = library(content, lang)
    weapons: dict[str, str] = {}
    for entry in every:
        weapons.setdefault(entry["weapon"]["uuid"], entry["weapon"]["name"])
    words = _words(request.args.get("q", ""))[:40]
    tier = request.args.get("tier", "")
    tier = tier if tier in {name.lower() for name in TIERS} else ""
    weapon = lower_id(request.args.get("weapon", ""))
    if weapon != "all" and weapon not in weapons:
        weapon = "all" if words or tier else (VANDAL if VANDAL in weapons else next(iter(weapons), "all"))
    picked = [
        entry
        for entry in every
        if (weapon == "all" or entry["weapon"]["uuid"] == weapon)
        and (not tier or entry["tier"]["key"] == tier)
        and (not words or words.lower() in entry["name"].lower())
    ]
    wants = set(current_app.config["VAL_DIARY"].wants()) if security.is_owner() else set()
    if request.args.get("wants") and wants:
        picked = [entry for entry in every if entry["uuid"] in wants]
        weapon = "wants"
    groups: list[dict] = []
    for entry in picked[:SHOWN]:
        if not groups or groups[-1]["uuid"] != entry["weapon"]["uuid"]:
            groups.append({**entry["weapon"], "items": []})
        groups[-1]["items"].append(
            entry | {"url": url_for("skin", uuid=entry["uuid"]), "wanted": entry["uuid"] in wants}
        )
    known = content.index("contenttiers", lang)
    tiers = []
    for item in sorted(
        known.values(), key=lambda item: TIERS.index(item["devName"]) if item.get("devName") in TIERS else 9
    ):
        if item.get("devName") in TIERS and all(other["key"] != item["devName"].lower() for other in tiers):
            tiers.append({"key": item["devName"].lower(), "name": item.get("displayName") or item["devName"]})
    ratings = current_app.config["VAL_RATINGS"]
    by_uuid = {entry["uuid"]: entry for entry in every}
    top = [
        by_uuid[skin] | {"url": url_for("skin", uuid=skin), "average": average, "count": count}
        for skin, average, count in ratings.top(8)
        if skin in by_uuid
    ]
    return {
        "groups": groups,
        "total": len(every),
        "found": len(picked),
        "more": max(0, len(picked) - SHOWN),
        "weapons": [{"uuid": key, "name": name} for key, name in weapons.items()],
        "weapon": weapon,
        "tiers": tiers,
        "tier": tier,
        "q": words,
        "top": top if not (words or tier or request.args.get("weapon") or weapon == "wants") else [],
        "wants": len(wants),
    }


def _skin_page(content: Content, lang: str, uuid: str) -> dict | None:
    found = content.skins(lang).get(uuid)
    if not found:
        return None
    weapon, skin = found
    if lower_id(skin.get("uuid")) != uuid:
        return None  # レベルやクロマの uuid: リンク先はスキン自身のページ
    if not _is_skin(weapon, skin):
        return None
    name = _words(skin.get("displayName"))
    tiers = content.index("contenttiers", lang)
    chosen = lower_id(request.args.get("chroma", ""))
    chromas = []
    for number, part in enumerate(skin.get("chromas") or []):
        chromas.append(
            {
                "uuid": lower_id(part.get("uuid")),
                "name": tr("標準") if number == 0 else variant_name(part.get("displayName"), name),
                "swatch": part.get("swatch") or "",
                "image": part.get("fullRender") or part.get("displayIcon") or "",
                "video": part.get("streamedVideo") or "",
                "url": url_for("skin", uuid=uuid, chroma=None if number == 0 else lower_id(part.get("uuid"))),
            }
        )
    current = next((part for part in chromas if part["uuid"] == chosen), chromas[0] if chromas else None)
    if current:
        current["current"] = True
    levels = []
    for number, part in enumerate(skin.get("levels") or [], 1):
        item = str(part.get("levelItem") or "").rsplit("::", 1)[-1]
        # "Divergence Vandal Level 3\nWeapon visually transforms every round ...": 名前の後ろが効果の説明
        lines = [line.strip() for line in str(part.get("displayName") or "").splitlines() if line.strip()]
        levels.append(
            {
                "number": number,
                "item": tr(LEVEL_ITEMS[item]) if item in LEVEL_ITEMS else "",
                "about": " ".join(lines[1:])[:200],
                "video": part.get("streamedVideo") or "",
                "poster": part.get("displayIcon") or "",
            }
        )
    themes = content.index("themes", lang)
    theme = lower_id(skin.get("themeUuid"))
    series = [
        entry | {"url": url_for("skin", uuid=entry["uuid"])}
        for entry in library(content, lang)
        if theme and entry["theme"] == theme and entry["uuid"] != uuid
    ]
    picture = (current or {}).get("image") or _picture(skin)
    return {
        "uuid": uuid,
        "name": name,
        "weapon": {
            "name": weapon.get("displayName") or "",
            "url": url_for("skins", weapon=lower_id(weapon.get("uuid"))),
        },
        "tier": tier_info(skin, tiers),
        "theme": _words((themes.get(theme) or {}).get("displayName")),
        "image": picture,
        "chromas": chromas if len(chromas) > 1 else [],
        "chroma_video": (current or {}).get("video") if len(chromas) > 1 else "",
        "levels": levels,
        "series": series[:24],
        "score": current_app.config["VAL_RATINGS"].score(uuid, _voter_cookie()),
        "rated": request.args.get("rated", ""),
        "wanted": security.is_owner() and uuid in current_app.config["VAL_DIARY"].wants(),
    }


def _voter_cookie() -> str:
    value = request.cookies.get(VOTER_COOKIE, "")
    return value if _VOTER.match(value) else ""


def register(app: Flask) -> None:
    @app.get("/skins")
    def skins():
        return render_template("skins.html", **_list_page(current_app.config["VAL_CONTENT"], g.lang))

    @app.get("/skins/<uuid>")
    def skin(uuid: str):
        uuid = uuid.lower()
        values = _skin_page(current_app.config["VAL_CONTENT"], g.lang, uuid) if _UUID.match(uuid) else None
        if values is None:
            abort(404)
        return render_template("skin.html", **values)

    @app.post("/skins/<uuid>/want")
    def want_skin(uuid: str):
        """持ち主のほしい物リスト（diary.py）: ストアに出たら印を付ける。"""
        security.owner_only()
        uuid = uuid.lower()
        if not _UUID.match(uuid) or _skin_page(current_app.config["VAL_CONTENT"], g.lang, uuid) is None:
            abort(404)
        current_app.config["VAL_DIARY"].want(uuid, request.form.get("on") == "1")
        return redirect(url_for("skin", uuid=uuid) + "#want", 303)

    @app.post("/skins/<uuid>/rate")
    def rate_skin(uuid: str):
        uuid = uuid.lower()
        content = current_app.config["VAL_CONTENT"]
        if not _UUID.match(uuid) or _skin_page(content, g.lang, uuid) is None:
            abort(404)
        try:
            stars = int(request.form.get("stars", ""))
        except ValueError:
            stars = 0
        if not 1 <= stars <= 5:
            abort(400)
        cookie = _voter_cookie() or secrets.token_urlsafe(24)
        kept = current_app.config["VAL_RATINGS"].rate(uuid, cookie, stars)
        response = redirect(url_for("skin", uuid=uuid, rated="1" if kept else "busy") + "#rate", 303)
        response.set_cookie(
            VOTER_COOKIE,
            cookie,
            max_age=2 * 365 * 86400,
            path=(request.script_root or "") + "/",
            secure=request.is_secure,
            httponly=True,
            samesite="Lax",
        )
        return response
