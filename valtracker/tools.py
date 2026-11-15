"""トラッカーまわりのページ: リージョンのランキング、Riot のお知らせ（メンテナンス、障害）、
ゲームに貼る前に描くクロスヘアコード、ストア（おすすめバンドル。持ち主には本人のデイリーオファー）、
各サーバーのいつもの混み具合とマッチの待ち時間（activity.py）、ローテーション中のマップと一緒に並べる人（queues.py）、
感度の換算、それらへの入口のツールページ。"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from flask import Flask, abort, current_app, g, jsonify, make_response, render_template, request, url_for

from valtracker import crosshair as crosshairs
from valtracker import i18n, queues, security, shared_url
from valtracker import shop as store
from valtracker.activity import BANDS, ENOUGH, FEW, LINE_QUEUES, RANKS_ENOUGH, WINDOW_DAYS
from valtracker.diary import store_day
from valtracker.henrik import BOARD_PAGE, HenrikError
from valtracker.riot import RiotError
from valtracker.riotweb import RiotWebError
from valtracker.stats import JST
from valtracker.views import (
    act_left,
    band_name,
    cluster,
    failure_info,
    queue_name,
    rank_info,
    riot_failure,
    share_text,
    tr,
)

#: ランキングとお知らせのリージョン（タブの順）-> 名前（訳す）。
REGION_NAMES = {
    "ap": "アジア太平洋",
    "kr": "韓国",
    "eu": "ヨーロッパ",
    "na": "北米",
    "latam": "ラテンアメリカ",
    "br": "ブラジル",
}
#: 選んでいないときに出すリージョン: その言語のプレイヤーが一番多いところ。
HOME_REGION = {"ja": "ap", "ko": "kr", "en-GB": "eu", "zh-TW": "ap"}
#: Riot はお知らせをこれらの言語で書く（HenrikDev の名前、henrik.slim_status を参照）。ほかは英語。
NOTICE_LOCALES = {"ja": "ja_jp", "ko": "ko_kr", "en-GB": "en_gb", "zh-TW": "zh_tw"}
#: 頼めるランキングのページ数（HenrikDev はリージョンの上位 1 万人ほどを出す）。
BOARD_PAGES = 200
#: サーバーのページは訪問者自身の時計で時間を数える: UTC からの時間と、その名前（訳す）。
CLOCKS = {"ja": (9, "日本時間"), "ko": (9, "韓国時間"), "zh-TW": (8, "台湾時間"), "en-GB": (0, "協定世界時（UTC）")}
#: 感度換算のゲーム: （名前, 感度 1 でマウス 1 カウントあたりに回る角度）。
GAMES = {
    "valorant": ("VALORANT", 0.07),
    "cs2": ("Counter-Strike 2", 0.022),
    "apex": ("Apex Legends", 0.022),
    "overwatch": ("Overwatch 2", 0.0066),
}
#: パーティーで並べる・並べない理由（queues.check）-> 文（訳す）。
PARTY_WORDS = {
    "size": "{size} 人パーティーではコンペティティブに参加できません。",
    "high": "{rank} 以上を含むパーティーは {size} 人まで（5 人は可）。",
    "high_strict": "{rank} 以上を含むパーティーは {size} 人までです。",
    "full": "5 人パーティーはランク差に関係なく参加できます。",
    "unranked": "未ランクのプレイヤーは判定できません。",
    "gap_ok": "組めます。{low} と組めるのは {top} までです。",
    "gap": "組めません。{low} と組めるのは {top} まで（{high} はランク差が大きすぎます）。",
    "unknown": "このランクの制限データがありません。",
}
#: これらのプラットフォームだけのお知らせは、このサイトが追う PC 版のものではない。
CONSOLES = {"playstation", "xbox", "ps5", "xbsx", "console"}


def _region() -> str:
    region = request.args.get("region", "")
    return region if region in REGION_NAMES else HOME_REGION.get(g.lang, "ap")


def _regions(endpoint: str, current: str, **values) -> list[dict]:
    return [
        {"key": key, "name": tr(name), "url": url_for(endpoint, region=key, **values), "current": key == current}
        for key, name in REGION_NAMES.items()
    ]


def _moment(value) -> datetime | None:
    """ISO の時刻（HenrikDev のもの）を日本時間に。無ければ None。"""
    try:
        found = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return found.astimezone(JST) if found.tzinfo else found.replace(tzinfo=JST)


def moment_info(value) -> dict | None:
    found = _moment(value)
    return {"iso": found.isoformat(), "text": i18n.when(g.lang, found)} if found else None


def _page_number() -> int:
    try:
        return max(1, min(BOARD_PAGES, int(request.args.get("page", 1))))
    except ValueError:
        return 1


def _wanted() -> tuple[str, str, str]:
    """ランキングで探すプレイヤー（``?q=Name#Tag``）: （入力されたもの, 名前, タグ）。
    Riot ID になりえないときは名前の代わりに問題の文。"""
    typed = " ".join(str(request.args.get("q", "")).split())[:40]
    if not typed:
        return "", "", ""
    name, _, tag = typed.partition("#")
    name, tag = name.strip(), tag.strip()
    if not 3 <= len(name) <= 16 or len(tag) > 5:
        return typed, "", ""
    return typed, name, tag


def _riot_board_act() -> str:
    """ゲーム自身のランキングを頼むアクト（持ち主の Riot アカウントで聞けるとき。でなければ ""）。"""
    found = current_app.config["VAL_RIOT"].status()
    if not found["linked"] or found["signed_out"]:
        return ""
    return ((current_app.config["VAL_CONTENT"].act(g.lang) or {}).get("uuid")) or ""


def _board(region: str, page: int) -> tuple[dict, int]:
    """leaderboard.html に出す 1 ページ分（または探したプレイヤー）と、ステータス。
    まず Riot 自身のランキング（持ち主の Riot アカウントでゲームのサーバーから）、無ければ HenrikDev のもの。"""
    typed, name, tag = _wanted()
    values = {"region": region, "page": page, "problem": "", "settings_link": False, "loading": False}
    values |= {"q": typed, "searched": bool(typed)}
    if typed and not name:
        values["problem"] = tr("「名前#タグ」の形式で入力してください。")
        return values | {"players": [], "thresholds": []}, 400
    start = 0 if typed else (page - 1) * BOARD_PAGE
    found, source = None, ""
    act = _riot_board_act()
    if act:
        try:
            found = current_app.config["VAL_RIOT"].leaderboard(region, act, start, BOARD_PAGE, name)
            source = "riot"
        except RiotError:
            found = None
    if found is None:
        try:
            found = current_app.config["VAL_HENRIK"].leaderboard(region, start, name, tag)
            source = "henrik"
        except HenrikError as error:
            status, values["problem"], values["settings_link"] = failure_info(error.kind)
            if error.kind == "notfound":
                values["problem"], status = tr("このページにはプレイヤーがいません。"), 404
                if typed:
                    values["problem"], status = "", 200
                    return values | {"players": [], "thresholds": [], "source": source}, status
            return values | {"players": [], "thresholds": []}, status
    players = []
    for item in found["players"]:
        if typed and (item["hidden"] or item["name"].casefold() != name.casefold()):
            continue  # Riot はそう始まる名前を探す。隠しているプレイヤーは名前では見つからない
        if typed and tag and item["tag"].casefold() != tag.casefold():
            continue
        players.append(
            item
            | {
                "rank_shown": rank_info({"id": item["tier"]}) if item["tier"] else None,
                "url": url_for("player", name=item["name"], tag=item["tag"])
                if item["name"] and item["tag"] and not item["hidden"]
                else "",
            }
        )
    thresholds = [
        item | {"rank_shown": rank_info({"id": item["tier"]})} for item in found["thresholds"] if item["tier"]
    ]
    more = not typed and len(found["players"]) >= BOARD_PAGE and page < BOARD_PAGES
    if found["total"]:
        more = more and page * BOARD_PAGE < found["total"]
    return values | {
        "players": players,
        "thresholds": thresholds,
        "source": source,
        "board_updated": moment_info(found["updated"]),
        "earlier": url_for("leaderboard", region=region, page=page - 1 if page > 2 else None)
        if page > 1 and not typed
        else "",
        "later": url_for("leaderboard", region=region, page=page + 1) if more else "",
    }, 200


def _status(region: str) -> dict:
    """リージョンのサーバーのお知らせ: Riot 自身のファイル、無ければ HenrikDev の写し（どちらも来なければ HenrikError）
    。"""
    try:
        return current_app.config["VAL_RIOT_WEB"].status(region)
    except RiotWebError:
        return current_app.config["VAL_HENRIK"].status(region)


def _notices(found: dict) -> list[dict]:
    """訪問者の言語での Riot のお知らせ（その言語で書かれていなければ英語、それも無ければどれか）。"""
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
                    "title": pick(item.get("titles") or {}) or tr("お知らせ"),
                    "words": pick(latest.get("words") or {}),
                    "when": moment_info(latest.get("at") or item.get("at")),
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
    table = [("primary", tr("通常"), shown["primary"])]
    if shown["ads"] is not None:
        table.append(("ads", tr("ADS"), shown["ads"]))
    described = []
    for key, name, settings in table:
        about = crosshairs.describe(settings)
        about["colour_name"] = tr(about["colour_name"])
        described.append({"key": key, "name": name, "about": about})
    return {
        "code": code,
        "problem": tr("クロスヘアコードとして読み取れません。ゲームでコピーしたものをそのまま貼ってください。")
        if code and not found
        else "",
        "unknown": found["unknown"] if found else [],
        "primary": crosshairs.shapes(shown["primary"]),
        "ads": _shapes(shown["ads"]),
        "sniper": crosshairs.sniper_shapes(shown["sniper"]),
        "described": described,
        "presets": [
            {
                "name": tr(name),
                "code": crosshairs.code(settings),
                "shapes": crosshairs.shapes(crosshairs.PRIMARY | settings),
            }
            for name, settings in crosshairs.PRESETS.items()
        ],
    }


def _left(until: float) -> dict | None:
    """``until``（Unix 時刻）までの残り時間。ページとそのスクリプト（カウントダウンする）用。"""
    seconds = until - time.time()
    if not until or seconds <= 0:
        return None
    parts = store.left(seconds)
    days = parts["hours"] // 24
    words = (
        tr("残り {days} 日", days=days)
        if days >= 2
        else tr("残り {hours} 時間 {minutes} 分", hours=parts["hours"], minutes=parts["minutes"])
    )
    return {"until": int(until), "text": words}


def _clock_text(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _zone() -> tuple[float, str]:
    """サーバーのページが時間を数える時計: 訪問者自身のもの（ページのスクリプトが入れる ``tz`` cookie、UTC からの分）。
    無いか同じなら言語のもの（ja なら日本）。"""
    offset, name = CLOCKS.get(g.lang, CLOCKS["ja"])
    try:
        minutes = int(request.cookies.get("tz", ""))
    except ValueError:
        return offset, tr(name)
    if minutes % 15 or not -720 <= minutes <= 840 or minutes == offset * 60:
        return offset, tr(name)
    hours, rest = divmod(abs(minutes), 60)
    zone = f"UTC{'+' if minutes >= 0 else '−'}{hours}" + (f":{rest:02d}" if rest else "")
    return minutes / 60, tr("現地時間（{zone}）", zone=zone)


def _numbers_region() -> str:
    region = request.args.get("region", "ap")
    if region not in REGION_NAMES:
        abort(404)
    return region


def _ranks_page(region: str) -> dict:
    """リージョンで見たコンペティティブの成績がランクにどう分布するか（上から）: 各ランクの割合と、
    そのランク以上の割合（「上位 X%」）。"""
    found = current_app.config["VAL_ACTIVITY"].ranks(region)
    total = found["lines"]
    values = {"lines": total, "enough": total >= RANKS_ENOUGH, "rows": [], "days": WINDOW_DAYS}
    if not values["enough"]:
        return values
    above, widest = 0, max(found["tiers"].values())
    numbers = sorted(
        {number for number in current_app.config["VAL_CONTENT"].tiers(g.lang) if number > 2} | set(found["tiers"])
    )
    for number in reversed(numbers):
        count = found["tiers"].get(number, 0)
        above += count
        values["rows"].append(
            {
                "rank": rank_info({"id": number}),
                "count": count,
                "share": share_text(100 * count / total),
                "bar": round(100 * count / widest, 1),
                "top": share_text(100 * above / total),
            }
        )
    return values


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
                "rank": rank_info({"id": server["tier"]}) if server["tier"] else None,
                "bars": [
                    {"x": hour * 10 + 1, "h": round(44 * share / top, 1), "share": share, "hour": hour}
                    for hour, share in enumerate(server["hours"])
                ],
            }
        )
    order = {name: index for index, (name, _, _) in enumerate(BANDS, 1)}
    waits = []
    for item in found["waits"]:
        band = band_name(item["band"]) if item["band"] else ""
        waits.append(
            item | {"queue_name": queue_name(item["queue"]), "band_name": band, "text": _clock_text(item["seconds"])}
        )
    lengths = [
        item
        | {
            "queue_name": queue_name(item["queue"]),
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
    except (ValueError, OverflowError):
        sens, dpi, new_dpi = 0, 0, 0
        values["problem"] = tr("数値を入力してください。")
    values |= {"sens": args.get("sens", ""), "dpi": args.get("dpi", ""), "new_dpi": args.get("new_dpi", "")}
    if not sens or not dpi:
        return values
    if not (0 < sens <= 1000 and 50 <= dpi <= 64000) or (new_dpi and not 50 <= new_dpi <= 64000):
        values["problem"] = tr("感度は 0 より大きい値、DPI は 50〜64000 で入力してください。")
        return values
    turn = sens * GAMES[game][1]  # 1 カウントで回る角度
    rows = []
    for key, (name, yaw) in GAMES.items():
        rows.append({"key": key, "name": name, "sens": round(turn / yaw, 4)})
    valorant = turn / GAMES["valorant"][1]
    values["results"] = {
        "rows": rows,
        "cm": round(360 * 2.54 / (dpi * turn), 1),
        "inch": round(360 / (dpi * turn), 1),
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
            values["problem"] = riot_failure(error.kind)
            values["settings_link"] = error.kind not in ("busy", "down")
        else:
            values["problem"] = tr("ゲームの設定を取得できません。しばらくしてからお試しください。")
        return values, 200 if error.kind == "nologin" else 503
    data = queues.settings(found.get("config") or {}, current_app.config["VAL_CONTENT"].map_index(g.lang))
    for queue in data["queues"]:
        queue["name"] = queue_name(queue["id"])
    values["queues"] = data["queues"]
    values["read_at"] = moment_info(datetime.fromtimestamp(float(found.get("at") or time.time()), JST).isoformat())
    competitive = data["competitive"]
    if competitive and competitive["ceilings"]:
        values["competitive"] = competitive | {
            "table": [
                {"rank": rank_info({"id": tier}), "top": rank_info({"id": top})}
                for tier, top in competitive["ceilings"].items()
            ],
            "high_rank": rank_info({"id": competitive["high_tier"]}) if competitive["high_tier"] else None,
        }
        tiers = current_app.config["VAL_CONTENT"].tiers(g.lang)
        values["choices"] = [{"tier": 0, "name": tr("未ランク")}] + [
            {"tier": tier, "name": rank_info({"id": tier})["name"]} for tier in sorted(tiers) if tier >= queues.IRON
        ]
        picked = []
        for value in request.args.getlist("rank")[:5]:
            try:
                picked.append(int(value))
            except ValueError:
                continue
        picked = [tier for tier in picked if tier == 0 or tier in tiers or tier >= queues.IRON]
        values["picked"] = picked + [None] * (5 - len(picked))
        if len(picked) >= 2:
            verdict = queues.check(picked, competitive)
            why, said = verdict["why"], dict(verdict["values"])
            for key in ("low", "high", "top"):
                if key in said:
                    said[key] = rank_info({"id": said[key]})["name"]
            if why == "high":
                said["rank"] = rank_info({"id": said.pop("tier")})["name"]
                why = "high" if competitive["bypass"] else "high_strict"
            values["verdict"] = {"ok": verdict["ok"], "words": tr(PARTY_WORDS[why], **said)}
    return values, 200


def _diary(own: dict) -> None:
    """持ち主の今日のストアを日記に残し（diary.py）、ほしいスキンと連続日数に印を付ける。"""
    diary = current_app.config["VAL_DIARY"]
    account = current_app.config["VAL_RIOT"].me()  # ストアはアカウントごとなので、日付もアカウントごと
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
        account,
    )
    wants = set(diary.wants())
    shown = own["daily"] + (own["night"] or []) + [thing for bundle in own["bundles"] for thing in bundle["items"]]
    for item in shown:
        item["wanted"] = bool(item.get("skin")) and item["skin"] in wants and not item.get("owned")
    own["wanted"] = list(dict.fromkeys(item["name"] for item in shown if item["wanted"]))
    own["streak"] = diary.streak(day, account)
    # ゲームがまだ見せていないナイトマーケットのカードは、めくるまで伏せる（app.js が覚える）
    own["night_key"] = day
    for number, item in enumerate(own["night"] or []):
        item["flip"] = "" if item.get("seen") else str(number + 1)


def _day_text(day: str) -> str:
    try:
        moment = datetime.fromisoformat(day)
    except ValueError:
        return day
    return i18n.weekday(g.lang, moment)


def _ics(text: str) -> str:
    """カレンダーのテキストの値: バックスラッシュ、カンマ、セミコロン、改行をエスケープする。"""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _reminder():
    now = time.time()
    start = datetime.fromtimestamp(_next_reset(now), UTC).strftime("%Y%m%dT%H%M%SZ")
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    title = _ics(tr("VALORANT のストアが更新されました"))
    about = _ics(tr("デイリーの 4 スキンが入れ替わりました。") + " " + shared_url("shop"))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//VAL Tracker//Store//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + _ics(tr("VALORANT のストア")),
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
    """デイリーストアの次の切り替わり: UTC の 0 時（日本時間 9:00）。"""
    return (int(now) // 86400 + 1) * 86400


def register(app: Flask) -> None:
    @app.get("/tools")
    def tools():
        return render_template("tools.html")

    @app.get("/help")
    def help_page():
        """初めて開いた人向けのサイトの使い方: 何を入力するか、プレイヤーが見つからない理由、数値の新しさ、
        隠しているプレイヤー、数値の意味と出どころ。"""
        return render_template("help.html")

    @app.get("/leaderboard")
    def leaderboard():
        """保持しているページならすぐ出す。でなければプレイヤー無しのページを出し、スクリプトが頼む（leaderboard_part）
        。プレイヤーページと同じ。``?full=1`` なら待つ。"""
        region, page = _region(), _page_number()
        typed, name, tag = _wanted()
        henrik, act = current_app.config["VAL_HENRIK"], _riot_board_act()
        start = 0 if typed else (page - 1) * BOARD_PAGE
        common = {
            "regions": _regions("leaderboard", region, q=typed or None),
            "region_name": tr(REGION_NAMES[region]),
            "act": act_left(),
        }
        if act:
            at_once = current_app.config["VAL_RIOT"].has_board(region, act, start, BOARD_PAGE, name)
        else:
            at_once = not henrik.key() or henrik.has_board(region, start, name, tag)
        if request.args.get("full") or (typed and not name) or at_once:
            values, status = _board(region, page)
            return render_template("leaderboard.html", **common, **values), status
        return render_template(
            "leaderboard.html", **common, region=region, page=page, q=typed, loading=True, problem=""
        )

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
        values = {"regions": _regions("status", region), "region_name": tr(REGION_NAMES[region]), "notices": []}
        try:
            found = _status(region)
        except HenrikError as error:
            code, problem, settings_link = failure_info(error.kind)
            return render_template("status.html", **values, problem=problem, settings_link=settings_link), code
        return render_template("status.html", **values | {"notices": _notices(found)}, problem="")

    @app.get("/status/notice")
    def status_notice():
        """訪問者のリージョンのメンテナンス・障害についてのトップページの 1 行（無い、または聞けなければ空）
        : スクリプトが差し込む。"""
        region = _region()
        try:
            notices = _notices(_status(region))
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

    @app.get("/numbers/<kind>")
    def numbers(kind: str):
        """このサイトが見た試合から計算した数値（PC アプリ用、 activity.Activity._ask）
        : アプリ自身のファイルには持ち主の試合しか無い。プレイヤーは入っていない。"""
        activity = current_app.config["VAL_ACTIVITY"]
        asked = request.args
        if kind == "agents":
            queue = asked.get("queue", "competitive")
            band = asked.get("band", "")
            map_id = asked.get("map", "")[:40]
            if queue not in LINE_QUEUES or (band and band not in [name for name, _, _ in BANDS]):
                abort(404)
            found = activity.agents(queue, band, map_id)
        elif kind == "ranks":
            found = activity.ranks(_numbers_region())
        elif kind == "servers":
            try:
                offset = max(-12.0, min(14.0, float(asked.get("offset", 9))))
            except ValueError:
                offset = 9.0
            found = activity.servers(_numbers_region(), round(offset * 4) / 4)
        elif kind == "yardstick":
            try:
                tier = max(0, min(27, int(asked.get("tier", 0))))
            except ValueError:
                tier = 0
            stick, lines, band = activity.yardstick(tier)
            # 並べた各一覧の 400 分の 1 ずつで、全体（数 MB の数値）と同じくらいの物差しになる
            thin = {
                stat: values if len(values) <= 400 else [values[i * (len(values) - 1) // 399] for i in range(400)]
                for stat, values in stick.items()
            }
            found = (thin, lines, band)
        else:
            abort(404)
        answer = jsonify(found)
        answer.headers["Cache-Control"] = "public, max-age=600"
        return answer

    @app.get("/servers")
    def servers():
        region = _region()
        values = _servers_page(region)
        values["regions"] = _regions("servers", region)
        values["region"] = region
        values["region_name"] = tr(REGION_NAMES[region])
        return render_template("servers.html", **values)

    @app.get("/ranks")
    def ranks():
        region = _region()
        values = _ranks_page(region)
        values["regions"] = _regions("ranks", region)
        values["region"] = region
        values["region_name"] = tr(REGION_NAMES[region])
        return render_template("ranks.html", **values)

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
        """全員: おすすめバンドル（HenrikDev）。持ち主: 本人のストアも（riot.py）。所持済みに印。"""
        content = current_app.config["VAL_CONTENT"]
        values: dict = {"featured": [], "featured_problem": "", "own": None, "own_problem": "", "own_detail": ""}
        try:
            values["featured"] = store.featured(current_app.config["VAL_HENRIK"].featured(), content, g.lang)
        except HenrikError:
            values["featured_problem"] = tr("読み込めませんでした。")
        for bundle in values["featured"]:
            bundle["left"] = _left(bundle["until"])
        values["vp_icon"] = (content.index("currencies", g.lang).get(store.VP) or {}).get("displayIcon")
        reset = _next_reset(time.time())
        values["reset"] = {"left": _left(reset), "when": moment_info(datetime.fromtimestamp(reset, JST).isoformat())}
        if security.is_owner():
            riot = current_app.config["VAL_RIOT"]
            try:
                found = riot.shop()
            except RiotError as error:
                values["own_problem"] = riot_failure(error.kind)
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
        """持ち主の過去のストア（diary.py）。ほしいスキンに印。"""
        security.owner_only()
        content, diary = current_app.config["VAL_CONTENT"], current_app.config["VAL_DIARY"]
        account = current_app.config["VAL_RIOT"].me()
        wants = set(diary.wants())
        days = []
        for day, entry in diary.days(account):
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
            "shop_history.html", days=days, streak=diary.streak(today, account), vp_icon=vp, seen=seen
        )

    @app.get("/shop/reminder.ics")
    def shop_reminder():
        """デイリーストアが切り替わるとき（UTC の 0 時）に 1 日 1 件の予定を入れるカレンダー（どのカレンダーアプリでも）
        。"""
        return _reminder()
