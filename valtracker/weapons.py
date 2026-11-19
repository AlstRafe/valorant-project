"""武器のページ（/weapons、/weapons/<uuid>）: ゲームの武器の値段と性能（連射・マガジン・リロード・壁抜き・
距離ごとのダメージ）、そこから計算するキルに必要な弾数と TTK（キルまでの時間）。

数字は valorant-api.com の /weapons（ゲームのファイルから読んだもの。Riot は武器の性能を API で出さない）。
content.py が ``arsenal`` に使う部分だけ残す。アーマーはダメージを全部受けるので、体力 100 に足したものとして数える。"""

from __future__ import annotations

import math

from flask import Flask, abort, current_app, g, render_template, request, url_for

from valtracker.util import as_dict
from valtracker.views import tr

#: ゲームのストアの順（安い順）。近接武器には性能が無いので出さない。
CATEGORIES = ("Sidearm", "SMG", "Shotgun", "Rifle", "Sniper", "Heavy")
CATEGORY_NAMES = {
    "Sidearm": "サイドアーム",
    "SMG": "SMG",
    "Shotgun": "ショットガン",
    "Rifle": "ライフル",
    "Sniper": "スナイパーライフル",
    "Heavy": "マシンガン",
}
#: 体力 + アーマー: アーマーなし、ライトシールド（とリジェンシールド）、ヘビーシールド。
HEALTH = (100, 125, 150)
#: 一覧と比べる表で使う相手: フルアーマー。
FULL = 150
WALLS = {"Low": "低", "Medium": "中", "High": "高"}
#: 右クリック（サブの撃ち方）の種類 -> 名前。知らないものは出さない。
ALTS = {"ADS": "覗き込み", "Shotgun": "散弾", "AirBurst": "エアバースト"}
FEATURES = {"ROFIncrease": "撃ち続けると連射が上がる", "DualZoom": "2 段階ズーム", "Silenced": "サイレンサー付き"}
MODES = {"SemiAutomatic": "セミオート"}
PARTS = (("head", "頭"), ("body", "体"), ("leg", "脚"))


def _number(value: float) -> str:
    """ダメージや秒: 整数ならそのまま、ほかは小数 2 桁まで（34.0 -> "34"、18.7 -> "18.7"、9.75 -> "9.75"）。"""
    return f"{round(float(value), 2):.2f}".rstrip("0").rstrip(".")


def shots(health: float, damage: float) -> int:
    """体力 ``health`` を倒すのに要る弾の数（0: 当たってもダメージが無い）。"""
    if damage <= 0:
        return 0
    return max(1, math.ceil(health / damage - 1e-9))


def ttk(count: int, rate: float) -> int | None:
    """``count`` 発で倒すまでのミリ秒（最初の弾は 0 秒、あとは連射の間隔ごと）。連射がわからなければ None。"""
    if count <= 0 or rate <= 0:
        return None
    return round(1000 * (count - 1) / rate)


def _bands(weapon: dict) -> list[dict]:
    """距離ごとのダメージと、体力ごとのキルに必要な弾数と TTK。ショットガンは 1 発の全ペレットが当たったとして。"""
    stats = as_dict(weapon.get("stats"))
    rate = float(stats.get("fireRate") or 0)
    pellets = max(1, int(stats.get("shotgunPelletCount") or 1))
    found = []
    for start, end, head, body, leg in stats.get("ranges") or []:
        damage = {"head": head, "body": body, "leg": leg}
        kills = {
            part: [
                {"health": health, "shots": (n := shots(health, damage[part] * pellets)), "ms": ttk(n, rate)}
                for health in HEALTH
            ]
            for part, _ in PARTS
        }
        found.append(
            {
                "range": f"{_number(start)}–{_number(end)} m",
                "damage": {part: _number(value) for part, value in damage.items()},
                "kills": kills,
            }
        )
    return found


