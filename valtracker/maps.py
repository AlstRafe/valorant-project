"""マップのページ（/maps、/maps/<uuid>）: ミニマップとコールアウト（場所の名前、訪問者の言語）、このサイトが見た試合での
攻撃側・防衛側のラウンド勝率、サイトごとの設置の割合と設置後の勝率、エージェントのピック率と勝率、よく使われる構成。

マップの絵・コールアウト・座標の変換は valorant-api.com（heatmap.py と同じ）。数字は activity.py の集計（直近
WINDOW_DAYS 日、標本からの推定なので試合数を出す）。射撃場・チームデスマッチ・スカーミッシュのマップ
（サイトもコールアウトも無い）は出さない。"""

from __future__ import annotations

import math

from flask import Flask, abort, current_app, g, render_template, request, url_for

from valtracker import heatmap
from valtracker.activity import FEW, WINDOW_DAYS
from valtracker.util import as_dict, as_list
from valtracker.views import QUEUES, tr
from valtracker.world import STAT_QUEUES, comp_rows

#: マップのページに出すエージェントの数（ピック率の高い順）。
AGENTS_SHOWN = 12


def _queue() -> str:
    queue = request.args.get("queue", "competitive")
    return queue if queue in STAT_QUEUES else "competitive"


def _maps(lang: str) -> list[dict]:
    """サイトのあるマップ（valorant-api.com の一覧の順）: サイトの説明（"A/B Sites"）とミニマップのあるもの。"""
    found = {}
    for item in current_app.config["VAL_CONTENT"].map_index(lang).values():  # uuid とアドレス名の両方で引ける
        if item.get("tacticalDescription") and heatmap.plan(item):
            found.setdefault(str(item["uuid"]).lower(), item)
    return list(found.values())


def _sides(counted: dict | None) -> dict | None:
    """攻守の勝率（FEW 試合から）: ``{"atk", "def"（%）, "rounds", "matches"}``。"""
    if not counted or counted.get("matches", 0) < FEW:
        return None
    won, played = (as_list(counted.get("atk")) + [0, 0])[:2]
    if not played:
        return None
    attack = round(100 * won / played, 1)
    return {"atk": attack, "def": round(100 - attack, 1), "rounds": played, "matches": counted["matches"]}


def _sites(counted: dict | None) -> list[dict]:
    """サイトごとの設置の割合（全部の設置のうち）と、設置後に攻撃側が取った割合。"""
    if not counted or counted.get("matches", 0) < FEW:
        return []
    sites = {key: value for key, value in as_dict(counted.get("sites")).items() if isinstance(value, list) and value}
    planted = sum(value[0] for value in sites.values())
    if not planted:
        return []
    return [
        {
            "site": site,
            "share": round(100 * value[0] / planted, 1),
            "won": round(100 * value[1] / value[0], 1) if value[0] else 0.0,
            "plants": value[0],
        }
        for site, value in sorted(sites.items())
    ]


def _list_page() -> dict:
    queue = _queue()
    counted = current_app.config["VAL_ACTIVITY"].maps(queue)
    played = current_app.config["VAL_ACTIVITY"].agents(queue)["maps"]
    cards = []
    for item in _maps(g.lang):
        key = str(item["uuid"]).lower()
        cards.append(
            {
                "uuid": key,
                "name": item.get("displayName") or "",
                "image": item.get("listViewIcon") or item.get("displayIcon") or "",
                "sites": item.get("tacticalDescription") or "",
                "pool": key in played,
                "games": played.get(key, 0),
                "sides": _sides(as_dict(counted.get("maps")).get(key)),
            }
        )
    # 最近遊ばれているマップ（試合の多い順）が先
    cards.sort(key=lambda card: (not card["pool"], -card["games"], card["name"]))
    return {"cards": cards, "queue": queue, "queues": _queues(), "days": WINDOW_DAYS, "few": FEW}


def _queues() -> list[dict]:
    return [{"key": key, "name": tr(QUEUES[key])} for key in STAT_QUEUES]


def _callouts(item: dict, drawn: dict) -> tuple[list[dict], list[dict]]:
    """ミニマップの上の名前（横・縦の %）と、場所ごとにまとめた一覧（A、B、ミッド…）。"""
    labels, groups = [], {}
    for callout in as_list(item.get("callouts")):
        if not isinstance(callout, dict):
            continue
        where = as_dict(callout.get("location"))
        spot = [where.get("x"), where.get("y")]
        if not all(isinstance(part, (int, float)) and math.isfinite(part) for part in spot):
            continue
        name = str(callout.get("regionName") or "").strip()
        area = str(callout.get("superRegionName") or "").strip()
        place = heatmap.onto(drawn, spot)
        if not name or place is None:
            continue
        kind = str(callout.get("superRegion") or "").rsplit("::", 1)[-1].lower()
        # 端の名前は内側へ伸ばす（真ん中そろえだと絵の外に半分出る）
        edge = " l" if place[0] < 12 else " r" if place[0] > 88 else ""
        kind = (kind if kind in ("a", "b", "c", "mid") else "other") + edge
        labels.append({"name": name, "x": place[0], "y": place[1], "kind": kind})
        groups.setdefault(area or tr("ほか"), []).append(name)
    listed = [{"area": area, "names": sorted(set(names))} for area, names in groups.items()]
    return labels, listed


def _map_page(uuid: str) -> dict:
    item = next((found for found in _maps(g.lang) if str(found["uuid"]).lower() == uuid), None)
    if item is None:
        abort(404)
    queue = _queue()
    activity = current_app.config["VAL_ACTIVITY"]
    content = current_app.config["VAL_CONTENT"]
    counted = as_dict(activity.maps(queue).get("maps")).get(uuid)
    agents = {key: value | {"uuid": key} for key, value in content.agent_index(g.lang).items()}
    picked = activity.agents(queue, "", uuid)
    rows = []
    for found in picked["agents"]:
        agent = agents.get(found["agent"])
        if agent:
            rows.append(
                found
                | {
                    "name": agent.get("displayName") or "",
                    "icon": agent.get("displayIcon") or "",
                    "role": as_dict(agent.get("role")).get("displayName") or "",
                    "uuid": found["agent"],
                    "few": found["picks"] < FEW,
                }
            )
    drawn = heatmap.plan(item)
    labels, areas = _callouts(item, drawn) if drawn else ([], [])
    return {
        "map": {
            "uuid": uuid,
            "name": item.get("displayName") or "",
            "image": item.get("listViewIcon") or "",
            "minimap": drawn["image"] if drawn else "",
            "sites": item.get("tacticalDescription") or "",
            "where": item.get("coordinates") or "",
        },
        "sides": _sides(counted),
        "sites": _sites(counted),
        "matches": picked["matches"],
        "agents": rows[:AGENTS_SHOWN],
        "more_agents": len(rows) > AGENTS_SHOWN,
        "top_pick": max((row["pick"] for row in rows), default=0),
        "comps": comp_rows(activity.comps(queue, uuid), agents),
        "labels": labels,
        "areas": areas,
        "queue": queue,
        "queues": _queues(),
        "days": WINDOW_DAYS,
        "few": FEW,
        "stats_url": url_for("agent_stats", queue=queue if queue != "competitive" else None, map=uuid),
    }


def register(app: Flask) -> None:
    @app.get("/maps")
    def maps():
        return render_template("maps.html", **_list_page())

    @app.get("/maps/<uuid>")
    def map_page(uuid: str):
        return render_template("map.html", **_map_page(uuid.lower()[:40]))
