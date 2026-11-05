"""Pages about the game around the matches, from HenrikDev: Riot's news (/news) and the esports schedule and
results (/esports). Everyone's, in the visitor's language."""

from __future__ import annotations

from flask import Flask, current_app, g, render_template, request, url_for

from valtracker.henrik import HenrikError
from valtracker.tools import _when
from valtracker.views import _failure, _t

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


def _category(key: str) -> str:
    return _t(CATEGORIES[key]) if key in CATEGORIES else key.replace("_", " ").title()


def _news_page() -> tuple[dict, int]:
    chosen = request.args.get("category", "")
    words = request.args.get("q", "").strip()[:60]
    values: dict = {"articles": [], "categories": [], "category": "", "q": words, "patch": None, "problem": ""}
    try:
        found = current_app.config["VAL_HENRIK"].news(NEWS_LANGS.get(g.lang, "en-gb"))
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

    @app.get("/esports")
    def esports():
        values, code = _esports_page()
        return render_template("esports.html", **values), code