def _card(weapon: dict) -> dict:
    """一覧と比べる表の 1 行: 近い距離での頭・体・脚、フルアーマーへの体の弾数と TTK。"""
    stats = as_dict(weapon.get("stats"))
    bands = _bands(weapon)
    near = bands[0] if bands else None
    body = near["kills"]["body"][HEALTH.index(FULL)] if near else None
    head = near["kills"]["head"][HEALTH.index(FULL)] if near else None
    return {
        "uuid": weapon["uuid"],
        "name": str(weapon.get("displayName") or ""),
        "icon": str(weapon.get("displayIcon") or ""),
        "category": weapon.get("category") or "",
        "cost": int(weapon.get("cost") or 0),
        "rate": _number(stats["fireRate"]) if stats.get("fireRate") else "",
        "magazine": int(stats.get("magazineSize") or 0),
        "pellets": int(stats.get("shotgunPelletCount") or 1),
        "damage": near["damage"] if near else None,
        "body": body,
        "head": head,
    }


def _arsenal() -> list[dict]:
    """性能のわかる武器（ゲームのストアの順）。"""
    found = [
        item | {"uuid": key}
        for key, item in current_app.config["VAL_CONTENT"].arsenal(g.lang).items()
        if isinstance(item.get("stats"), dict)
        and item.get("category") in CATEGORIES
        and item.get("stats", {}).get("ranges")
    ]
    found.sort(
        key=lambda item: (CATEGORIES.index(item["category"]), int(item.get("cost") or 0), item.get("displayName") or "")
    )
    return found


def _list_page() -> dict:
    weapons = [_card(item) for item in _arsenal()]
    groups = []
    for key in CATEGORIES:
        listed = [item for item in weapons if item["category"] == key]
        if listed:
            groups.append({"key": key, "name": tr(CATEGORY_NAMES[key]), "items": listed})
    return {"groups": groups, "full": FULL}


def _facts(weapon: dict) -> list[dict]:
    """武器のページの上の数字（あるものだけ）。"""
    stats = as_dict(weapon.get("stats"))
    ads = as_dict(stats.get("ads"))
    facts = [{"name": tr("価格"), "value": f"{int(weapon.get('cost') or 0):,}", "unit": tr("クレジット")}]
    if stats.get("fireRate"):
        facts.append({"name": tr("連射速度"), "value": _number(stats["fireRate"]), "unit": tr("発/秒")})
    if stats.get("magazineSize"):
        facts.append({"name": tr("マガジン"), "value": str(int(stats["magazineSize"])), "unit": tr("発")})
    if stats.get("reloadTimeSeconds"):
        facts.append({"name": tr("リロード"), "value": _number(stats["reloadTimeSeconds"]), "unit": tr("秒")})
    if stats.get("equipTimeSeconds"):
        facts.append({"name": tr("持ち替え"), "value": _number(stats["equipTimeSeconds"]), "unit": tr("秒")})
    if stats.get("wall") in WALLS:
        facts.append({"name": tr("壁抜き"), "value": tr(WALLS[stats["wall"]]), "unit": ""})
    if stats.get("firstBulletAccuracy") is not None:
        facts.append({"name": tr("初弾の拡散"), "value": f"{stats['firstBulletAccuracy']:.2f}°", "unit": ""})
    if ads.get("zoomMultiplier") and stats.get("alt") == "ADS":
        facts.append({"name": tr("ズーム"), "value": f"×{_number(ads['zoomMultiplier'])}", "unit": ""})
    if int(stats.get("shotgunPelletCount") or 1) > 1:
        facts.append({"name": tr("ペレット"), "value": str(int(stats["shotgunPelletCount"])), "unit": tr("1 発あたり")})
    return facts


