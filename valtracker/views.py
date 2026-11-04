"""The pages: the search and favourites (top), a player, a match, the agents, the owner's settings."""

from __future__ import annotations

from flask import Flask, abort, current_app, g, redirect, render_template, request, send_file, url_for

from valtracker import i18n, security, stats
from valtracker.henrik import REGIONS, HenrikError, split_riot_id

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
}
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


def _row(match: dict, player: dict) -> dict:
    """One match in a player's list."""
    metadata = match.get("metadata") or {}
    rounds = stats.rounds_played(match)
    agent = _agent(player.get("agent"))
    moment = stats.started(match)
    return {
        "id": str(metadata.get("match_id") or ""),
        "region": str(metadata.get("region") or ""),
        "map": _map(metadata),
        "queue": _queue(metadata),
        "started": moment,
        "when": i18n.when(g.lang, moment) if moment else "",
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_icon": agent["icon"],
        "result": stats.team_result(match, str(player.get("team_id") or "")),
        "line": stats.line(player, rounds),
        "rounds": rounds,
    }


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
        henrik = current_app.config["VAL_HENRIK"]
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        try:
            account = henrik.account(name, tag)
        except HenrikError as error:
            status, problem, settings_link = _failure(error.kind)
            return render_template(
                "player.html", name=name, tag=tag, problem=problem, settings_link=settings_link, account=None
            ), status
        region = str(account.get("region") or "ap")
        name, tag = str(account.get("name") or name), str(account.get("tag") or tag)
        try:
            mmr = henrik.mmr(region, name, tag)
        except HenrikError:
            mmr = None  # unranked, or the rank could not be read: the matches are still worth showing
        rows, matches_problem = [], ""
        try:
            for match in henrik.matches(region, name, tag):
                found = stats.find_player(match, str(account.get("puuid") or ""), name, tag)
                if found:
                    rows.append(_row(match, found))
        except HenrikError as error:
            matches_problem = _failure(error.kind)[1]
        current = (mmr or {}).get("current") or {}
        peak = (mmr or {}).get("peak") or {}
        return render_template(
            "player.html",
            name=name,
            tag=tag,
            account=account,
            region=region,
            card=str(account.get("card") or ""),
            rank=_rank(current.get("tier")) if current else None,
            rr=current.get("rr"),
            last_change=current.get("last_change"),
            peak=_rank(peak.get("tier")) if peak else None,
            rows=rows,
            summary=stats.summary(rows),
            matches_problem=matches_problem,
            problem="",
            settings_link=False,
        )

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
        teams = []
        for team in found.get("teams") or []:
            team_id = str(team.get("team_id") or "")
            players = [
                {
                    "name": str(player.get("name") or ""),
                    "tag": str(player.get("tag") or ""),
                    "agent": _agent(player.get("agent")),
                    "rank": _rank(player.get("tier")),
                    "line": stats.line(player, rounds),
                }
                for player in found.get("players") or []
                if str(player.get("team_id") or "") == team_id
            ]
            players.sort(key=lambda player: -player["line"]["acs"])
            teams.append({"id": team_id, "result": stats.team_result(found, team_id), "players": players})
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

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        security.owner_only()
        store = current_app.config["VAL_SETTINGS"]
        saved = ""
        if request.method == "POST":
            if request.form.get("do") == "clear":
                store.save(henrik_key="")
                saved = "API キーを消しました。"
            else:
                key = request.form.get("henrik_key", "").strip()
                if key:
                    store.save(henrik_key=key[:200])
                    saved = "API キーを保存しました。"
        key = store.henrik_key()
        shown = f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("●●●●" if key else "")
        return render_template("settings.html", shown=shown, saved=saved)

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", status=404, message=_t("ページが見つかりません")), 404

    @app.errorhandler(400)
    @app.errorhandler(403)
    def refused(error):
        return render_template("error.html", status=error.code, message=_t("この操作はできませんでした。")), error.code
