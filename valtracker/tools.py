"""The pages around the tracker: a region's leaderboard, Riot's notices (maintenance, incidents), crosshair codes
drawn before they are pasted into the game, the store (featured bundles; the owner's own daily offers), and the
tools page that leads to them."""

from __future__ import annotations

import time
from datetime import datetime

from flask import Flask, current_app, g, make_response, render_template, request, url_for

from valtracker import crosshair as crosshairs
from valtracker import i18n, security
from valtracker import shop as store
from valtracker.henrik import BOARD_PAGE, HenrikError
from valtracker.riot import RiotError
from valtracker.stats import JST
from valtracker.views import RIOT_FAILURES, _failure, _rank, _t

#: The leaderboard's and the notices' regions, in the order of the tabs -> their names (translated).
REGION_NAMES = {
    "ap": "アジア太平洋",
    "kr": "韓国",
    "eu": "ヨーロッパ",
    "na": "北米",
    "latam": "ラテンアメリカ",
    "br": "ブラジル",
}
#: The region shown when none is chosen: the one most of a language's players are in.
HOME_REGION = {"ja": "ap", "ko": "kr", "en-GB": "eu", "zh-TW": "ap"}
#: Riot writes its notices in each of these (HenrikDev's names, see henrik._slim_status); English otherwise.
NOTICE_LOCALES = {"ja": "ja_jp", "ko": "ko_kr", "en-GB": "en_gb", "zh-TW": "zh_tw"}
#: Pages of the leaderboard that can be asked for (HenrikDev lists about the top 10,000 of a region).
BOARD_PAGES = 200
#: A notice for these platforms only is not about the PC game, which this site tracks.
CONSOLES = {"playstation", "xbox", "ps5", "xbsx", "console"}


def _region() -> str:
    region = request.args.get("region", "")
    return region if region in REGION_NAMES else HOME_REGION.get(g.lang, "ap")


def _regions(endpoint: str, current: str, **values) -> list[dict]:
    return [
        {"key": key, "name": _t(name), "url": url_for(endpoint, region=key, **values), "current": key == current}
        for key, name in REGION_NAMES.items()
    ]


def _moment(value) -> datetime | None:
    """An ISO time (HenrikDev's) in Japan's time, or None."""
    try:
        found = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return found.astimezone(JST) if found.tzinfo else found.replace(tzinfo=JST)


def _when(value) -> dict | None:
    found = _moment(value)
    return {"iso": found.isoformat(), "text": i18n.when(g.lang, found)} if found else None


def _page_number() -> int:
    try:
        return max(1, min(BOARD_PAGES, int(request.args.get("page", 1))))
    except ValueError:
        return 1


def _board(region: str, page: int) -> tuple[dict, int]:
    """What leaderboard.html shows of one page, and the status."""
    values = {"region": region, "page": page, "problem": "", "settings_link": False, "loading": False}
    try:
        found = current_app.config["VAL_HENRIK"].leaderboard(region, (page - 1) * BOARD_PAGE)
    except HenrikError as error:
        status, values["problem"], values["settings_link"] = _failure(error.kind)
        if error.kind == "notfound":
            values["problem"], status = _t("このページにはプレイヤーがいません。"), 404
        return values | {"players": [], "thresholds": []}, status
    players = []
    for item in found["players"]:
        players.append(
            item
            | {
                "rank_shown": _rank({"id": item["tier"]}) if item["tier"] else None,
                "url": url_for("player", name=item["name"], tag=item["tag"])
                if item["name"] and item["tag"] and not item["hidden"]
                else "",
            }
        )
    thresholds = [item | {"rank_shown": _rank({"id": item["tier"]})} for item in found["thresholds"] if item["tier"]]
    more = len(found["players"]) >= BOARD_PAGE and page < BOARD_PAGES
    if found["total"]:
        more = more and page * BOARD_PAGE < found["total"]
    return values | {
        "players": players,
        "thresholds": thresholds,
        "updated": _when(found["updated"]),
        "earlier": url_for("leaderboard", region=region, page=page - 1 if page > 2 else None) if page > 1 else "",
        "later": url_for("leaderboard", region=region, page=page + 1) if more else "",
    }, 200


def _notices(found: dict) -> list[dict]:
    """Riot's notices in the visitor's language (English, else any, when Riot did not write one in it)."""
    wanted = NOTICE_LOCALES.get(g.lang, "en_us")

    def pick(words: dict) -> str:
        return words.get(wanted) or words.get("en_us") or words.get("en_gb") or next(iter(words.values()), "")

    listed = []
    for kind in ("incidents", "maintenances"):
        for item in found.get(kind) or []:
            platforms = set(item.get("platforms") or [])
            if platforms and platforms <= CONSOLES:
                continue
            latest = (item.get("updates") or [{}])[0]
            listed.append(
                {
                    "kind": item["kind"],
                    "title": pick(item.get("titles") or {}) or _t("お知らせ"),
                    "words": pick(latest.get("words") or {}),
                    "when": _when(latest.get("at") or item.get("at")),
                    "severity": item.get("severity") or "",
                    "status": item.get("status") or "",
                }
            )
    return listed


def _shapes(settings: dict | None) -> list[dict] | None:
    return crosshairs.shapes(settings) if settings is not None else None