def _traits(weapon: dict) -> list[str]:
    """撃ち方の特徴（わかるものだけ）。"""
    stats = as_dict(weapon.get("stats"))
    ads = as_dict(stats.get("ads"))
    traits = []
    if stats.get("mode") in MODES:
        traits.append(tr(MODES[stats["mode"]]))
    if stats.get("alt") in ALTS:
        traits.append(tr("右クリック: {kind}", kind=tr(ALTS[stats["alt"]])))
    if int(ads.get("burstCount") or 1) > 1:
        traits.append(tr("覗き込みで {count} 点バースト", count=int(ads["burstCount"])))
    if ads.get("fireRate") and stats.get("fireRate") and stats.get("alt") == "ADS":
        traits.append(tr("覗き込み中の連射 {rate} 発/秒", rate=_number(ads["fireRate"])))
    if stats.get("feature") in FEATURES:
        traits.append(tr(FEATURES[stats["feature"]]))
    return traits


def _versus(mine: dict, theirs: dict) -> list[dict]:
    """2 つの武器の比べる表: ``{"name", "mine", "theirs", "better": "mine"/"theirs"/""}``。ダメージは近い距離の 1 発分。"""

    def hit(card: dict, part: str) -> float | None:
        return float(card["damage"][part]) * card["pellets"] if card["damage"] else None

    def field(card: dict, key: str, part: str) -> int | None:
        return card[key][part] if card[key] else None

    rows = [
        (tr("価格"), mine["cost"], theirs["cost"], "low", ""),
        (tr("連射速度"), float(mine["rate"] or 0) or None, float(theirs["rate"] or 0) or None, "high", ""),
        (tr("マガジン"), mine["magazine"] or None, theirs["magazine"] or None, "high", ""),
        (tr("頭"), hit(mine, "head"), hit(theirs, "head"), "high", ""),
        (tr("体"), hit(mine, "body"), hit(theirs, "body"), "high", ""),
        (tr("体の弾数（150）"), field(mine, "body", "shots"), field(theirs, "body", "shots"), "low", ""),
        (tr("TTK（体・150）"), field(mine, "body", "ms"), field(theirs, "body", "ms"), "low", "ms"),
        (tr("頭の弾数（150）"), field(mine, "head", "shots"), field(theirs, "head", "shots"), "low", ""),
    ]
    found = []
    for name, left, right, good, unit in rows:
        better = ""
        if left is not None and right is not None and left != right:
            better = "mine" if (left < right) == (good == "low") else "theirs"
        found.append(
            {
                "name": name,
                "mine": "–" if left is None else (f"{left:,}" if isinstance(left, int) else _number(left)),
                "theirs": "–" if right is None else (f"{right:,}" if isinstance(right, int) else _number(right)),
                "better": better,
                "ms": unit == "ms",
            }
        )
    return found


def _weapon_page(uuid: str) -> dict:
    weapons = _arsenal()
    weapon = next((item for item in weapons if item["uuid"] == uuid), None)
    if weapon is None:
        abort(404)
    other = request.args.get("vs", "").lower()[:40]
    rival = next((item for item in weapons if item["uuid"] == other and other != uuid), None)
    card = _card(weapon)
    return {
        "weapon": card,
        "category_name": tr(CATEGORY_NAMES[weapon["category"]]),
        "facts": _facts(weapon),
        "traits": _traits(weapon),
        "bands": _bands(weapon),
        "parts": [(key, tr(name)) for key, name in PARTS],
        "health": HEALTH,
        "full": FULL,
        "rival": _card(rival) if rival else None,
        "versus": _versus(card, _card(rival)) if rival else [],
        "choices": [
            {"uuid": item["uuid"], "name": str(item.get("displayName") or "")}
            for item in weapons
            if item["uuid"] != uuid
        ],
        "skins_url": url_for("skins", weapon=uuid),
    }


def register(app: Flask) -> None:
    @app.get("/weapons")
    def weapons():
        return render_template("weapons.html", **_list_page())

    @app.get("/weapons/<uuid>")
    def weapon(uuid: str):
        return render_template("weapon.html", **_weapon_page(uuid.lower()[:40]))
