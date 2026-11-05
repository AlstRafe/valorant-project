"""Pages about the game around the matches: Riot's news (/news) and the esports schedule and results (/esports)
from HenrikDev, and how often each agent is picked and wins (/agents/stats) in the matches this site has seen
(activity.py). Everyone's, in the visitor's language."""

from __future__ import annotations

from flask import Flask, current_app, g, render_template, request, url_for

from valtracker import security
from valtracker.activity import BANDS, FEW, WINDOW_DAYS
from valtracker.henrik import HenrikError
from valtracker.tools import _family, _when
from valtracker.views import QUEUES, _failure, _rank, _t

#: The language of Riot's news site for each of ours.
NEWS_LANGS = {"ja": "ja-jp", "ko": "ko-kr", "en-GB": "en-gb", "zh-TW": "zh-tw"}
#: The news' kinds, as Riot's site files them -> their names (translated); others show as they come.
CATEGORIES = {
    "patch_notes": "パッチノート",
    "game_updates": "アップデート",
    "dev": "開発",
    "esports": "eスポーツ",
    "announcements": "お知らせ",
    "community": "コミュニティ",
}
NEWS_SHOWN = 60
#: The esports page: the matches to come (and those being played), or the results.
SHOWS = ("upcoming", "results")
#: How many results and matches to come are shown at most.
GAMES_SHOWN = 80


#: The queues the agents' numbers are kept for (teams of five that win or lose).
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
    # the maps the matches seen were played on (by valorant-api.com's uuid)
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
                    "role": (agent.get("role") or {}).get("displayName") or "",
                    "uuid": item["agent"],
                    "few": item["picks"] < FEW,
                }
            )
    sort = request.args.get("sort", "pick")
    sort = sort if sort in ("pick", "win") else "pick"
    if sort == "win":
        rows.sort(key=lambda row: (row["few"], -row["win"], -row["picks"]))
    band_names = []
    for name, (low, high) in bands.items():
        first, last = (_family(_rank({"id": tier})["name"]) for tier in (low, high))
        band_names.append({"key": name, "name": first if first == last else f"{first} – {last}"})
    return {
        "rows": rows,
        "matches": found["matches"],
        "queue": queue,
        "queues": [{"key": key, "name": _t(QUEUES[key])} for key in STAT_QUEUES],
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
    return _t(CATEGORIES[key]) if key in CATEGORIES else key.replace("_", " ").title()


def _news_page() -> tuple[dict, int]:
    chosen = request.args.get("category", "")
    words = request.args.get("q", "").strip()[:60]
    values: dict = {"articles": [], "categories": [], "category": "", "q": words, "patch": None, "problem": ""}
    henrik = current_app.config["VAL_HENRIK"]
    country = NEWS_LANGS.get(g.lang, "en-gb")
    try:
        try:
            found = henrik.news(country)
        except HenrikError as error:
            # a language Riot's site (or HenrikDev) has no news in: the English ones, and the page says so
            if error.kind not in ("down", "notfound") or country == "en-gb":
                raise
            found = henrik.news("en-gb")
            values["english"] = True
    except HenrikError as error:
        code, values["problem"], values["settings_link"] = _failure(error.kind)
        return values, code
    counts: dict[str, int] = {}
    for item in found:
        if item["category"]:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
    order = list(CATEGORIES)
    keys = sorted(counts, key=lambda key: (order.index(key) if key in order else len(order), key))
    values["categories"] = [
        {
            "key": key,
            "name": _category(key),
            "count": counts[key],
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
        shown.append(item | {"when": _when(item["at"]), "category_name": _category(item["category"])})
    values["articles"] = shown[:NEWS_SHOWN]
    patches = [item for item in found if item["category"] == "patch_notes"]
    if patches and not values["category"] and not words:
        values["patch"] = patches[0] | {"when": _when(patches[0]["at"])}
    return values, 200


def _esports_page() -> tuple[dict, int]:
    show = request.args.get("show", "upcoming")
    show = show if show in SHOWS else "upcoming"
    chosen = request.args.get("league", "").lower()
    values: dict = {"show": show, "games": [], "leagues": [], "league": "", "live": 0, "problem": ""}
    try:
        found = current_app.config["VAL_HENRIK"].esports()
    except HenrikError as error:
        code, values["problem"], values["settings_link"] = _failure(error.kind)
        if error.kind == "down":
            values["problem"] = _t("eスポーツの日程を読み込めませんでした。時間をおいてもう一度どうぞ。")
        values["detail"] = error.detail if security.is_owner() else ""
        return values, code
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
    values["games"] = [item | {"when": _when(item["at"])} for item in games[:GAMES_SHOWN]]
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