def _crosshair_page(code: str) -> dict:
    found = crosshairs.parse(code) if code else None
    shown = found or crosshairs.parse("0")
    table = [("primary", _t("通常"), shown["primary"])]
    if shown["ads"] is not None:
        table.append(("ads", _t("覗き込み"), shown["ads"]))
    described = []
    for key, name, settings in table:
        about = crosshairs.describe(settings)
        about["colour_name"] = _t(about["colour_name"])
        described.append({"key": key, "name": name, "about": about})
    return {
        "code": code,
        "problem": _t("クロスヘアのコードではありませんでした。ゲームの「コピー」で出たものをそのまま貼ってください。")
        if code and not found
        else "",
        "unknown": found["unknown"] if found else [],
        "primary": crosshairs.shapes(shown["primary"]),
        "ads": _shapes(shown["ads"]),
        "sniper": crosshairs.sniper_shapes(shown["sniper"]),
        "reach": crosshairs.REACH,
        "described": described,
        "presets": [
            {
                "name": _t(name),
                "code": crosshairs.code(settings),
                "shapes": crosshairs.shapes(crosshairs.PRIMARY | settings),
            }
            for name, settings in crosshairs.PRESETS.items()
        ],
    }


def _left(until: float) -> dict | None:
    """The time left until ``until`` (a Unix time), for the page and its script (which counts it down)."""
    seconds = until - time.time()
    if not until or seconds <= 0:
        return None
    parts = store.left(seconds)
    days = parts["hours"] // 24
    words = (
        _t("残り {days} 日", days=days)
        if days >= 2
        else _t("残り {hours} 時間 {minutes} 分", hours=parts["hours"], minutes=parts["minutes"])
    )
    return {"until": int(until), "text": words}


def register(app: Flask) -> None:
    @app.get("/tools")
    def tools():
        return render_template("tools.html")

    @app.get("/leaderboard")
    def leaderboard():
        """At once when the page is kept; else the page without the players, which the script asks for
        (leaderboard_part), as a player's page does. ``?full=1`` waits for them."""
        region, page = _region(), _page_number()
        henrik = current_app.config["VAL_HENRIK"]
        common = {"regions": _regions("leaderboard", region), "region_name": _t(REGION_NAMES[region])}
        if request.args.get("full") or not henrik.key() or henrik.has_board(region, (page - 1) * BOARD_PAGE):
            values, status = _board(region, page)
            return render_template("leaderboard.html", **common, **values), status
        return render_template("leaderboard.html", **common, region=region, page=page, loading=True, problem="")

    @app.get("/leaderboard/part")
    def leaderboard_part():
        region, page = _region(), _page_number()
        values, status = _board(region, page)
        response = make_response(render_template("_leaderboard.html", **values), status)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/status")
    def status():
        region = _region()
        values = {"regions": _regions("status", region), "region_name": _t(REGION_NAMES[region]), "notices": []}
        try:
            found = current_app.config["VAL_HENRIK"].status(region)
        except HenrikError as error:
            code, problem, settings_link = _failure(error.kind)
            return render_template("status.html", **values, problem=problem, settings_link=settings_link), code
        return render_template("status.html", **values | {"notices": _notices(found)}, problem="")

    @app.get("/status/notice")
    def status_notice():
        """The top page's line about maintenance or an incident in the visitor's region (empty when there is
        none, or it could not be asked): its script puts it in place."""
        region = _region()
        try:
            notices = _notices(current_app.config["VAL_HENRIK"].status(region))
        except HenrikError:
            notices = []
        response = make_response(
            render_template("_notice.html", notices=notices, region=region) if notices else "", 200
        )
        response.headers["Cache-Control"] = "private, max-age=120"
        return response

    @app.get("/crosshair")
    def crosshair():
        code = request.args.get("code", "").strip()[:600]
        values = _crosshair_page(code)
        return render_template("crosshair.html", **values), 400 if values["problem"] else 200

    @app.get("/shop")
    def shop():
        """Everyone: the featured bundles (HenrikDev). The owner: their own store too (riot.py), with what they
        already have marked."""
        content = current_app.config["VAL_CONTENT"]
        values: dict = {"featured": [], "featured_problem": "", "own": None, "own_problem": "", "own_detail": ""}
        try:
            values["featured"] = store.featured(current_app.config["VAL_HENRIK"].featured(), content, g.lang)
        except HenrikError:
            values["featured_problem"] = _t("読み込めませんでした。")
        for bundle in values["featured"]:
            bundle["left"] = _left(bundle["until"])
        values["vp_icon"] = (content.index("currencies", g.lang).get(store.VP) or {}).get("displayIcon")
        if security.is_owner():
            riot = current_app.config["VAL_RIOT"]
            try:
                found = riot.shop()
            except RiotError as error:
                values["own_problem"] = RIOT_FAILURES.get(error.kind, RIOT_FAILURES["down"])
                values["own_detail"] = error.detail
                values["own_settings"] = error.kind not in ("busy", "down")
            else:
                try:
                    owned = riot.collection().get("owned")
                except RiotError:
                    owned = None
                own = store.page(found, content, g.lang, owned)
                for key in ("daily", "night", "accessories"):
                    own[key + "_left"] = _left(own[key + "_until"])
                for bundle in own["bundles"]:
                    bundle["left"] = _left(bundle["until"])
                values["own"] = own
        return render_template("shop.html", **values)
