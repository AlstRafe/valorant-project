"""The pages around the tracker: a region's leaderboard, Riot's notices (maintenance, incidents), crosshair codes
drawn before they are pasted into the game, the store (featured bundles; the owner's own daily offers), how busy
each server usually is and the wait for a match (activity.py), the maps in rotation and who may queue together
(queues.py), a sensitivity converter, and the tools page that leads to them."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from flask import Flask, current_app, g, make_response, render_template, request, url_for

from valtracker import crosshair as crosshairs
from valtracker import i18n, queues, security
from valtracker import shop as store
from valtracker.activity import BANDS, ENOUGH, FEW
from valtracker.diary import store_day
from valtracker.henrik import BOARD_PAGE, HenrikError
from valtracker.riot import RiotError
from valtracker.stats import JST
from valtracker.views import QUEUES, RIOT_FAILURES, _failure, _rank, _t, act_left, cluster

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
#: The servers page counts hours in the visitor's own clock: hours from UTC, and its name (translated).
CLOCKS = {"ja": (9, "日本時間"), "ko": (9, "韓国時間"), "zh-TW": (8, "台湾時間"), "en-GB": (0, "協定世界時（UTC）")}
#: The sensitivity converter's games: (name, degrees a count of the mouse turns at sensitivity 1).
GAMES = {
    "valorant": ("VALORANT", 0.07),
    "cs2": ("Counter-Strike 2", 0.022),
    "apex": ("Apex Legends", 0.022),
    "overwatch": ("Overwatch 2", 0.0066),
}
#: Why a party may or may not queue together (queues.check) -> the words (translated).
PARTY_WORDS = {
    "size": "{size} 人のパーティーではコンペティティブに行けません。",
    "high": "{rank} 以上の人がいるパーティーは {size} 人まで（5 人なら行けます）。",
    "high_strict": "{rank} 以上の人がいるパーティーは {size} 人までです。",
    "full": "5 人ならランクの差に関係なく行けます。",
    "unranked": "ランクの付いていない人は、ここでは確かめられません。",
    "gap_ok": "行けます。{low} がいるパーティーは {top} まで一緒に行けます。",
    "gap": "行けません。{low} がいるパーティーは {top} まで（{high} は離れすぎています）。",
    "unknown": "このランクの決まりが Riot の設定にありませんでした。",
}
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


def _clock_text(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _family(name: str) -> str:
    """A rank's name without its division ("Gold 3" -> "Gold")."""
    return re.sub(r"\s*\d+$", "", name) or name


def _zone() -> tuple[float, str]:
    """The clock the servers page counts hours in: the visitor's own (the ``tz`` cookie, minutes from UTC, which the
    page's script sets), or their language's (Japan's for ja) when there is none or it is the same."""
    offset, name = CLOCKS.get(g.lang, CLOCKS["ja"])
    try:
        minutes = int(request.cookies.get("tz", ""))
    except ValueError:
        return offset, _t(name)
    if minutes % 15 or not -720 <= minutes <= 840 or minutes == offset * 60:
        return offset, _t(name)
    hours, rest = divmod(abs(minutes), 60)
    zone = f"UTC{'+' if minutes >= 0 else '−'}{hours}" + (f":{rest:02d}" if rest else "")
    return minutes / 60, _t("あなたの時間（{zone}）", zone=zone)


def _servers_page(region: str) -> dict:
    offset, clock_name = _zone()
    found = current_app.config["VAL_ACTIVITY"].servers(region, offset)
    servers = []
    for server in found["servers"]:
        top = max(server["hours"]) or 1
        servers.append(
            server
            | {
                "shown": cluster(server["name"]),
                "rank": _rank({"id": server["tier"]}) if server["tier"] else None,
                "bars": [
                    {"x": hour * 10 + 1, "h": round(44 * share / top, 1), "share": share, "hour": hour}
                    for hour, share in enumerate(server["hours"])
                ],
            }
        )
    order = {name: index for index, (name, _, _) in enumerate(BANDS, 1)}
    labels = {name: (low, high) for name, low, high in BANDS}
    waits = []
    for item in found["waits"]:
        band = ""
        if item["band"]:
            low, high = (_family(_rank({"id": tier})["name"]) for tier in labels[item["band"]])
            band = low if low == high else f"{low} – {high}"
        queue = _t(QUEUES[item["queue"]]) if item["queue"] in QUEUES else item["queue"]
        waits.append(item | {"queue_name": queue, "band_name": band, "text": _clock_text(item["seconds"])})
    lengths = [
        item
        | {
            "queue_name": _t(QUEUES[item["queue"]]) if item["queue"] in QUEUES else item["queue"],
            "text": _clock_text(item["seconds"]),
        }
        for item in found.get("lengths", [])
    ]
    queues_seen = [item["queue"] for item in waits]
    waits.sort(key=lambda item: (queues_seen.index(item["queue"]), order.get(item["band"], 0)))
    return {
        "servers": servers,
        "waits": waits,
        "lengths": lengths,
        "matches": found["matches"],
        "enough": ENOUGH,
        "hour_now": found["hour_now"],
        "clock_name": clock_name,
        "offset_minutes": round(offset * 60),
        "few": FEW,
    }


