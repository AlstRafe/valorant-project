"""試合以外のゲームまわりのページ: Riot のニュース（/news。 Riot 公式サイトから＝riotweb.py、だめなら HenrikDev）、
eスポーツの予定と結果（/esports。予定は Riot の eスポーツサイト、だめなら HenrikDev。結果と進行中の試合は HenrikDev）、
このサイトが見た試合でのエージェントのピック率・勝率（/agents/stats、activity.py）。どれも全員共通で、
訪問者の言語で出す。"""

from __future__ import annotations

from datetime import UTC, datetime

from flask import Flask, current_app, g, render_template, request, url_for

from valtracker import security
from valtracker.activity import BANDS, FEW, WINDOW_DAYS
from valtracker.henrik import HenrikError
from valtracker.riotweb import RiotWebError
from valtracker.tools import moment_info
from valtracker.util import as_dict
from valtracker.views import QUEUES, band_name, failure_info, tr

#: こちらの言語ごとの Riot ニュースサイトの言語。
NEWS_LANGS = {"ja": "ja-jp", "ko": "ko-kr", "en-GB": "en-gb", "zh-TW": "zh-tw"}
#: Riot サイトでのニュースの種類 -> 表示名（訳す）。ほかの種類は来たまま出す。
CATEGORIES = {
    "patch_notes": "パッチノート",
    "game_updates": "アップデート",
    "dev": "開発",
    "esports": "eスポーツ",
    "announcements": "お知らせ",
    "community": "コミュニティ",
    "riotstore": "Riot ストア",
}
NEWS_SHOWN = 60
#: eスポーツのページ: 予定（と進行中）か、結果か。
SHOWS = ("upcoming", "results")
#: 結果・予定を出す最大数。
GAMES_SHOWN = 80


#: エージェントの数字を集めるキュー（5 人チームで勝ち負けがあるもの）。
STAT_QUEUES = ("competitive", "unrated", "swiftplay", "premier")


def _agent_stats() -> dict:
    content = current_app.config["VAL_CONTENT"]
    queue = request.args.get("queue", "competitive")
    queue = queue if queue in STAT_QUEUES else "competitive"
    bands = {name: (low, high) for name, low, high in BANDS}
    chosen_band = request.args.get("band", "")
    chosen_band = chosen_band if chosen_band in bands and queue == "competitive" else ""
    chosen_map = request.args.get("map", "").lower()[:40]
    found = current_app.config["VAL_ACTIVITY"].agents(queue, chosen_band, chosen_map)
    known = content.map_index(g.lang)
    # 見た試合のマップ（valorant-api.com の uuid）
    maps = {key: known[key] for key in found["maps"] if key in known}
    if chosen_map not in maps:
        chosen_map = ""
        found = current_app.config["VAL_ACTIVITY"].agents(queue, chosen_band, "")
    agents = content.agent_index(g.lang)
    rows = []
    for item in found["agents"]:
        agent = agents.get(item["agent"])
        if agent:
            rows.append(
                item
                | {
                    "name": agent.get("displayName") or "",
                    "icon": agent.get("displayIcon") or "",
                    "role": as_dict(agent.get("role")).get("displayName") or "",
                    "uuid": item["agent"],
                    "few": item["picks"] < FEW,
                }
            )
    sort = request.args.get("sort", "pick")
    sort = sort if sort in ("pick", "win") else "pick"
    if sort == "win":
        rows.sort(key=lambda row: (row["few"], -row["win"], -row["picks"]))
    band_names = [{"key": name, "name": band_name(name)} for name in bands]
    return {
        "rows": rows,
        "matches": found["matches"],
        "queue": queue,
        "queues": [{"key": key, "name": tr(QUEUES[key])} for key in STAT_QUEUES],
        "band": chosen_band,
        "bands": band_names,
        "map": chosen_map,
        "maps": sorted(
            ({"key": key, "name": item.get("displayName") or ""} for key, item in maps.items()),
            key=lambda item: item["name"],
        ),
        "sort": sort,
        "few": FEW,
        "days": WINDOW_DAYS,
        "top_pick": max((row["pick"] for row in rows), default=0),
    }


def _category(key: str) -> str:
    return tr(CATEGORIES[key]) if key in CATEGORIES else key.replace("_", " ").title()


