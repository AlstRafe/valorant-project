"""The pages: the search and favourites (top), a player, a match, the agents, the owner's collection and settings."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Flask, abort, current_app, g, make_response, redirect, render_template, request, send_file, url_for

from valtracker import collection as owned_items
from valtracker import i18n, security, stats
from valtracker.henrik import MODES, REGIONS, HenrikError, shows_rr, split_riot_id
from valtracker.riot import AUTHORIZE, RiotError

#: HenrikDev's queue ids -> what the game calls them (translated in locale.py).
QUEUES = {
    "competitive": "コンペティティブ",
    "unrated": "アンレート",
    "swiftplay": "スイフトプレイ",
    "spikerush": "スパイクラッシュ",
    "deathmatch": "デスマッチ",
    "ggteam": "エスカレーション",
    "onefa": "レプリケーション",
    "hurm": "チームデスマッチ",
    "premier": "プレミア",
    "custom": "カスタム",
    "newmap": "新マップ",
    "snowball": "スノーボールファイト",
    "skirmish_1v1": "スカーミッシュ",
    "skirmish_2v2": "スカーミッシュ",
}
#: The filter above a player's matches, in the game's order ("" is every mode; the names are QUEUES').
MODE_FILTER = (
    "",
    "competitive",
    "unrated",
    "swiftplay",
    "spikerush",
    "deathmatch",
    "teamdeathmatch",
    "escalation",
    "premier",
)
#: Why a player or match could not be shown -> (HTTP status, the visitor's words, the owner's words).
FAILURES = {
    "nokey": (
        503,
        "戦績の検索はまだ準備中です。",
        "HenrikDev の API キーがまだ設定されていません。設定から入れてください。",
    ),
    "badkey": (
        503,
        "戦績を取得できませんでした。時間をおいてもう一度どうぞ。",
        "HenrikDev の API キーが受け付けられませんでした。設定で確かめてください。",
    ),
    "notfound": (404, "プレイヤーが見つかりませんでした。名前とタグを確かめてください。", None),
    "busy": (503, "混み合っています。少し待ってからもう一度どうぞ。", None),
    "down": (503, "戦績のサービスから答えがありませんでした。時間をおいてもう一度どうぞ。", None),
}

#: Why the owner's Riot account could not be read (the owner's pages: Japanese only).
RIOT_FAILURES = {
    "nologin": "Riot アカウントがまだ連携されていません。設定で ssid を貼ってください。",
    "badpaste": "貼られたものが ssid ではありませんでした。手順 3 の値（Value）をそのまま貼ってください。",
    "signedout": "Riot のログインが切れています。もう一度ログインして、新しい ssid を設定で貼ってください。",
    "busy": "Riot のサーバーが混み合っています。少し待ってからもう一度どうぞ。",
    "down": "Riot のサーバーから答えがありませんでした。時間をおいてもう一度どうぞ。",
}


def _t(ja: str, **values) -> str:
    return i18n.text(getattr(g, "lang", i18n.DEFAULT), ja, **values)


def _failure(kind: str) -> tuple[int, str, bool]:
    """(status, the words in the visitor's language, whether a link to the settings helps)."""
    status, visitor, owner = FAILURES.get(kind, FAILURES["down"])
    if owner and security.is_owner():
        return status, _t(owner), kind in ("nokey", "badkey")
    return status, _t(visitor), False


def _queue(metadata: dict) -> str:
    queue = metadata.get("queue") or {}
    key = str(queue.get("id") or "").lower()
    return _t(QUEUES[key]) if key in QUEUES else str(queue.get("name") or key or "")


def _rank(tier: dict | None) -> dict:
    """HenrikDev's ``{"id": 21, "name": "Ascendant 1"}`` in the visitor's language, with its picture."""
    tier = tier or {}
    try:
        number = int(tier.get("id") or 0)
    except (TypeError, ValueError):
        number = 0
    known = current_app.config["VAL_CONTENT"].tiers(g.lang).get(number) or {}
    name = str(known.get("tierName") or tier.get("name") or "")
    return {"tier": number, "name": name.title() if name.isupper() else name, "icon": known.get("smallIcon")}


def _agent(agent: dict | None) -> dict:
    agent = agent or {}
    known = current_app.config["VAL_CONTENT"].agent_index(g.lang).get(str(agent.get("id") or "").lower()) or {}
    return {
        "id": str(agent.get("id") or ""),
        "name": str(known.get("displayName") or agent.get("name") or ""),
        "icon": known.get("displayIcon"),
    }


def _map(metadata: dict) -> dict:
    found = metadata.get("map") or {}
    index = current_app.config["VAL_CONTENT"].map_index(g.lang)
    known = index.get(str(found.get("id") or "").lower()) or index.get(str(found.get("name") or "").lower()) or {}
    return {
        "name": str(known.get("displayName") or found.get("name") or ""),
        "image": known.get("listViewIcon"),
    }


def _row(match: dict, player: dict, rr_changes: dict) -> dict:
    """One match in a player's list."""
    metadata = match.get("metadata") or {}
    match_id = str(metadata.get("match_id") or "")
    rounds = stats.rounds_played(match)
    agent = _agent(player.get("agent"))
    moment = stats.started(match)
    return {
        "id": match_id,
        "region": str(metadata.get("region") or ""),
        "map": _map(metadata),
        "queue": _queue(metadata),
        "started": moment,
        "when": i18n.when(g.lang, moment) if moment else "",
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_icon": agent["icon"],
        "result": stats.team_result(match, str(player.get("team_id") or "")),
        "placing": stats.placing(match, player),
        "rr": (rr_changes.get(match_id) or {}).get("change"),
        "line": stats.line(player, rounds),
        "rounds": rounds,
        # Deathmatch, Skirmish and the like would skew the averages: the summary counts the standard games.
        "standard": str((metadata.get("queue") or {}).get("mode_type") or "Standard") == "Standard",
    }


def _mode() -> str:
    """The mode the visitor narrowed a player's matches to (``?mode=``), or "" for all of them."""
    mode = request.args.get("mode", "")
    return mode if mode in MODES else ""


def _modes(mode: str) -> list[dict]:
    return [
        {"key": key, "name": _t(QUEUES[MODES[key]]) if key else _t("すべて"), "current": key == mode}
        for key in MODE_FILTER
    ]


def _standing(current: dict, season: dict | None) -> dict:
    """What else the rank card says: the placement games left, the leaderboard place, the latest act's record."""

    def number(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    board = current.get("leaderboard_placement")
    act = {}
    if isinstance(season, dict) and number(season.get("games")):
        short = str((season.get("season") or {}).get("short") or "") if isinstance(season.get("season"), dict) else ""
        games, wins = number(season.get("games")), number(season.get("wins"))
        act = {
            "name": re.sub(r"(\d)a(\d)", r"\1 A\2", short.lower()).upper(),
            "games": games,
            "wins": wins,
            "win_rate": round(100 * wins / games),
        }
    return {
        "placements": number(current.get("games_needed_for_rating")),
        "leaderboard": number(board.get("rank")) if isinstance(board, dict) else 0,
        "act": act,
    }


def _player(name: str, tag: str, mode: str = "") -> tuple[dict, int]:
    """What the player page shows, and its HTTP status. The account comes first (its region is needed); then
    the rank and the matches are asked for at the same time."""
    henrik = current_app.config["VAL_HENRIK"]
    try:
        account = henrik.account(name, tag)
    except HenrikError as error:
        status, problem, settings_link = _failure(error.kind)
        return {"name": name, "tag": tag, "problem": problem, "settings_link": settings_link, "account": None}, status
    region = str(account.get("region") or "ap")
    name, tag = str(account.get("name") or name), str(account.get("tag") or tag)
    with ThreadPoolExecutor(3, thread_name_prefix="henrik") as pool:
        asked_mmr = pool.submit(henrik.mmr, region, name, tag)
        asked_matches = pool.submit(henrik.matches, region, name, tag, mode)
        asked_rr = pool.submit(henrik.rr_changes, region, name, tag) if shows_rr(mode) else None
    try:
        mmr = asked_mmr.result()
    except HenrikError:
        mmr = None  # unranked, or the rank could not be read: the matches are still worth showing
    try:
        rr_changes = asked_rr.result() if asked_rr else {}
    except HenrikError:
        rr_changes = {}  # the list only goes without the RR each match gave
    rows, matches_problem = [], ""
    try:
        for match in asked_matches.result():
            found = stats.find_player(match, str(account.get("puuid") or ""), name, tag)
            if found:
                rows.append(_row(match, found, rr_changes))
    except HenrikError as error:
        matches_problem = _failure(error.kind)[1]
    current = (mmr or {}).get("current") or {}
    peak = (mmr or {}).get("peak") or {}
    return {
        "name": name,
        "tag": tag,
        "account": account,
        "region": region,
        "card": str(account.get("card") or ""),
        "rank": _rank(current.get("tier")) if current else None,
        "rr": current.get("rr"),
        "last_change": current.get("last_change"),
        "peak": _rank(peak.get("tier")) if peak else None,
        "standing": _standing(current, (mmr or {}).get("season")),
        "rows": rows,
        "summary": stats.summary(rows),
        "matches_problem": matches_problem,
        "mode": mode,
        "modes": _modes(mode),
        "problem": "",
        "settings_link": False,
    }, 200


def register(app: Flask) -> None:
    @app.get("/")
    def top():
        return render_template("home.html", query="", problem="")

    @app.get("/search")
    def search():
        query = request.args.get("q", "").strip()[:40]
        found = split_riot_id(query)
        if not found:
            problem = _t("「名前#タグ」の形で入れてください（例: Name#JP1）。") if query else ""
            return render_template("home.html", query=query, problem=problem), 400 if query else 200
        return redirect(url_for("player", name=found[0], tag=found[1]), 303)

    @app.get("/p/<name>/<tag>")
    def player(name: str, tag: str):
        """At once: what HenrikDev already told us, or the page without the rank and matches, which the page's
        script then asks for (player_part). HenrikDev takes seconds, and the browser showed nothing meanwhile.
        ``?full=1`` waits for them (the link for a browser without JavaScript)."""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        henrik, mode = current_app.config["VAL_HENRIK"], _mode()
        if request.args.get("full") or not henrik.key() or henrik.has_player(name, tag, mode):  # an answer at once
            values, status = _player(name, tag, mode)
            return render_template("player.html", **values), status
        return render_template(
            "player.html", name=name, tag=tag, mode=mode, loading=True, account=None, problem="", settings_link=False
        )

    @app.get("/p/<name>/<tag>/part")
    def player_part(name: str, tag: str):
        """The player's rank and matches, as the part of the page that the script puts in place."""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        start = time.monotonic()
        values, status = _player(name, tag, _mode())
        response = make_response(render_template("_player.html", **values), status)
        response.headers["Cache-Control"] = "no-store"
        # how long HenrikDev took, for the browser's developer tools (Network -> Timing)
        response.headers["Server-Timing"] = f"henrik;dur={(time.monotonic() - start) * 1000:.0f}"
        return response

    @app.get("/m/<region>/<match_id>")
    def match(region: str, match_id: str):
        if region not in REGIONS:
            abort(404)
        try:
            found = current_app.config["VAL_HENRIK"].match(region, match_id)
        except HenrikError as error:
            status, problem, settings_link = _failure(error.kind)
            if error.kind == "notfound":
                abort(404)
            return render_template("match.html", problem=problem, settings_link=settings_link), status
        metadata = found.get("metadata") or {}
        rounds = stats.rounds_played(found)
        me = request.args.get("me", "").lower()  # the player whose list the visitor came from: their line stands out

        def lines(team_id: str | None) -> list[dict]:
            players = [
                {
                    "name": str(player.get("name") or ""),
                    "tag": str(player.get("tag") or ""),
                    "agent": _agent(player.get("agent")),
                    "rank": _rank(player.get("tier")),
                    "line": stats.line(player, rounds),
                    "me": f"{player.get('name')}#{player.get('tag')}".lower() == me,
                }
                for player in found.get("players") or []
                if isinstance(player, dict) and (team_id is None or str(player.get("team_id") or "") == team_id)
            ]
            # by combat score; without rounds (Deathmatch and the like) there is none, so by kills
            players.sort(key=lambda player: -(player["line"]["acs"] if rounds else player["line"]["kills"]))
            return players

        teams = []
        for team in found.get("teams") or []:
            team_id = str(team.get("team_id") or "")
            teams.append({"id": team_id, "result": stats.team_result(found, team_id), "players": lines(team_id)})
        if not any(team["players"] for team in teams):  # Deathmatch: everyone for themselves, in one table
            teams = [{"id": "", "result": None, "players": lines(None)}]
        moment = stats.started(found)
        length = int((metadata.get("game_length_in_ms") or 0) / 60000)
        return render_template(
            "match.html",
            problem="",
            map=_map(metadata),
            queue=_queue(metadata),
            started=moment,
            when=i18n.when(g.lang, moment) if moment else "",
            minutes=length,
            rounds=rounds,
            teams=teams,
        )

    @app.get("/agents")
    def agents():
        listed = current_app.config["VAL_CONTENT"].agents(g.lang)
        return render_template("agents.html", agents=listed)

    @app.get("/agents/<uuid>")
    def agent(uuid: str):
        found = current_app.config["VAL_CONTENT"].agent(g.lang, uuid)
        if not found:
            abort(404)
        abilities = [item for item in found.get("abilities") or [] if isinstance(item, dict)]
        order = {"Ability1": 1, "Ability2": 2, "Grenade": 0, "Ultimate": 3, "Passive": 4}
        abilities.sort(key=lambda item: order.get(str(item.get("slot")), 5))
        return render_template("agent.html", agent=found, abilities=abilities)

    @app.get("/media/<path:path>")
    def media(path: str):
        found = current_app.config["VAL_MEDIA"].file(path)
        if found is None:
            abort(404)
        response = send_file(found, mimetype="image/png", max_age=30 * 24 * 3600, conditional=True)
        response.headers["Cache-Control"] = "public, max-age=2592000"
        return response

    @app.get("/collection")
    def collection():
        """The owner's own skins, buddies, cards and the rest, from their Riot account (riot.py)."""
        security.owner_only()
        show = request.args.get("show", "skins")
        show = show if show in owned_items.TABS else "skins"
        try:
            found = current_app.config["VAL_RIOT"].collection()
        except RiotError as error:
            status = 503 if error.kind in ("busy", "down") else 200
            values = {"problem": RIOT_FAILURES.get(error.kind, RIOT_FAILURES["down"]), "detail": error.detail}
            return render_template("collection.html", **values, settings_link=status == 200), status
        values = owned_items.page(found, current_app.config["VAL_CONTENT"], g.lang, show)
        return render_template("collection.html", **values)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        security.owner_only()
        store, riot = current_app.config["VAL_SETTINGS"], current_app.config["VAL_RIOT"]
        saved = riot_problem = ""
        if request.method == "POST":
            do = request.form.get("do")
            if do == "clear":
                store.save(henrik_key="")
                saved = "API キーを消しました。"
            elif do == "riot":
                try:
                    linked = riot.sign_in(request.form.get("riot", ""))
                    saved = f"Riot アカウント {linked['name']}#{linked['tag']} と連携しました。"
                except RiotError as error:
                    riot_problem = RIOT_FAILURES.get(error.kind, RIOT_FAILURES["down"])
                    riot_problem += f"（{error.detail}）" if error.detail else ""
            elif do == "riot_off":
                riot.sign_out()
                saved = "Riot アカウントとの連携を解除しました。"
            else:
                key = request.form.get("henrik_key", "").strip()
                if key:
                    store.save(henrik_key=key[:200])
                    saved = "API キーを保存しました。"
        key = store.henrik_key()
        shown = f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("●●●●" if key else "")
        status = riot.status()
        until = datetime.fromtimestamp(status["until"], stats.JST).strftime("%H:%M") if status["until"] else ""
        return render_template(
            "settings.html",
            shown=shown,
            saved=saved,
            riot=status,
            riot_until=until,
            riot_problem=riot_problem,
            riot_link=AUTHORIZE,
        )

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", status=404, message=_t("ページが見つかりません")), 404

    @app.errorhandler(400)
    @app.errorhandler(403)
    def refused(error):
        return render_template("error.html", status=error.code, message=_t("この操作はできませんでした。")), error.code