def _sensitivity(args) -> dict:
    game = args.get("game", "valorant")
    game = game if game in GAMES else "valorant"
    values: dict = {"game": game, "games": GAMES, "problem": "", "results": None}
    try:
        sens = float(args.get("sens", "") or 0)
        dpi = int(float(args.get("dpi", "") or 0))
        new_dpi = int(float(args.get("new_dpi", "") or 0))
    except ValueError:
        sens, dpi, new_dpi = 0, 0, 0
        values["problem"] = _t("数を入れてください。")
    values |= {"sens": args.get("sens", ""), "dpi": args.get("dpi", ""), "new_dpi": args.get("new_dpi", "")}
    if not sens or not dpi:
        return values
    if not (0 < sens <= 1000 and 50 <= dpi <= 64000) or (new_dpi and not 50 <= new_dpi <= 64000):
        values["problem"] = _t("感度は 0 より大きく、DPI は 50〜64000 で入れてください。")
        return values
    turn = sens * GAMES[game][1]  # degrees a count turns
    rows = []
    for key, (name, yaw) in GAMES.items():
        rows.append({"key": key, "name": name, "sens": round(turn / yaw, 4), "edpi": round(dpi * turn / yaw, 1)})
    valorant = turn / GAMES["valorant"][1]
    values["results"] = {
        "rows": rows,
        "cm": round(360 * 2.54 / (dpi * turn), 1),
        "inch": round(360 / (dpi * turn), 1),
        "valorant": round(valorant, 4),
        "edpi": round(valorant * dpi, 1),
        "new": round(valorant * dpi / new_dpi, 4) if new_dpi else None,
    }
    return values


def _queues_page() -> tuple[dict, int]:
    values: dict = {"problem": "", "settings_link": False, "queues": [], "competitive": None, "verdict": None}
    try:
        found = current_app.config["VAL_RIOT"].queues()
    except RiotError as error:
        if security.is_owner():
            values["problem"] = RIOT_FAILURES.get(error.kind, RIOT_FAILURES["down"])
            values["settings_link"] = error.kind not in ("busy", "down")
        else:
            values["problem"] = _t("いまはゲームの設定を読めません。時間をおいてもう一度どうぞ。")
        return values, 200 if error.kind == "nologin" else 503
    data = queues.settings(found.get("config") or {}, current_app.config["VAL_CONTENT"].map_index(g.lang))
    for queue in data["queues"]:
        queue["name"] = _t(QUEUES[queue["id"]]) if queue["id"] in QUEUES else queue["id"]
    values["queues"] = data["queues"]
    values["read_at"] = _when(datetime.fromtimestamp(float(found.get("at") or time.time()), JST).isoformat())
    competitive = data["competitive"]
    if competitive and competitive["ceilings"]:
        values["competitive"] = competitive | {
            "table": [
                {"rank": _rank({"id": tier}), "top": _rank({"id": top})}
                for tier, top in competitive["ceilings"].items()
            ],
            "high_rank": _rank({"id": competitive["high_tier"]}) if competitive["high_tier"] else None,
        }
        tiers = current_app.config["VAL_CONTENT"].tiers(g.lang)
        values["choices"] = [{"tier": 0, "name": _t("未ランク")}] + [
            {"tier": tier, "name": _rank({"id": tier})["name"]} for tier in sorted(tiers) if tier >= queues.IRON
        ]
        picked = []
        for value in request.args.getlist("rank")[:5]:
            try:
                picked.append(int(value))
            except ValueError:
                continue
        picked = [tier for tier in picked if tier == 0 or tier in tiers or tier >= queues.IRON]
        values["picked"] = picked + [None] * (5 - len(picked))
        if len([tier for tier in picked]) >= 2:
            verdict = queues.check(picked, competitive)
            why, said = verdict["why"], dict(verdict["values"])
            for key in ("low", "high", "top"):
                if key in said:
                    said[key] = _rank({"id": said[key]})["name"]
            if why == "high":
                said["rank"] = _rank({"id": said.pop("tier")})["name"]
                why = "high" if competitive["bypass"] else "high_strict"
            values["verdict"] = {"ok": verdict["ok"], "words": _t(PARTY_WORDS[why], **said)}
    return values, 200