def _news_page() -> tuple[dict, int]:
    chosen = request.args.get("category", "")
    words = request.args.get("q", "").strip()[:60]
    values: dict = {"articles": [], "categories": [], "category": "", "q": words, "patch": None, "problem": ""}
    henrik = current_app.config["VAL_HENRIK"]
    country = NEWS_LANGS.get(g.lang, "en-gb")
    try:
        found = current_app.config["VAL_RIOT_WEB"].news(country)
    except RiotWebError:
        found = None  # Riot 公式の一覧が来なかった: HenrikDev の写し
    try:
        try:
            if found is None:
                found = henrik.news(country)
        except HenrikError as error:
            # Riot サイト（や HenrikDev）にニュースが無い言語: 英語版を出し、ページにそう書く
            if error.kind not in ("down", "notfound") or country == "en-gb":
                raise
            found = henrik.news("en-gb")
            values["english"] = True
    except HenrikError as error:
        code, values["problem"], values["settings_link"] = failure_info(error.kind)
        return values, code
    counts: dict[str, int] = {}
    for item in found:
        if item["category"]:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
    order = list(CATEGORIES)
    keys = sorted(counts, key=lambda key: (order.index(key) if key in order else len(order), key))
    values["categories"] = [
        {
            "name": _category(key),
            "url": url_for("news", category=key, q=words or None),
            "current": key == chosen,
        }
        for key in keys
    ]
    values["category"] = chosen if chosen in counts else ""
    values["every_url"] = url_for("news", q=words or None)
    shown = []
    for item in found:
        if values["category"] and item["category"] != values["category"]:
            continue
        if words and words.lower() not in item["title"].lower():
            continue
        shown.append(item | {"when": moment_info(item["at"]), "category_name": _category(item["category"])})
    values["articles"] = shown[:NEWS_SHOWN]
    patches = [item for item in found if item["category"] == "patch_notes"]
    if patches and not values["category"] and not words:
        values["patch"] = patches[0] | {"when": moment_info(patches[0]["at"])}
    return values, 200


def _esports_page() -> tuple[dict, int]:
    show = request.args.get("show", "upcoming")
    show = show if show in SHOWS else "upcoming"
    chosen = request.args.get("league", "").lower()
    values: dict = {"show": show, "games": [], "leagues": [], "league": "", "live": 0, "problem": ""}
    found = None
    if show == "upcoming":  # まず Riot 公式の一覧（HenrikDev の呼び出し回数を使わない）
        try:
            found = current_app.config["VAL_RIOT_WEB"].esports(g.lang)
        except RiotWebError:
            found = None
    if found is None:
        try:
            found, since = current_app.config["VAL_HENRIK"].esports()
        except HenrikError as error:
            if show == "results" and error.kind not in ("nokey", "badkey"):
                # 予定（Riot）は出せる: エラーにせず、結果がどこにあるかをページに書く
                values["no_results"] = True
                values["detail"] = error.detail if security.is_owner() else ""
                found, since = [], None
            else:
                code, values["problem"], values["settings_link"] = failure_info(error.kind)
                if error.kind == "down":
                    values["problem"] = tr("eスポーツの日程を読み込めませんでした。しばらくしてからお試しください。")
                values["detail"] = error.detail if security.is_owner() else ""
                return values, code
        if since:  # 新しい予定が来なかった: 前回のものを、いつのものか添えて出す
            values["since"] = moment_info(datetime.fromtimestamp(since, UTC).isoformat())
    counts: dict[str, list] = {}
    for item in found:
        if item["league"]["id"]:
            counts.setdefault(item["league"]["id"], [0, item["league"]["name"]])[0] += 1
    values["league"] = chosen if chosen in counts else ""
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1][0], pair[1][1]))
    values["leagues"] = [
        {
            "id": key,
            "name": name,
            "url": url_for("esports", show=show if show != "upcoming" else None, league=key),
            "current": key == values["league"],
        }
        for key, (_, name) in ranked
    ]
    values["every_url"] = url_for("esports", show=show if show != "upcoming" else None)
    values["show_urls"] = {
        key: url_for("esports", show=key if key != "upcoming" else None, league=values["league"] or None)
        for key in SHOWS
    }
    picked = [item for item in found if not values["league"] or item["league"]["id"] == values["league"]]
    if show == "results":
        games = [item for item in picked if item["state"] == "completed"][::-1]
    else:
        games = [item for item in picked if item["state"] == "inProgress"]
        games += [item for item in picked if item["state"] not in ("completed", "inProgress")]
    values["live"] = sum(1 for item in picked if item["state"] == "inProgress")
    values["games"] = [item | {"when": moment_info(item["at"])} for item in games[:GAMES_SHOWN]]
    return values, 200


def register(app: Flask) -> None:
    @app.get("/news")
    def news():
        values, code = _news_page()
        return render_template("news.html", **values), code

    @app.get("/agents/stats")
    def agent_stats():
        return render_template("agent_stats.html", **_agent_stats())

    @app.get("/esports")
    def esports():
        values, code = _esports_page()
        return render_template("esports.html", **values), code
