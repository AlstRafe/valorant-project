"""The pages: the search and favourites (top), a player, a match, the agents, the owner's collection and settings
(the leaderboard, the server status, crosshairs and the store are tools.py's)."""

from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import unquote

from flask import (
    Flask,
    abort,
    current_app,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from valtracker import archive as archive_module
from valtracker import collection as owned_items
from valtracker import heatmap, i18n, pc, release, score, security, stats
from valtracker import rounds as rounds_module
from valtracker.activity import BANDS, LINE_QUEUES, LINE_STATS, RANKS_ENOUGH
from valtracker.henrik import FRESH_AFTER, MODES, NAME, REGIONS, HenrikError, shows_rr, split_riot_id
from valtracker.media import kind as media_kind
from valtracker.pdmatch import MODE_TYPES
from valtracker.riot import AUTHORIZE, RiotError
from valtracker.update import UpdateError

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
#: The servers (HenrikDev's ``cluster``, the game's names in English) -> the city in Japanese (translated); any
#: other is shown as HenrikDev names it.
CLUSTERS = {
    "Tokyo": "東京",
    "Hong Kong": "香港",
    "Singapore": "シンガポール",
    "Sydney": "シドニー",
    "Mumbai": "ムンバイ",
    "Seoul": "ソウル",
    "Frankfurt": "フランクフルト",
    "London": "ロンドン",
    "Paris": "パリ",
    "Stockholm": "ストックホルム",
    "Madrid": "マドリード",
    "Warsaw": "ワルシャワ",
    "Istanbul": "イスタンブール",
    "Bahrain": "バーレーン",
    "Tel Aviv": "テルアビブ",
    "N. Virginia": "バージニア北部",
    "Oregon": "オレゴン",
    "N. California": "北カリフォルニア",
    "Texas": "テキサス",
    "Illinois": "イリノイ",
    "Georgia": "ジョージア",
    "Mexico City": "メキシコシティ",
    "Miami": "マイアミ",
    "Santiago": "サンティアゴ",
    "São Paulo": "サンパウロ",
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
#: A player's lifetime list: matches on one page of it; the RR chart's and the score chart's points at most (a
#: longer line is drawn whole, with fewer dots, or in averages); the dots on each kill map (the newest).
LIFE_PAGE = 20
RR_DOTS = 60
SCORE_POINTS = 40
HEAT_DOTS = 800
#: How long a player's lifetime, once worked out, is kept when nothing of theirs is added (seconds).
LIFE_KEEP = 900

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

#: Why the owner's Riot account could not be read (riot_failure: in the page's language).
RIOT_FAILURES = {
    "nologin": "Riot アカウントがまだ連携されていません。設定で ssid を貼ってください。",
    # the PC app: the account is the Riot Client's
    "noclient": "Riot クライアントが開いていないか、サインインしていません。開いてサインインすると自動でつながります。",
    "badpaste": "貼られたものが ssid ではありませんでした。手順 3 の値（Value）をそのまま貼ってください。",
    "signedout": "Riot のログインが切れています。もう一度ログインして、新しい ssid を設定で貼ってください。",
    "busy": "Riot のサーバーが混み合っています。少し待ってからもう一度どうぞ。",
    "down": "Riot のサーバーから答えがありませんでした。時間をおいてもう一度どうぞ。",
}


def _t(ja: str, **values) -> str:
    return i18n.text(getattr(g, "lang", i18n.DEFAULT), ja, **values)


def riot_failure(kind: str) -> str:
    """Why the owner's Riot account could not be read (RiotError's kind), in the page's language."""
    if kind == "nologin" and current_app.config.get("VAL_LOCAL") is not None:
        kind = "noclient"
    return _t(RIOT_FAILURES.get(kind, RIOT_FAILURES["down"]))


def _failure(kind: str) -> tuple[int, str, bool]:
    """(status, the words in the visitor's language, whether a link to the settings helps)."""
    status, visitor, owner = FAILURES.get(kind, FAILURES["down"])
    if owner and security.is_owner():
        return status, _t(owner), kind in ("nokey", "badkey")
    return status, _t(visitor), False


def cluster(name) -> str:
    """A server's name in the visitor's language."""
    name = str(name or "")
    return _t(CLUSTERS[name]) if name in CLUSTERS else name


#: A profile's address pasted into the search instead of a Riot ID: tracker.gg's, and this site's own.
PROFILE_LINKS = (
    re.compile(r"tracker\.gg/valorant/profile/riot/([^/?#\s]+)", re.IGNORECASE),
    re.compile(r"/p/([^/?#\s]+)/([^/?#\s]+)"),
)


def _from_link(text: str) -> str:
    """``Name#TAG`` out of a pasted profile address; anything else as it is."""
    if "/" not in text:
        return text
    for pattern in PROFILE_LINKS:
        found = pattern.search(text)
        if found:
            return "#".join(unquote(part) for part in found.groups())
    return text


def act_left() -> dict | None:
    """The act being played and the time left in it (content.Content.act): ``{"name", "text", "ends"}``."""
    found = current_app.config["VAL_CONTENT"].act(g.lang)
    if not found:
        return None
    seconds = found["ends"].timestamp() - time.time()
    if seconds <= 0:
        return None
    if seconds >= 2 * 86400:
        text = _t("{act} の終わりまであと {days} 日", act=found["name"], days=int(seconds // 86400))
    else:
        text = _t("{act} の終わりまであと {hours} 時間", act=found["name"], hours=max(1, math.ceil(seconds / 3600)))
    return {"name": found["name"], "text": text, "ends": found["ends"]}


def _queue(metadata: dict) -> str:
    queue = metadata.get("queue") or {}
    key = str(queue.get("id") or "").lower()
    return _t(QUEUES[key]) if key in QUEUES else str(queue.get("name") or key or "")


def _rank(tier: dict | None, old: bool = False) -> dict:
    """HenrikDev's ``{"id": 21, "name": "Ascendant 1"}`` in the visitor's language, with its picture."""
    tier = tier or {}
    try:
        number = int(tier.get("id") or 0)
    except (TypeError, ValueError):
        number = 0
    known = current_app.config["VAL_CONTENT"].tiers(g.lang, old=old).get(number) or {}
    name = str(known.get("tierName") or tier.get("name") or "")
    return {"tier": number, "name": name.title() if name.isupper() else name, "icon": known.get("smallIcon")}


def _agent(agent: dict | None) -> dict:
    agent = agent or {}
    known = current_app.config["VAL_CONTENT"].agent_index(g.lang).get(str(agent.get("id") or "").lower()) or {}
    role = known.get("role") if isinstance(known.get("role"), dict) else {}
    return {
        "id": str(agent.get("id") or ""),
        "name": str(known.get("displayName") or agent.get("name") or ""),
        "icon": known.get("displayIcon"),
        "role": {"name": str(role.get("displayName") or ""), "icon": role.get("displayIcon")},
    }


def _map_item(metadata: dict) -> dict:
    """valorant-api.com's map that a match was played on ({} when not known)."""
    found = metadata.get("map") or {}
    index = current_app.config["VAL_CONTENT"].map_index(g.lang)
    return index.get(str(found.get("id") or "").lower()) or index.get(str(found.get("name") or "").lower()) or {}


def _map(metadata: dict) -> dict:
    known = _map_item(metadata)
    found = metadata.get("map") or {}
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
    adv = stats.advanced(match, player)
    result = stats.team_result(match, str(player.get("team_id") or ""))
    side = ((match.get("digest") or {}).get("sides") or {}).get(str(player.get("team_id") or ""))
    everyone = [other for other in match.get("players") or [] if isinstance(other, dict)]
    place = next((number for number, other in enumerate(everyone) if other is player), -1)
    return {
        "id": match_id,
        "region": str(metadata.get("region") or ""),
        "map": _map(metadata),
        "queue": _queue(metadata),
        "server": cluster(metadata.get("cluster")),
        "started": moment,
        "when": i18n.when(g.lang, moment) if moment else "",
        "length": int(metadata.get("game_length_in_ms") or 0),
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_icon": agent["icon"],
        "role": agent["role"],
        "result": result,
        "placing": stats.placing(match, player),
        "rr": (rr_changes.get(match_id) or {}).get("change"),
        "line": stats.line(player, rounds, adv),
        "adv": adv,
        "extra": rounds_module.extras(match, place) if result["rounds"] else {},
        "rounds": rounds,
        "side": side if result["rounds"] else None,
        "weapons": adv.get("weapons") or {},
        "weapon_names": (match.get("digest") or {}).get("weapons") or {},
        "lobby": stats.lobby(match, player, rounds) if result["rounds"] else None,
        "mates": stats.mates(match, player),
        # Deathmatch, Skirmish and the like would skew the averages: the summary counts the standard games.
        "standard": str((metadata.get("queue") or {}).get("mode_type") or "Standard") == "Standard",
        "queue_id": str((metadata.get("queue") or {}).get("id") or "").lower(),
        "tier": _tier_number(player.get("tier")),
    }


def _tier_number(tier) -> int:
    number = tier.get("id") if isinstance(tier, dict) else tier
    try:
        return int(number or 0)
    except (TypeError, ValueError):
        return 0


def _band_name(name: str) -> str:
    """A rank band (activity.BANDS) in the visitor's words: "Platinum – Diamond"; every band for ""."""
    for key, low, high in BANDS:
        if key == name:
            first, last = (_family(_rank({"id": tier})["name"]) for tier in (low, high))
            return first if first == last else f"{first} – {last}"
    return _t("すべてのランク")


def _family(name: str) -> str:
    """A rank's name without its number: "Gold 2" -> "Gold"."""
    return re.sub(r"\s*\d+$", "", name)


def share_text(value: float) -> str:
    """A percentage for the page: one decimal under 1% (0.4), else whole (12); none at all is 0."""
    return "0" if value <= 0 else f"{value:.1f}" if value < 1 else str(round(value))


def rank_top(region: str, tier: int) -> str | None:
    """ "Top X%" of a rank in a region: the share of the competitive lines seen there at that rank or above
    (activity.Activity.ranks), or None with fewer than RANKS_ENOUGH of them."""
    found = current_app.config["VAL_ACTIVITY"].ranks(region)
    if tier <= 2 or found["lines"] < RANKS_ENOUGH:
        return None
    above = sum(count for number, count in found["tiers"].items() if number >= tier)
    return share_text(100 * above / found["lines"])


def _score(line: dict, rounds: int, queue: str, tier: int, won) -> dict | None:
    """A match's score (score.py) against the lines of the player's rank band: ``{"value", "grade", "standings":
    {stat: 0..1}, "lines", "band"}``, or None (another mode, a remake, too few lines seen yet)."""
    if queue not in LINE_QUEUES or rounds < 5:
        return None
    stick, lines, band = current_app.config["VAL_ACTIVITY"].yardstick(tier)
    value = score.match_score(line, 1.0 if won is True else 0.0 if won is False else 0.5, stick)
    if value is None:
        return None
    return {
        "value": value,
        "grade": score.grade(value),
        "standings": {key: score.standing(stick.get(key) or [], line.get(key)) for key in LINE_STATS},
        "lines": lines,
        "band": band,
    }


def _scores(rows: list[dict]) -> dict | None:
    """Each row's score, and over them the average, its grade, "top X%" for each number and the trend (oldest
    first): ``{"value", "grade", "games", "lines", "band", "tops": {stat: X}, "chart"}``, or None."""
    for row in rows:
        # a stored line (archive.py) has no KAST: it would not be scored as the others are
        row["score"] = (
            None
            if row.get("thin")
            else _score(row["line"], row["rounds"], row["queue_id"], row["tier"], row["result"]["won"])
        )
    scored = [row for row in rows if row["score"]]
    if not scored:
        return None
    value = round(sum(row["score"]["value"] for row in scored) / len(scored))
    widest = max(scored, key=lambda row: row["score"]["lines"])["score"]
    return {
        "value": value,
        "grade": score.grade(value),
        "games": len(scored),
        "lines": widest["lines"],
        "band": _band_name(widest["band"]),
        "tops": {key: score.top([row["score"]["standings"][key] for row in scored]) for key in LINE_STATS},
        "chart": _score_chart(_averages([row["score"]["value"] for row in reversed(scored)], SCORE_POINTS))
        | {
            "narrow": _score_chart(_averages([row["score"]["value"] for row in reversed(scored)], NARROW_DOTS), NARROW),
            "first": i18n.day(g.lang, scored[-1]["started"]) if scored[-1].get("started") else "",
            "last": i18n.day(g.lang, scored[0]["started"]) if scored[0].get("started") else "",
        }
        if len(scored) > 1
        else None,
    }


def _averages(values: list[int], most: int) -> list[int]:
    """At most ``most`` values: a longer list in that many runs of about the same length, each its average."""
    if len(values) <= most:
        return values
    size = len(values) / most
    return [
        round(sum(part) / len(part))
        for index in range(most)
        for part in [values[round(index * size) : round((index + 1) * size)]]
        if part
    ]


def _score_chart(values: list[int], size: tuple[int, int] = (420, 130)) -> dict:
    """The scores of the matches as a line, oldest first (an SVG drawn here), with a line where each grade
    begins. Narrower than the RR's (at most SCORE_POINTS dots: a lifetime's in averages), so it stays tall enough
    on a phone; ``size``: the drawing's width and height."""
    width, height = size
    pad = 12

    def y(value: float) -> float:
        return round(pad + (1000 - value) * (height - 2 * pad) / 1000, 1)

    left = 26  # room for the grades' letters
    step = (width - left - pad) / (len(values) - 1)
    dots = [
        {"x": round(left + number * step, 1), "y": y(value), "value": value, "grade": score.grade(value)}
        for number, value in enumerate(values)
    ]
    return {
        "width": width,
        "height": height,
        "path": "M" + " L".join(f"{dot['x']},{dot['y']}" for dot in dots),
        "dots": dots,
        "lines": [{"y": y(low), "grade": letter} for low, letter in score.GRADES if low],
    }


def _weapon(key: str, said: str = "") -> dict:
    """A weapon (or what else killed: an ability) in the visitor's language, with its kill-feed picture."""
    known = current_app.config["VAL_CONTENT"].arsenal(g.lang).get(str(key).lower()) or {}
    return {"name": str(known.get("displayName") or said or key), "icon": known.get("killStreamIcon")}


#: The lobby comparison's rows: key -> what it is called, and how its numbers are written.
LOBBY_LABELS = {
    "acs": ("ACS", "{:.0f}"),
    "adr": ("ADR", "{:.0f}"),
    "kd": ("K/D", "{:.2f}"),
    "hs": ("HS率", "{:.0f}%"),
    "kast": ("KAST", "{:.0f}%"),
    "fk": ("ファーストキル / ラウンド", "{:.2f}"),
    "fd": ("ファーストデス / ラウンド", "{:.2f}"),
}


def _lobby_rows(compared: list[dict]) -> list[dict]:
    """The player beside their lobbies, worded; the best and the weakest of them marked."""
    found = []
    for item in compared:
        label, number = LOBBY_LABELS[item["key"]]
        found.append(
            dict(item, label=_t(label), mine_text=number.format(item["mine"]), lobby_text=number.format(item["lobby"]))
        )
    best = max(found, key=lambda item: item["score"], default=None)
    worst = min(found, key=lambda item: item["score"], default=None)
    for item in found:
        item["strength"] = item is best and item["better"]
        item["weakness"] = item is worst and item["worse"]
    return found


def _rr_chart(rr_changes: dict) -> dict | None:
    """The RR of the competitive matches as a line (an SVG drawn here: no script), oldest first, with a
    line where each rank begins (elo: 100 a rank, rank 3 at 0): the wide drawing, and in ``narrow`` one for a phone
    (taller for its width, so its words stay readable), the first and last days under it."""
    points = sorted(
        (item for item in rr_changes.values() if item.get("elo") and item.get("at")), key=lambda item: item["at"]
    )
    if len(points) < 2:
        return None
    wide = _rr_drawing(points, 800, 150, RR_DOTS)
    first, last = (datetime.fromtimestamp(points[index]["at"], stats.JST) for index in (0, -1))
    return wide | {
        "narrow": _rr_drawing(points, NARROW[0], NARROW[1], NARROW_DOTS),
        "net": sum(item["change"] for item in points),
        "games": len(points),
        "first": i18n.day(g.lang, first),
        "last": i18n.day(g.lang, last),
    }


#: A chart's size on a phone (wide 800 × 150 would be 65 px tall there, its words 5 px), and its dots at most.
NARROW = (360, 190)
NARROW_DOTS = 20


def _rr_drawing(points: list[dict], width: int, height: int, most: int) -> dict:
    pad = 14
    elos = [item["elo"] for item in points]
    low, high = min(elos) - 12, max(elos) + 12
    if high - low < 60:
        middle = (high + low) / 2
        low, high = middle - 30, middle + 30

    def y(elo: float) -> float:
        return round(pad + (high - elo) * (height - 2 * pad) / (high - low), 1)

    step = (width - 2 * pad) / (len(points) - 1)
    every = [
        {
            "x": round(pad + number * step, 1),
            "y": y(item["elo"]),
            "change": item["change"],
            "rank": _rank({"id": item["tier"]}),
            "rr": item["rr"],
        }
        for number, item in enumerate(points)
    ]
    # a lifetime's line: drawn through every match, with a dot on some of them (the first and the latest too)
    gap = max(1, math.ceil(len(every) / most))
    dots = [dot for number, dot in enumerate(every) if number % gap == 0 or number == len(every) - 1]
    lines = []
    for elo in range(int(low // 100 + 1) * 100, int(high) + 1, 100):
        lines.append({"y": y(elo), "rank": _rank({"id": elo // 100 + 3})})
    return {
        "width": width,
        "height": height,
        "path": "M" + " L".join(f"{dot['x']},{dot['y']}" for dot in every),
        "dots": dots,
        "lines": lines,
    }


def _next_rank(rank: dict | None, rr, rr_changes: dict) -> dict | None:
    """How far the next rank is: the RR it needs and, at the player's average gain, about how many wins."""
    if not rank or not 3 <= rank["tier"] < 24 or rr is None:
        return None
    changes = [item.get("change", 0) for item in rr_changes.values()]
    gains = [change for change in changes if change > 0]
    losses = [-change for change in changes if change < 0]
    need = max(1, 100 - int(rr))
    gain = round(sum(gains) / len(gains)) if gains else 0
    # at the player's own win rate and average gain and loss, the RR a match brings on average
    per_match = (sum(gains) - sum(losses)) / len(changes) if changes else 0
    return {
        "need": need,
        "rank": _rank({"id": rank["tier"] + 1}),
        "gain": gain,
        "loss": round(sum(losses) / len(losses)) if losses else 0,
        "wins": -(-need // gain) if gain else None,
        "matches": math.ceil(need / per_match) if per_match > 0 and len(changes) >= 5 else None,
        "flat": len(changes) >= 5 and per_match <= 0,
    }


def _streak(rows: list[dict]) -> dict | None:
    """The wins or losses in a row up to the latest match (newest first; a draw ends it): two or more, or None."""
    results = [row["result"]["won"] for row in rows if row["standard"]]
    if not results or results[0] is None:
        return None
    count = next((index for index, won in enumerate(results) if won is not results[0]), len(results))
    return {"won": results[0], "count": count} if count >= 2 else None


def _weapon_said(rows: list[dict], key: str) -> str:
    """HenrikDev's own name of a weapon (English), for one valorant-api.com does not know."""
    for row in rows:
        said = (row.get("weapon_names") or {}).get(key)
        if said:
            return said
    return ""


def _mode() -> str:
    """The mode the visitor narrowed a player's matches to (``?mode=``), or "" for all of them."""
    mode = request.args.get("mode", "")
    return mode if mode in MODES else ""


def _modes(mode: str) -> list[dict]:
    return [
        {"key": key, "name": _t(QUEUES[MODES[key]]) if key else _t("すべて"), "current": key == mode}
        for key in MODE_FILTER
    ]


def _standing(current: dict) -> dict:
    """What else the rank card says: the placement games left and the leaderboard place."""

    def number(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    board = current.get("leaderboard_placement")
    return {
        "placements": number(current.get("games_needed_for_rating")),
        "leaderboard": number(board.get("rank")) if isinstance(board, dict) else 0,
    }


#: The act rank badge: rows of 1, 3 and 5 triangles, pointing up and down in turn; the best wins fill it
#: from the top, left to right.
BADGE_ROWS = ((True,), (True, False, True), (True, False, True, False, True))


def _acts(seasons: list) -> list[dict]:
    """Each act played, the latest first: its name, record, final rank and badge."""
    acts = []
    for season in seasons:
        tiers = current_app.config["VAL_CONTENT"].tiers(g.lang, old=season.get("old", False))
        best = iter(season.get("best") or [])
        rows = []
        for row in BADGE_ROWS:
            slots = []
            for up in row:
                win = next(best, 0)
                icon = (
                    (tiers.get(win) or {}).get("rankTriangleUpIcon" if up else "rankTriangleDownIcon") if win else None
                )
                slots.append({"up": up, "tier": win, "icon": icon})
            rows.append(slots)
        games, wins = season["games"], season["wins"]
        acts.append(
            {
                "key": season["short"],
                "name": re.sub(r"(\d)a(\d)", r"\1 A\2", season["short"]).upper(),
                "games": games,
                "wins": wins,
                "win_rate": round(100 * wins / games) if games else 0,
                "end": _rank({"id": season.get("end_tier")}, old=season.get("old", False)),
                "board": season.get("board", 0),
                "rows": rows,
                "shown": len(season.get("best") or []),
            }
        )
    return acts


def _player(
    name: str, tag: str, mode: str = "", fresh: bool = False, span: str = "", page: int = 1, act: str = ""
) -> tuple[dict, int]:
    """What the player page shows, and its HTTP status. The account comes first (its region is needed); then
    the rank and the matches are asked for at the same time. ``fresh``: the visitor pressed "最新にする", so
    what is kept of the rank and the matches is asked for again (when it was read over a minute ago). ``span``
    "all": every match kept of the player (archive.py), not the latest RECENT; "act": those of one act (``act``,
    its uuid; the act being played when not given), as tracker.gg counts a season; ``page``: of its list."""
    henrik = current_app.config["VAL_HENRIK"]
    try:
        account = henrik.account(name, tag)
    except HenrikError as error:
        status, problem, settings_link = _failure(error.kind)
        return {"name": name, "tag": tag, "problem": problem, "settings_link": settings_link, "account": None}, status
    region = str(account.get("region") or "ap")
    name, tag = str(account.get("name") or name), str(account.get("tag") or tag)
    if fresh:
        henrik.forget_player(region, name, tag, mode)
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
    rows, matches_problem, spots = [], "", {}
    try:
        listed = asked_matches.result()
        current_app.config["VAL_ACTIVITY"].seen(listed, str(account.get("puuid") or ""))
        current_app.config["VAL_ARCHIVE"].keep(listed, "henrik")
        for match in listed:
            found = stats.find_player(match, str(account.get("puuid") or ""), name, tag)
            if found:
                rows.append(_row(match, found, rr_changes))
                _add_spots(spots, match, found)
    except HenrikError as error:
        matches_problem = _failure(error.kind)[1]
    life, heat_maps, rr_all = None, None, rr_changes
    puuid = str(account.get("puuid") or "").lower()
    window = _act_window(act) if span == "act" else None
    span = "act" if window else "all" if span else ""
    if span and puuid:
        life = _lifetime(puuid, region, name, tag, mode, rr_changes, window)
        rows, heat_maps, rr_all = life.pop("rows"), life.pop("heat_maps"), life.pop("rr")
        if rows:
            matches_problem = ""  # HenrikDev did not answer now, but the matches kept of the player are there
    pages = max(1, math.ceil(len(rows) / LIFE_PAGE)) if life else 1
    page = min(max(1, page), pages)
    shown = rows[(page - 1) * LIFE_PAGE : page * LIFE_PAGE] if life else rows
    current = (mmr or {}).get("current") or {}
    peak = (mmr or {}).get("peak") or {}
    rank = _rank(current.get("tier")) if current else None
    summary = stats.summary(rows)
    today = datetime.now(stats.JST).date()
    played_today = [row for row in rows if row["started"] and row["started"].astimezone(stats.JST).date() == today]
    if summary and played_today:
        today_rr = [
            item["change"]
            for item in rr_changes.values()
            if item.get("at") and datetime.fromtimestamp(item["at"], stats.JST).date() == today
        ]
        summary["today"] = {
            "games": len(played_today),
            "wins": sum(row["result"]["won"] is True for row in played_today),
            "losses": sum(row["result"]["won"] is False for row in played_today),
            "rr": sum(today_rr) if today_rr else None,
            "minutes": round(sum(row["length"] for row in played_today) / 60000),
        }
    scores = _scores(rows) if rows else None
    if summary:
        summary["score"] = scores
        summary["streak"] = _streak(rows)
        for weapon in summary["weapons"]:
            weapon.update(_weapon(weapon["key"], _weapon_said(rows, weapon["key"])))
        summary["lobby"] = _lobby_rows(summary["lobby"])
    return {
        "name": name,
        "tag": tag,
        "account": account,
        "region": region,
        "card": str(account.get("card") or ""),
        "rank": rank,
        "rank_top": rank_top(region, rank["tier"]) if rank else None,
        "rr": current.get("rr"),
        "next_rank": _next_rank(rank, current.get("rr"), rr_changes),
        "act": act_left() if rank else None,
        "rr_chart": _rr_chart(rr_all) if shows_rr(mode) else None,
        "last_change": current.get("last_change"),
        "peak": _rank(peak.get("tier")) if peak else None,
        "standing": _standing(current),
        "acts": _acts((mmr or {}).get("seasons") or []),
        "rows": rows,
        "listed": shown,
        "summary": summary,
        "heat_maps": heat_maps if life else _heat_maps(spots),
        "matches_problem": matches_problem,
        "mode": mode,
        "modes": _modes(mode),
        "span": span if life else "",
        "window": window if life else None,
        "acts_shown": _acts_shown(window, life) if life and window else [],
        "heat_dots": HEAT_DOTS,
        "life": life,
        "pages": {"page": page, "count": pages},
        "read": _read(henrik.read_at(region, name, tag, mode)),
        "problem": "",
        "settings_link": False,
    }, 200


def _act_window(uuid: str = "") -> dict | None:
    """The act a player's numbers are counted over (span "act"): the one asked for, else the one being played, else
    the latest: ``{"uuid", "name", "starts", "ends", "current", "from": its first day in the visitor's words}``.
    None when the acts are not known (valorant-api.com did not answer)."""
    acts = current_app.config["VAL_CONTENT"].acts(g.lang)
    now = time.time()
    current = next((item for item in acts if item["starts"].timestamp() <= now < item["ends"].timestamp()), None)
    found = (
        next((item for item in acts if uuid and item["uuid"] == uuid), None) or current or (acts[0] if acts else None)
    )
    if not found:
        return None
    starts = found["starts"].astimezone(stats.JST)
    return dict(found, current=found is current, **{"from": i18n.day(g.lang, starts)})


def _acts_shown(window: dict, life: dict) -> list[dict]:
    """The acts to choose from on a player's page: from the act of their first kept match to the one being played
    (and the one shown, whatever it is), the latest first."""
    first = life.get("first") or 0
    now = time.time()
    shown = []
    for item in current_app.config["VAL_CONTENT"].acts(g.lang):
        started = item["starts"].timestamp()
        if item["uuid"] == window["uuid"] or (started <= now and (not first or item["ends"].timestamp() > first)):
            shown.append(
                {"uuid": item["uuid"], "name": item["name"], "current": started <= now < item["ends"].timestamp()}
            )
    return shown


def _lifetime(
    puuid: str, region: str, name: str, tag: str, mode: str, recent_rr: dict, window: dict | None = None
) -> dict:
    """Every match kept of the player (archive.py), as the player page counts them: ``{"rows", "heat_maps", "rr",
    "count", "whole", "thin", "since", "first", "reading"}``; with ``window`` (_act_window), only the matches of
    that act. Asks the background to read the rest of their history. What the rows and drawings come to is kept
    until something of the player is added (or LIFE_KEEP)."""
    archive = current_app.config["VAL_ARCHIVE"]
    state = current_app.config["VAL_FILLER"].want(region, puuid, name, tag)
    archive.keep_rr(puuid, recent_rr)
    queue = MODES.get(mode, "")
    low, high = (window["starts"].timestamp(), window["ends"].timestamp()) if window else (0.0, math.inf)
    key = ("life", puuid, queue, g.lang, archive.version(puuid), window["uuid"] if window else "")
    kept = current_app.config["VAL_LIVES"].get(key)
    if kept is None:
        rr_all = archive.rr(puuid)
        if window:
            rr_all = {match_id: item for match_id, item in rr_all.items() if low <= (item.get("at") or 0) < high}
        rows, spots = [], {}
        for match in archive.matches(puuid, queue):
            moment = stats.started(match)
            if window and not (moment and low <= moment.timestamp() < high):
                continue
            found = stats.find_player(match, puuid)
            if found:
                row = _row(match, found, rr_all)
                # Riot's records name no one but the player (pdmatch.py): their party is left out
                row["mates"] = [mate for mate in row["mates"] if mate["name"] and mate["tag"]]
                rows.append(row)
                _add_spots(spots, match, found)
        whole = len(rows)
        for line in archive.thin(puuid, queue):
            if line.get("rounds"):
                row = _thin_row(line, region, rr_all)
                if not window or (row["started"] and low <= row["started"].timestamp() < high):
                    rows.append(row)
        rows.sort(key=lambda row: row["started"].timestamp() if row["started"] else 0, reverse=True)
        kept = {"rows": rows, "heat_maps": _heat_maps(spots, HEAT_DOTS), "rr": rr_all, "whole": whole}
        current_app.config["VAL_LIVES"].put(key, kept, LIFE_KEEP)
    since = archive.first(puuid)
    return {
        "rows": kept["rows"],
        "heat_maps": kept["heat_maps"],
        "rr": kept["rr"],
        "count": len(kept["rows"]),
        "whole": kept["whole"],
        "thin": len(kept["rows"]) - kept["whole"],
        "since": i18n.day(g.lang, datetime.fromtimestamp(since, stats.JST)) if since else "",
        "first": since or 0,
        "reading": archive_module.progress(state, archive),
    }


def _thin_row(line: dict, region: str, rr_changes: dict) -> dict:
    """A stored line (henrik._slim_stored: the player's numbers, no rounds) as one match of the player's list."""
    match_id = str(line.get("id") or "")
    try:
        moment = datetime.fromisoformat(str(line.get("started") or "").replace("Z", "+00:00")).astimezone(stats.JST)
    except ValueError:
        moment = None
    queue_id = str(line.get("queue") or "")
    rounds = int(line.get("rounds") or 0)
    player = {
        "stats": {
            "score": line.get("score"),
            "kills": line.get("kills"),
            "deaths": line.get("deaths"),
            "assists": line.get("assists"),
            "headshots": line.get("head"),
            "bodyshots": line.get("body"),
            "legshots": line.get("leg"),
            "damage": {"dealt": line.get("made"), "received": line.get("received")},
        }
    }
    agent = _agent({"id": line.get("agent")})
    return {
        "id": match_id,
        "region": region,
        "map": _map({"map": {"id": line.get("map_id") or "", "name": line.get("map") or ""}}),
        "queue": _t(QUEUES[queue_id]) if queue_id in QUEUES else queue_id,
        "server": cluster(line.get("cluster")),
        "started": moment,
        "when": i18n.when(g.lang, moment) if moment else "",
        "length": 0,
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_icon": agent["icon"],
        "role": agent["role"],
        "result": {"won": line.get("won"), "us": line.get("us") or 0, "them": line.get("them") or 0, "rounds": True},
        "placing": {"place": 0, "of": 0, "mvp": ""},
        "rr": (rr_changes.get(match_id) or {}).get("change"),
        "line": stats.line(player, rounds),
        "adv": {},
        "extra": {},
        "rounds": rounds,
        "side": None,
        "weapons": {},
        "weapon_names": {},
        "lobby": None,
        "mates": [],
        "standard": queue_id not in MODE_TYPES,
        "queue_id": queue_id,
        "tier": int(line.get("tier") or 0),
        "thin": True,
    }


def _span() -> str:
    """ "all" when the visitor asked for a player's lifetime (``?span=all``), "act" for one act (``?span=act``,
    ``&act=`` its uuid), else "" (the latest matches)."""
    asked = request.args.get("span")
    return asked if asked in ("all", "act") else ""


def _act() -> str:
    """The act asked for (``?act=``, a uuid), or ""."""
    asked = str(request.args.get("act") or "").lower()
    return asked if re.fullmatch(r"[0-9a-f-]{36}", asked) else ""


def _page() -> int:
    try:
        return max(1, int(request.args.get("page", "1")))
    except ValueError:
        return 1


def _read(at: float) -> dict | None:
    """When the page's numbers were read from HenrikDev, and whether "最新にする" can ask again yet."""
    if not at:
        return None
    age = max(0, time.time() - at)
    return {"minutes": int(age // 60), "again": age >= FRESH_AFTER}


def _add_spots(spots: dict, match: dict, player: dict) -> None:
    """The player's kills and deaths in a match (heatmap.points), added to its map's."""
    everyone = [item for item in match.get("players") or [] if isinstance(item, dict)]
    mine = next((number for number, item in enumerate(everyone) if item is player), -1)
    found = heatmap.points(match, mine)
    if found:
        known = _map_item(match.get("metadata") or {})
        if known.get("uuid"):
            spots.setdefault(str(known["uuid"]).lower(), (known, []))[1].extend(found)


def _heat_maps(spots: dict, most: int | None = None) -> list[dict]:
    """Each map's drawing of the player's kills and deaths (heatmap.drawing), the busiest map first; ``most``: the
    dots drawn on each, at most (the newest)."""
    drawn = []
    for known, found in spots.values():
        plan = heatmap.plan(known)
        drawing = heatmap.drawing(plan, found, most=most) if plan else None
        if drawing:
            drawn.append(dict(drawing, name=str(known.get("displayName") or ""), id=str(known["uuid"]).lower()))
    drawn.sort(key=lambda item: -len(item["dots"]))
    return drawn[: heatmap.MAPS_SHOWN]


#: How a round ended -> the words of its picture's title.
ENDINGS = {
    "elim": "全員倒した",
    "defuse": "スパイク解除",
    "detonate": "スパイク爆発",
    "time": "時間切れ",
    "surrender": "降参",
}


#: A team's buy (_buy) -> its name.
BUYS = {"eco": "エコ", "semi-eco": "セミエコ", "semi-buy": "セミバイ", "full": "フルバイ"}


def _clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _buy(loadout: int, players: int) -> str:
    """A team's buy from its loadout: eco, semi-eco, semi-buy or full (by the average per player)."""
    each = loadout / players if players else 0
    return "eco" if each < 1000 else "semi-eco" if each < 2000 else "semi-buy" if each < 3900 else "full"


def _match_page(found: dict, me: str) -> dict:
    """What a match's page shows: the scoreboards (with the rounds' numbers), the rounds one by one and their
    kills, the parties, and for the player the visitor came from their duels with each opponent."""
    metadata = found.get("metadata") or {}
    rounds = stats.rounds_played(found)
    digest = found.get("digest") or {}
    everyone = [player for player in found.get("players") or [] if isinstance(player, dict)]
    me = me.lower()
    mine = next(
        (n for n, player in enumerate(everyone) if f"{player.get('name')}#{player.get('tag')}".lower() == me), -1
    )
    # parties of two or more, numbered in the order they appear
    sizes: dict[str, int] = {}
    for player in everyone:
        if player.get("party_id"):
            sizes[player["party_id"]] = sizes.get(player["party_id"], 0) + 1
    parties: dict[str, int] = {}
    for player in everyone:
        party = player.get("party_id")
        if party and sizes[party] > 1 and party not in parties:
            parties[party] = len(parties) + 1
    people = [
        {
            "name": str(player.get("name") or ""),
            "tag": str(player.get("tag") or ""),
            "team": str(player.get("team_id") or ""),
            "agent": _agent(player.get("agent")),
            "rank": _rank(player.get("tier")),
            "line": stats.line(player, rounds, stats.advanced(found, player)),
            "tier": _tier_number(player.get("tier")),
            "won": stats.team_result(found, str(player.get("team_id") or ""))["won"],
            "mvp": stats.placing(found, player)["mvp"],
            "party": parties.get(player.get("party_id"), 0),
            "me": number == mine,
        }
        for number, player in enumerate(everyone)
    ]

    queue_id = str((metadata.get("queue") or {}).get("id") or "").lower()
    for person in people:
        person["score"] = _score(person["line"], rounds, queue_id, person["tier"], person["won"])

    def lines(team_id: str | None) -> list[dict]:
        listed = [person for person in people if team_id is None or person["team"] == team_id]
        # by combat score; without rounds (Deathmatch and the like) there is none, so by kills
        listed.sort(key=lambda person: -(person["line"]["acs"] if rounds else person["line"]["kills"]))
        return listed

    teams = []
    for team in found.get("teams") or []:
        team_id = str(team.get("team_id") or "")
        teams.append({"id": team_id, "result": stats.team_result(found, team_id), "players": lines(team_id)})
    if not any(team["players"] for team in teams):  # Deathmatch: everyone for themselves, in one table
        teams = [{"id": "", "result": None, "players": lines(None)}]
    my_team = people[mine]["team"] if mine >= 0 else ""
    teams.sort(key=lambda team: team["id"] != my_team)  # the team of the player the visitor came from, first
    team_sizes = {team["id"]: len(team["players"]) for team in teams}
    queue = str((metadata.get("queue") or {}).get("id") or "").lower()
    half = rounds_module.HALF.get(queue, 0)
    said = digest.get("weapons") or {}
    by_round: dict[int, list] = {}
    for kill in digest.get("kills") or []:
        by_round.setdefault(kill[0], []).append(kill)

    def someone(number: int) -> dict | None:
        return people[number] if 0 <= number < len(people) else None

    timeline = []
    for number, item in enumerate(digest.get("rounds") or []):
        won = item["w"] == my_team if my_team else None
        kills = []
        for kill in by_round.get(number, []):
            killer, victim = someone(kill[2]), someone(kill[3])
            kills.append(
                {
                    "time": _clock(kill[1]),
                    "killer": killer,
                    "victim": victim,
                    "weapon": _weapon(kill[4], said.get(kill[4], "")),
                    "helpers": [someone(helper) for helper in kill[5] if someone(helper)],
                }
            )
        timeline.append(
            {
                "number": number + 1,
                "winner": item["w"],
                "won": won,
                "end": item["e"],
                "end_words": _t(ENDINGS[item["e"]]) if item["e"] in ENDINGS else "",
                "ceremony": item["c"],
                "site": item["s"],
                "attack": item["a"],
                "buys": {team: _buy(value, team_sizes.get(team, 0)) for team, value in (item.get("l") or {}).items()},
                # a gap before the second half and before overtime
                "turn": bool(half) and number in (half, 2 * half),
                "kills": kills,
            }
        )
    duels = []
    if mine >= 0 and digest.get("kills"):
        for number, person in enumerate(people):
            if person["team"] == my_team and my_team:
                continue
            if number == mine:
                continue
            took = sum(1 for kill in digest["kills"] if kill[2] == mine and kill[3] == number)
            lost = sum(1 for kill in digest["kills"] if kill[2] == number and kill[3] == mine)
            if took or lost:
                duels.append({"person": person, "took": took, "lost": lost})
        duels.sort(key=lambda duel: (-(duel["took"] - duel["lost"]), -duel["took"]))
    tiers = [person["rank"]["tier"] for person in people if person["rank"]["tier"] > 2]
    average = _rank({"id": round(sum(tiers) / len(tiers))}) if tiers else None
    moment = stats.started(found)
    scored = any(person["score"] for person in people)
    plan = heatmap.plan(_map_item(metadata)) if mine >= 0 else None
    return {
        "heat": heatmap.drawing(plan, heatmap.points(found, mine), lines=True) if plan else None,
        "scored": scored,
        "map": _map(metadata),
        "queue": _queue(metadata),
        "server": cluster(metadata.get("cluster")),
        "started": moment,
        "when": i18n.when(g.lang, moment) if moment else "",
        "minutes": int((metadata.get("game_length_in_ms") or 0) / 60000),
        "rounds": rounds,
        "teams": teams,
        "timeline": timeline,
        "my_team": my_team,
        "detailed": bool(digest.get("players")),
        "duels": duels,
        "average": average,
        "me": people[mine] if mine >= 0 else None,
        "parties": bool(parties),
        "buy_words": {key: _t(words) for key, words in BUYS.items()},
    }


def _me() -> dict | None:
    """The owner's linked Riot account for the top page's 自分 box (riot.Riot.status: read from this server's own
    file, no call to Riot), or None for a visitor."""
    if not security.is_owner():
        return None
    return current_app.config["VAL_RIOT"].status()


def _no_key() -> bool:
    """The owner has no HenrikDev key yet (the PC app's first start): the top page says where it goes."""
    return security.is_owner() and not current_app.config["VAL_SETTINGS"].henrik_key()


def register(app: Flask) -> None:
    @app.get("/")
    def top():
        return render_template("home.html", query="", problem="", me=_me(), no_key=_no_key())

    @app.get("/search")
    def search():
        query = _from_link(request.args.get("q", "").strip())[:40]
        found = split_riot_id(query)
        if not found:
            # a name without its tag: say what is missing, with that name for the example; "Name JP1" (a space
            # where the # goes, likely; a name may hold spaces, so the Riot ID is only offered)
            name_only = query if query and "#" not in query.replace("＃", "#") and NAME.match(query) else ""
            head, _, last = name_only.rpartition(" ")
            guess = split_riot_id(f"{head}#{last}") if head.strip() else None
            if name_only:
                example = "#".join(guess) if guess else f"{name_only}#JP1"
                problem = _t("タグ（# のあと）も入れてください。例: {example}", example=example)
            else:
                problem = _t("「名前#タグ」の形で入れてください（例: Name#JP1）。") if query else ""
            values = {"query": query, "problem": problem, "name_only": name_only, "guess": guess, "me": _me()}
            values["no_key"] = _no_key()
            return render_template("home.html", **values), 400 if query else 200
        return redirect(url_for("player", name=found[0], tag=found[1]), 303)

    @app.get("/p/<name>/<tag>")
    def player(name: str, tag: str):
        """At once: what HenrikDev already told us, or the page without the rank and matches, which the page's
        script then asks for (player_part). HenrikDev takes seconds, and the browser showed nothing meanwhile.
        ``?full=1`` waits for them (the link for a browser without JavaScript)."""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        henrik, mode, fresh = current_app.config["VAL_HENRIK"], _mode(), bool(request.args.get("fresh"))
        kept = henrik.has_player(name, tag, mode) and not fresh
        if request.args.get("full") or not henrik.key() or kept:  # an answer at once
            values, status = _player(name, tag, mode, fresh, _span(), _page(), _act())
            return render_template("player.html", **values), status
        return render_template(
            "player.html",
            name=name,
            tag=tag,
            mode=mode,
            fresh=fresh,
            span=_span(),
            page=_page(),
            act_asked=_act(),
            loading=True,
            account=None,
            problem="",
            settings_link=False,
        )

    @app.get("/p/<name>/<tag>/part")
    def player_part(name: str, tag: str):
        """The player's rank and matches, as the part of the page that the script puts in place."""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        start = time.monotonic()
        values, status = _player(name, tag, _mode(), bool(request.args.get("fresh")), _span(), _page(), _act())
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
        current_app.config["VAL_ACTIVITY"].seen([found])
        current_app.config["VAL_ARCHIVE"].keep([found], "henrik")
        me = request.args.get("me", "")  # the player whose list the visitor came from: their line stands out
        return render_template("match.html", problem="", **_match_page(found, me))

    @app.get("/agents")
    def agents():
        """Every agent, or the ones of a role (``?role=`` its uuid): the roles as links above the list."""
        listed = current_app.config["VAL_CONTENT"].agents(g.lang)
        roles: dict[str, dict] = {}
        for item in listed:
            role = item.get("role") or {}
            if role.get("uuid"):
                roles.setdefault(str(role["uuid"]).lower(), role)
        chosen = request.args.get("role", "").lower()
        chosen = chosen if chosen in roles else ""
        shown = [item for item in listed if not chosen or str((item.get("role") or {}).get("uuid")).lower() == chosen]
        ordered = sorted(roles.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
        return render_template("agents.html", agents=shown, roles=ordered, role=chosen, every=bool(listed))

    @app.get("/agents/<uuid>")
    def agent(uuid: str):
        found = current_app.config["VAL_CONTENT"].agent(g.lang, uuid)
        if not found:
            abort(404)
        abilities = [item for item in found.get("abilities") or [] if isinstance(item, dict)]
        order = {"Ability1": 1, "Ability2": 2, "Grenade": 0, "Ultimate": 3, "Passive": 4}
        abilities.sort(key=lambda item: order.get(str(item.get("slot")), 5))
        return render_template("agent.html", agent=found, abilities=abilities)

    @app.get("/app/latest")
    def app_latest():
        """The newest build of the PC app, for the app's own "there is a newer one" (release.py). The site only:
        the app itself has no clone to read."""
        found = None if current_app.config.get("VAL_LOCAL") is not None else release.latest()
        if not found:
            abort(404)
        answer = jsonify(found)
        answer.headers["Cache-Control"] = "public, max-age=300"
        return answer

    @app.get("/media/<path:path>")
    def media(path: str):
        found = current_app.config["VAL_MEDIA"].file(path)
        if found is None:
            abort(404)
        response = send_file(found, mimetype="image/png", max_age=30 * 24 * 3600, conditional=True)
        response.headers["Cache-Control"] = "public, max-age=2592000"
        return response

    @app.get("/pic/<path:path>")
    def picture(path: str):
        """A news or esports picture (media.OUTSIDE), kept here like the game's."""
        found = current_app.config["VAL_MEDIA"].outside(path)
        if found is None:
            abort(404)
        with open(found, "rb") as file:
            kind = media_kind(file.read(16)) or "image/png"
        response = send_file(found, mimetype=kind, max_age=30 * 24 * 3600, conditional=True)
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
            values = {"problem": riot_failure(error.kind), "detail": error.detail}
            return render_template("collection.html", **values, settings_link=status == 200), status
        weapon = request.args.get("weapon", "")[:36] if show == "skins" else ""
        sets = [item for item in current_app.config["VAL_SETS"].load().get("sets") or [] if isinstance(item, dict)]
        values = owned_items.page(found, current_app.config["VAL_CONTENT"], g.lang, show, weapon, sets)
        from valtracker.owner import equip_said

        values["equip_said"], values["equip_ok"] = equip_said(request.args.get("equip", "")[:20])
        values["equip_detail"] = "" if values["equip_ok"] else request.args.get("riot", "")[:80]
        return render_template("collection.html", **values)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        security.owner_only()
        store, riot = current_app.config["VAL_SETTINGS"], current_app.config["VAL_RIOT"]
        autostart = current_app.config["VAL_AUTOSTART"]
        saved = riot_problem = riot_detail = ""
        if request.method == "POST":
            do = request.form.get("do")
            if do == "clear":
                store.save(henrik_key="")
                saved = _t("API キーを消しました。")
            elif do == "riot":
                try:
                    linked = riot.sign_in(request.form.get("riot", ""))
                    saved = _t(
                        "Riot アカウント {account} と連携しました。", account=f"{linked['name']}#{linked['tag']}"
                    )
                except RiotError as error:
                    riot_problem, riot_detail = riot_failure(error.kind), error.detail
            elif do == "riot_off":
                riot.sign_out()
                saved = _t("Riot アカウントとの連携を解除しました。")
            elif do == "look" and current_app.config["VAL_UPDATER"] is not None:
                # the PC app: ask the site now whether a newer build is out (update.py)
                try:
                    found = current_app.config["VAL_UPDATER"].look()
                except UpdateError:
                    riot_problem = _t("新しい版があるか確かめられませんでした。少ししてからもう一度押してください。")
                else:
                    if found and found["ready"]:
                        saved = _t("新しい版があります。上の「いますぐ更新」で更新できます。")
                    elif found:
                        saved = _t("新しい版を作っている途中です。数分後にもう一度確かめてください。")
                    else:
                        saved = _t("いまの版が最新です。")
            elif do in ("app", "alert_test") and current_app.config["VAL_LOCAL"] is not None:
                # the PC app's own choices (pc.py): the match-found alert, the start with Windows
                store.save(**{key: request.form.get(key) == "on" for key in pc.DEFAULTS})
                saved = _t("PC アプリの設定を保存しました。")
                if autostart is not None and autostart.possible:
                    wanted = request.form.get("autostart") == "on"
                    if not autostart.set(wanted):
                        saved, riot_problem = "", _t("Windows の起動に登録できませんでした。")
                if do == "alert_test" and current_app.config["VAL_ALERT"] is not None:
                    current_app.config["VAL_ALERT"]("found", always=True)
                    saved = _t("知らせを試しました（タスクバーのボタンが光り、音がオンなら鳴ります）。")
            else:
                key = request.form.get("henrik_key", "").strip()
                if key:
                    store.save(henrik_key=key[:200])
                    saved = _t("API キーを保存しました。")
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
            riot_detail=riot_detail,
            riot_link=AUTHORIZE,
            pc=pc.chosen(store),
            autostart={
                "possible": autostart is not None and autostart.possible,
                "on": autostart is not None and autostart.on(),
            },
        )

    from valtracker import compare, owner, seasons, skinlib, tools, world

    tools.register(app)
    seasons.register(app)
    world.register(app)
    skinlib.register(app)
    owner.register(app)
    compare.register(app)

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", status=404, message=_t("ページが見つかりません")), 404

    @app.errorhandler(400)
    @app.errorhandler(403)
    def refused(error):
        return render_template("error.html", status=error.code, message=_t("この操作はできませんでした。")), error.code