def _diary(own: dict) -> None:
    """Notes the owner's store of today (diary.py) and marks the skins they want, and the days in a row."""
    diary = current_app.config["VAL_DIARY"]
    now = time.time()
    day = store_day(own["daily_until"], now)
    diary.note(
        day,
        [{"uuid": item["skin"], "price": item.get("price") or 0} for item in own["daily"] if item.get("skin")],
        [
            {"uuid": item["skin"], "price": item.get("price") or 0, "discount": item.get("discount") or 0}
            for item in own["night"] or []
            if item.get("skin")
        ],
    )
    wants = set(diary.wants())
    shown = own["daily"] + (own["night"] or []) + [thing for bundle in own["bundles"] for thing in bundle["items"]]
    for item in shown:
        item["wanted"] = bool(item.get("skin")) and item["skin"] in wants and not item.get("owned")
    own["wanted"] = list(dict.fromkeys(item["name"] for item in shown if item["wanted"]))
    own["streak"] = diary.streak(day)
    # the night market's cards the game has not shown yet lie face down until turned (app.js, remembered there)
    own["night_key"] = day
    for number, item in enumerate(own["night"] or []):
        item["flip"] = "" if item.get("seen") else str(number + 1)


def _day_text(day: str) -> str:
    try:
        moment = datetime.fromisoformat(day)
    except ValueError:
        return day
    weekday = "月火水木金土日"[moment.weekday()]
    return f"{moment.month}月{moment.day}日（{weekday}）"


def _ics(text: str) -> str:
    """A calendar's text value: backslashes, commas, semicolons and line breaks escaped."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _reminder():
    now = time.time()
    start = datetime.fromtimestamp(_next_reset(now), UTC).strftime("%Y%m%dT%H%M%SZ")
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    title = _ics(_t("VALORANT のストアが入れ替わりました"))
    about = _ics(_t("毎日の 4 つのスキンが新しくなりました。") + " " + url_for("shop", _external=True))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VAL Tracker//Store//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _ics(_t("VALORANT のストア")),
        "BEGIN:VEVENT",
        "UID:daily-store@val-tracker",
        "DTSTAMP:" + stamp,
        "DTSTART:" + start,
        "DURATION:PT15M",
        "RRULE:FREQ=DAILY",
        "SUMMARY:" + title,
        "DESCRIPTION:" + about,
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:" + title,
        "TRIGGER:PT0M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    response = make_response("\r\n".join(lines) + "\r\n")
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="valorant-store.ics"'
    return response


def _next_reset(now: float) -> float:
    """The daily store's next change: midnight UTC (9:00 in Japan)."""
    return (int(now) // 86400 + 1) * 86400


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
        common = {
            "regions": _regions("leaderboard", region),
            "region_name": _t(REGION_NAMES[region]),
            "act": act_left(),
        }
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

    @app.get("/servers")
    def servers():
        region = _region()
        values = _servers_page(region)
        values["regions"] = _regions("servers", region)
        values["region"] = region
        values["region_name"] = _t(REGION_NAMES[region])
        return render_template("servers.html", **values)

    @app.get("/queues")
    def queue_settings():
        values, status = _queues_page()
        return render_template("queues.html", **values), status

    @app.get("/sensitivity")
    def sensitivity():
        values = _sensitivity(request.args)
        return render_template("sensitivity.html", **values), 400 if values["problem"] else 200

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
        reset = _next_reset(time.time())
        values["reset"] = {"left": _left(reset), "when": _when(datetime.fromtimestamp(reset, JST).isoformat())}
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
                _diary(own)
                values["own"] = own
        return render_template("shop.html", **values)

    @app.get("/shop/history")
    def shop_history():
        """The owner's stores of the days before (diary.py), with the skins they want marked."""
        security.owner_only()
        content, diary = current_app.config["VAL_CONTENT"], current_app.config["VAL_DIARY"]
        wants = set(diary.wants())
        days = []
        for day, entry in diary.days():
            listed = {}
            for key in ("daily", "night"):
                listed[key] = [
                    store.item("skins", thing.get("uuid"), content, g.lang)
                    | {"price": thing.get("price") or 0, "discount": thing.get("discount") or 0}
                    for thing in entry.get(key) or []
                    if isinstance(thing, dict)
                ]
                for thing in listed[key]:
                    thing["wanted"] = thing.get("skin") in wants
            days.append({"day": day, "when": _day_text(day), **listed})
        seen: dict[str, int] = {}
        for item in (thing for day in days for thing in day["daily"]):
            if item.get("skin"):
                seen[item["skin"]] = seen.get(item["skin"], 0) + 1
        today = store_day(_next_reset(time.time()), time.time())
        vp = (content.index("currencies", g.lang).get(store.VP) or {}).get("displayIcon")
        return render_template(
            "shop_history.html", days=days, streak=diary.streak(today), vp_icon=vp, wants=len(wants), seen=seen
        )

    @app.get("/shop/reminder.ics")
    def shop_reminder():
        """A calendar with one event a day when the daily store changes (midnight UTC), for any calendar app."""
        return _reminder()
