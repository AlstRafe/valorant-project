"""ページ: 検索とお気に入り（トップ）、プレイヤー、試合、エージェント、持ち主のコレクションと設定（ランキング、
サーバーステータス、クロスヘア、ストアは tools.py）。"""

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
from valtracker import heatmap, i18n, pc, pdmatch, release, score, security, smurf, stats
from valtracker import rounds as rounds_module
from valtracker.activity import BANDS, LINE_QUEUES, LINE_STATS, RANKS_ENOUGH
from valtracker.content import todays_tier
from valtracker.henrik import FRESH_AFTER, MODES, NAME, REGIONS, HenrikError, shows_rr, slim_match, split_riot_id
from valtracker.media import kind as media_kind
from valtracker.pdmatch import MODE_TYPES
from valtracker.riot import ACCOUNTS_MAX, AUTHORIZE, RiotError
from valtracker.update import UpdateError
from valtracker.util import as_dict, to_int

#: HenrikDev のキュー ID -> ゲームでの呼び名（訳は locale.py）。
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
#: サーバー（HenrikDev の ``cluster``、ゲームの英語名）-> 日本語の都市名（訳す）。ほかは HenrikDev の名前のまま出す。
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
#: プレイヤーの試合の上の絞り込み（ゲームの順。"" は全モード、名前は QUEUES のもの）。
MODE_FILTER = ("", *MODES)
#: プレイヤーの通算の一覧: 1 ページの試合数、RR とスコアのグラフの最大点数（長い線は点を減らすか平均にして全体を描く）、
#: 各キルマップの点数（新しいものから）。
LIFE_PAGE = 20
RR_DOTS = 60
SCORE_POINTS = 40
HEAT_DOTS = 800
#: 計算したプレイヤーの通算を、その人のものが何も増えなければ持つ秒数。
LIFE_KEEP = 900

#: プレイヤーや試合を出せなかった理由 -> （HTTP ステータス, 訪問者への文, 持ち主への文）。
FAILURES = {
    "nokey": (
        503,
        "戦績の検索はまだ準備中です。",
        "HenrikDev の API キーが未設定です。設定から登録してください。",
    ),
    "badkey": (
        503,
        "戦績を取得できませんでした。しばらくしてからお試しください。",
        "HenrikDev の API キーが無効です。設定を確認してください。",
    ),
    "notfound": (404, "プレイヤーが見つかりません。名前とタグを確認してください。", None),
    "busy": (503, "混雑しています。少し待ってからお試しください。", None),
    "down": (
        503,
        "戦績データの取得元（HenrikDev）が応答しません。このサイトは動いています。しばらくしてからお試しください。",
        None,
    ),
}

#: 持ち主の Riot アカウントを読めなかった理由（riot_failure: ページの言語で）。
RIOT_FAILURES = {
    "nologin": "Riot アカウントが未連携です。設定から連携してください。",
    # PC アプリ: アカウントは Riot Client のもの
    "noclient": "Riot クライアントが起動していないか、サインインしていません。",
    "badpaste": "ssid ではないようです。手順 3 の値をそのまま貼ってください。",
    "signedout": "Riot のログインが切れました。設定で ssid を貼り直してください。",
    "busy": "Riot のサーバーが混雑しています。少し待ってからお試しください。",
    "down": "Riot のサーバーが応答しません。しばらくしてからお試しください。",
    "full": "連携できるアカウントは {count} 個までです。",
}


def tr(ja: str, **values) -> str:
    return i18n.text(getattr(g, "lang", i18n.DEFAULT), ja, **values)


def riot_failure(kind: str) -> str:
    """持ち主の Riot アカウントを読めなかった理由（RiotError の kind）をページの言語で。"""
    if kind == "nologin" and current_app.config.get("VAL_LOCAL") is not None:
        kind = "noclient"
    return tr(RIOT_FAILURES.get(kind, RIOT_FAILURES["down"]), count=ACCOUNTS_MAX)


def failure_info(kind: str) -> tuple[int, str, bool]:
    """（ステータス, 訪問者の言語の文, 設定へのリンクが役立つか）。"""
    status, visitor, owner = FAILURES.get(kind, FAILURES["down"])
    if owner and security.is_owner():
        return status, tr(owner), kind in ("nokey", "badkey")
    return status, tr(visitor), False


def cluster(name) -> str:
    """訪問者の言語でのサーバー名。"""
    name = str(name or "")
    return tr(CLUSTERS[name]) if name in CLUSTERS else name


#: Riot ID の代わりに検索に貼られたプロフィールのアドレス: tracker.gg のものと、このサイトのもの。
PROFILE_LINKS = (
    re.compile(r"tracker\.gg/valorant/profile/riot/([^/?#\s]+)", re.IGNORECASE),
    re.compile(r"/p/([^/?#\s]+)/([^/?#\s]+)"),
)


def from_link(text: str) -> str:
    """貼られたプロフィールのアドレスから ``Name#TAG`` を取り出す。ほかはそのまま。"""
    if "/" not in text:
        return text
    for pattern in PROFILE_LINKS:
        found = pattern.search(text)
        if found:
            return "#".join(unquote(part) for part in found.groups())
    return text


def act_left() -> str | None:
    """今のアクトの残り時間の文（content.Content.act）。"""
    found = current_app.config["VAL_CONTENT"].act(g.lang)
    if not found:
        return None
    seconds = found["ends"].timestamp() - time.time()
    if seconds <= 0:
        return None
    if seconds >= 2 * 86400:
        text = tr("{act} 終了まであと {days} 日", act=found["name"], days=int(seconds // 86400))
    else:
        text = tr("{act} 終了まであと {hours} 時間", act=found["name"], hours=max(1, math.ceil(seconds / 3600)))
    return text


def queue_name(key: str) -> str:
    """キュー ID の訪問者の言語での名前（知らないものは ID のまま）。"""
    key = key.lower()
    return tr(QUEUES[key]) if key in QUEUES else key


def _queue(metadata: dict) -> str:
    queue = metadata.get("queue") or {}
    key = str(queue.get("id") or "").lower()
    return tr(QUEUES[key]) if key in QUEUES else str(queue.get("name") or key or "")


def rank_info(tier: dict | None, old: bool = False) -> dict:
    """HenrikDev の ``{"id": 21, "name": "Ascendant 1"}`` を訪問者の言語で、絵つきで。"""
    tier = tier if isinstance(tier, dict) else {"id": tier}  # 番号だけの tier も読む
    number = to_int(tier.get("id"))
    known = current_app.config["VAL_CONTENT"].tiers(g.lang, old=old).get(number) or {}
    name = str(known.get("tierName") or tier.get("name") or "")
    return {"tier": number, "name": name.title() if name.isupper() else name, "icon": known.get("smallIcon")}


def peak_info(mmr: dict | None) -> dict | None:
    """プレイヤーの全アクトでの最高ランク: v3 mmr の ``peak``、またはアクトの良い勝利と最終ランクの中のもっと高いもの。
    今の番号で: エピソード 5 より前のアクトにはアセンダントが無かった（その頃のイモータル 1 は 21、今の 24）。"""
    peak = (mmr or {}).get("peak") or {}
    tier = as_dict(peak.get("tier"))
    number = to_int(tier.get("id"))
    best = todays_tier(number, bool(peak.get("old")))
    for act in (mmr or {}).get("seasons") or []:
        best = max(
            best, todays_tier(max([act.get("end_tier") or 0, *(act.get("best") or [])[:1]]), bool(act.get("old")))
        )
    if best <= 2:
        return None
    return rank_info(tier if best == number else {"id": best})


def _own_rr(region: str, puuid: str) -> dict:
    """ページが持ち主自身のアカウントで、持ち主が見ているときの、ゲーム自身のサーバー（PD）
    からの持ち主の最近のコンペティティブの RR: HenrikDev のランクは試合直後に 1 試合遅れる。ほかの人や、
    Riot が答えないときは {}。"""
    if not puuid or not security.is_owner():
        return {}
    riot = current_app.config["VAL_RIOT"]
    try:
        if puuid not in {riot.me(), riot.me(game=True)}:
            return {}
        return riot.rr_updates(region, puuid)["changes"]
    except RiotError:
        return {}


def _current(current: dict, rr_changes: dict, sure: bool) -> dict:
    """今のランクと RR: v3 mmr の ``current``。``current`` が 1 試合遅れている（まだ前の試合後の値。試合直後に起きる）
    ときは RR 履歴の最新の試合後の値。 ``sure``: 履歴にゲーム自身の記録がある（持ち主のページ）ので、
    その最新の試合が正しい。"""
    ordered = sorted(
        (item for item in rr_changes.values() if item.get("at") and item.get("tier", 0) > 2),
        key=lambda item: -item["at"],
    )
    if not ordered:
        return current
    now = (_tier_number(current.get("tier")), current.get("rr"))
    latest = (ordered[0]["tier"], ordered[0]["rr"])
    before = (ordered[1]["tier"], ordered[1]["rr"]) if len(ordered) > 1 else None
    if now == latest or not (sure or (now[0] > 2 and now == before)):
        return current
    return current | {"tier": {"id": latest[0]}, "rr": latest[1], "last_change": ordered[0]["change"]}


def _act_peak(mmr: dict | None, rank: dict | None) -> dict | None:
    """プレイヤーのこのアクトの最高ランク: 最新アクトの良い勝利と最終ランク、今のランク。
    今ランクがある人だけ: 無ければ最後にプレイしたアクトは前のアクト。"""
    seasons = (mmr or {}).get("seasons") or []
    if not rank or rank["tier"] <= 2 or not seasons:
        return None
    act = seasons[0]
    best = todays_tier(max([act.get("end_tier") or 0, *(act.get("best") or [])[:1]]), bool(act.get("old")))
    return rank if best <= rank["tier"] else rank_info({"id": best})


def agent_info(agent: dict | None) -> dict:
    agent = agent or {}
    known = current_app.config["VAL_CONTENT"].agent_index(g.lang).get(str(agent.get("id") or "").lower()) or {}
    role = as_dict(known.get("role"))
    return {
        "id": str(agent.get("id") or ""),
        "name": str(known.get("displayName") or agent.get("name") or ""),
        "icon": known.get("displayIcon"),
        "role": {"name": str(role.get("displayName") or ""), "icon": role.get("displayIcon")},
    }


def _map_item(metadata: dict) -> dict:
    """試合をしたマップ（valorant-api.com のもの。不明なら {}）。"""
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


def match_row(match: dict, player: dict, rr_changes: dict) -> dict:
    """プレイヤーの一覧の試合 1 つ。"""
    metadata = match.get("metadata") or {}
    match_id = str(metadata.get("match_id") or "")
    rounds = stats.rounds_played(match)
    agent = agent_info(player.get("agent"))
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
        "length": to_int(metadata.get("game_length_in_ms")),
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "agent_icon": agent["icon"],
        "role": agent["role"],
        "result": result,
        "placing": stats.placing(match, player),
        "rr": (rr_changes.get(match_id) or {}).get("change"),
        "then": _then(player, rr_changes.get(match_id)),
        "line": stats.line(player, rounds, adv),
        "adv": adv,
        "extra": rounds_module.extras(match, place) if result["rounds"] else {},
        "rounds": rounds,
        "side": side if result["rounds"] else None,
        "weapons": adv.get("weapons") or {},
        "weapon_names": (match.get("digest") or {}).get("weapons") or {},
        "lobby": stats.lobby(match, player, rounds) if result["rounds"] else None,
        "mates": stats.mates(match, player),
        # デスマッチ、スカーミッシュなどは平均をゆがめる: まとめは通常の試合で数える。
        "standard": str((metadata.get("queue") or {}).get("mode_type") or "Standard") == "Standard",
        "queue_id": str((metadata.get("queue") or {}).get("id") or "").lower(),
        "tier": _tier_number(player.get("tier")),
    }


def _then(player: dict, moved: dict | None) -> dict | None:
    """一覧の行用の、試合をしたときのプレイヤーのランク: その試合後のランクと RR（RR 履歴）、
    無ければ試合をしたときのランク（RR 無し）。ランクが無ければ None。"""
    tier, rr = _tier_number((moved or {}).get("tier")), (moved or {}).get("rr")
    if tier <= 2:
        tier, rr = _tier_number(player.get("tier")), None
    return {"rank": rank_info({"id": tier}), "rr": rr} if tier > 2 else None


def _tier_number(tier) -> int:
    return to_int(tier.get("id") if isinstance(tier, dict) else tier)


def band_name(name: str) -> str:
    """訪問者の言葉でのランク帯（activity.BANDS）: 「プラチナ – ダイヤモンド」。"" なら全帯。"""
    for key, low, high in BANDS:
        if key == name:
            first, last = (rank_family(rank_info({"id": tier})["name"]) for tier in (low, high))
            return first if first == last else f"{first} – {last}"
    return tr("すべてのランク")


def rank_family(name: str) -> str:
    """番号を除いたランク名: "Gold 2" -> "Gold"。"""
    return re.sub(r"\s*\d+$", "", name) or name


def share_text(value: float) -> str:
    """ページ用のパーセント: 1% 未満は小数 1 桁（0.4）、ほかは整数（12）。無ければ 0。"""
    return "0" if value <= 0 else f"{value:.1f}" if value < 1 else str(round(value))


def rank_top(region: str, tier: int) -> str | None:
    """リージョンでのランクの「上位 X%」: そこで見たコンペティティブの成績のうち、
    そのランク以上の割合（activity.Activity.ranks）。RANKS_ENOUGH 件未満なら None。"""
    found = current_app.config["VAL_ACTIVITY"].ranks(region)
    if tier <= 2 or found["lines"] < RANKS_ENOUGH:
        return None
    above = sum(count for number, count in found["tiers"].items() if number >= tier)
    return share_text(100 * above / found["lines"])


def match_score(line: dict, rounds: int, queue: str, tier: int, won) -> dict | None:
    """プレイヤーのランク帯の成績と比べた試合のスコア（score.py）
    : ``{"value", "grade", "standings": {stat: 0..1}, "lines", "band"}``。別のモード、リメイク、
    見た成績がまだ少なすぎるなら None。"""
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
    """各行のスコアと、全体の平均・その評価・各数値の「上位 X%」・推移（古い順）
    : ``{"value", "grade", "games", "lines", "band", "tops": {stat: X}, "chart"}``。無ければ None。"""
    for row in rows:
        # 保存した成績（archive.py）には KAST が無い: ほかと同じようには採点できない
        row["score"] = (
            None
            if row.get("thin")
            else match_score(row["line"], row["rounds"], row["queue_id"], row["tier"], row["result"]["won"])
        )
    scored = [row for row in rows if row["score"]]
    if not scored:
        return None
    value = round(sum(row["score"]["value"] for row in scored) / len(scored))
    widest = max(scored, key=lambda row: row["score"]["lines"])["score"]
    trend = [row["score"]["value"] for row in reversed(scored)]  # 古い順
    return {
        "value": value,
        "grade": score.grade(value),
        "games": len(scored),
        "lines": widest["lines"],
        "band": band_name(widest["band"]),
        "tops": {key: score.top([row["score"]["standings"][key] for row in scored]) for key in LINE_STATS},
        "chart": _score_chart(_averages(trend, SCORE_POINTS))
        | {
            "narrow": _score_chart(_averages(trend, NARROW_DOTS), NARROW),
            "first": i18n.day(g.lang, scored[-1]["started"]) if scored[-1].get("started") else "",
            "last": i18n.day(g.lang, scored[0]["started"]) if scored[0].get("started") else "",
        }
        if len(scored) > 1
        else None,
    }


def _averages(values: list[int], most: int) -> list[int]:
    """最大 ``most`` 個の値: それより長い一覧は、ほぼ同じ長さの ``most`` 区間に分けてそれぞれの平均にする。"""
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
    """試合のスコアの線（古い順、ここで描く SVG）。各評価の始まりに線を引く。RR のものより細い（最大 SCORE_POINTS 点。
    通算は平均で）ので、スマホでも十分な高さになる。``size``: 絵の幅と高さ。"""
    width, height = size
    pad = 12

    def y(value: float) -> float:
        return round(pad + (1000 - value) * (height - 2 * pad) / 1000, 1)

    left = 26  # 評価の文字の場所
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
    """訪問者の言語での武器（またはアビリティなど倒したもの）と、そのキルフィードの絵。"""
    known = current_app.config["VAL_CONTENT"].arsenal(g.lang).get(str(key).lower()) or {}
    return {
        "name": str(known.get("displayName") or said or key),
        "icon": known.get("killStreamIcon"),
        # 性能のわかる武器（weapons.py）: 武器のページへのリンク
        "page": str(key).lower() if as_dict(known.get("stats")).get("ranges") else "",
    }


#: ロビー比較の行: キー -> 呼び名と、数値の書き方。
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
    """プレイヤーとロビーの比較を文に。一番良いものと一番弱いものに印。"""
    found = []
    for item in compared:
        label, number = LOBBY_LABELS[item["key"]]
        found.append(
            dict(item, label=tr(label), mine_text=number.format(item["mine"]), lobby_text=number.format(item["lobby"]))
        )
    best = max(found, key=lambda item: item["score"], default=None)
    worst = min(found, key=lambda item: item["score"], default=None)
    for item in found:
        item["strength"] = item is best and item["better"]
        item["weakness"] = item is worst and item["worse"]
    return found


def _rr_chart(rr_changes: dict) -> dict | None:
    """コンペティティブの試合の RR の線（ここで描く SVG、スクリプト無し）、古い順。
    各ランクの始まりに線（elo: 1 ランク 100、ランク 3 が 0）: 横長の絵と、``narrow`` にスマホ用のもの（幅の割に高く、
    文字が読める）。下に最初と最後の日付。"""
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


#: スマホでのグラフの大きさ（横長の 800 × 150 だとそこでは高さ 65 px、文字 5 px になる）と、最大の点数。
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
            "rank": rank_info({"id": item["tier"]}),
            "rr": item["rr"],
        }
        for number, item in enumerate(points)
    ]
    # 通算の線: 全試合を通して描き、一部に点を打つ（最初と最新にも）
    gap = max(1, math.ceil(len(every) / most))
    dots = [dot for number, dot in enumerate(every) if number % gap == 0 or number == len(every) - 1]
    lines = [
        {"y": y(elo), "rank": rank_info({"id": elo // 100 + 3})}
        for elo in range(int(low // 100 + 1) * 100, int(high) + 1, 100)
    ]
    return {
        "width": width,
        "height": height,
        "path": "M" + " L".join(f"{dot['x']},{dot['y']}" for dot in every),
        "dots": dots,
        "lines": lines,
    }


def _next_rank(rank: dict | None, rr, rr_changes: dict) -> dict | None:
    """次のランクまでの距離: 必要な RR と、プレイヤーの平均の上がり幅で約何勝か。"""
    if not rank or not 3 <= rank["tier"] < 24 or rr is None:
        return None
    changes = [item.get("change", 0) for item in rr_changes.values()]
    gains = [change for change in changes if change > 0]
    losses = [-change for change in changes if change < 0]
    need = max(1, 100 - to_int(rr))
    gain = round(sum(gains) / len(gains)) if gains else 0
    # プレイヤー自身の勝率と平均の上がり・下がり幅での、1 試合あたりの平均 RR
    per_match = (sum(gains) - sum(losses)) / len(changes) if changes else 0
    return {
        "need": need,
        "rank": rank_info({"id": rank["tier"] + 1}),
        "gain": gain,
        "loss": round(sum(losses) / len(losses)) if losses else 0,
        "wins": -(-need // gain) if gain else None,
        "matches": math.ceil(need / per_match) if per_match > 0 and len(changes) >= 5 else None,
        "flat": len(changes) >= 5 and per_match <= 0,
    }


def _streak(rows: list[dict]) -> dict | None:
    """最新の試合までの連勝・連敗（新しい順。引き分けで切れる）: 2 以上、無ければ None。"""
    results = [row["result"]["won"] for row in rows if row["standard"]]
    if not results or results[0] is None:
        return None
    count = next((index for index, won in enumerate(results) if won is not results[0]), len(results))
    return {"won": results[0], "count": count} if count >= 2 else None


def _weapon_said(rows: list[dict], key: str) -> str:
    """valorant-api.com が知らない武器用の、HenrikDev 自身の武器名（英語）。"""
    for row in rows:
        said = (row.get("weapon_names") or {}).get(key)
        if said:
            return said
    return ""


def _mode() -> str:
    """訪問者がプレイヤーの試合を絞ったモード（``?mode=``）。全部なら ""。"""
    mode = request.args.get("mode", "")
    return mode if mode in MODES else ""


def _modes(mode: str) -> list[dict]:
    return [
        {"key": key, "name": tr(QUEUES[MODES[key]]) if key else tr("すべて"), "current": key == mode}
        for key in MODE_FILTER
    ]


def _standing(current: dict) -> dict:
    """ランクカードのその他の情報: 残りの配置試合とランキングの順位。"""
    board = current.get("leaderboard_placement")
    return {
        "placements": to_int(current.get("games_needed_for_rating")),
        "leaderboard": to_int(board.get("rank")) if isinstance(board, dict) else 0,
    }


#: アクトランクのバッジ: 三角 1・3・5 個の行で、上向きと下向きが交互。良い勝利で上から、左から右へ埋める。
BADGE_ROWS = ((True,), (True, False, True), (True, False, True, False, True))


def _acts(seasons: list) -> list[dict]:
    """プレイしたアクト（新しい順）: 名前、戦績、最終ランク、バッジ。"""
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
                "end": rank_info({"id": season.get("end_tier")}, old=season.get("old", False)),
                "board": season.get("board", 0),
                "rows": rows,
                "shown": len(season.get("best") or []),
            }
        )
    return acts


def player_page(
    name: str, tag: str, mode: str = "", fresh: bool = False, span: str = "", page: int = 1, act: str = ""
) -> tuple[dict, int]:
    """プレイヤーページに出すものと HTTP ステータス。まずアカウント（リージョンが必要）、次にランクと試合を同時に頼む。
    ``fresh``: 訪問者が「データを更新」を押したので、持っているランクと試合を頼み直す（読んでから 1 分以上経っていれば）
    。 ``span`` "all": 最新 RECENT ではなく、持っている全試合（archive.py）。
    "act": 1 つのアクトの試合（``act`` はその uuid。無ければ今のアクト）。tracker.gg のシーズンの数え方と同じ。
    ``page``: その一覧のページ。"""
    henrik = current_app.config["VAL_HENRIK"]
    try:
        account = henrik.account(name, tag)
    except HenrikError as error:
        status, problem, settings_link = failure_info(error.kind)
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
        mmr = None  # ランクなし、またはランクが読めなかった: 試合はそれでも出す価値がある
    try:
        rr_changes = asked_rr.result() if asked_rr else {}
    except HenrikError:
        rr_changes = {}  # 一覧に各試合の RR が付かないだけ
    own = _own_rr(region, str(account.get("puuid") or "").lower())
    rr_changes = rr_changes | own  # 持ち主の試合のゲーム自身の記録: HenrikDev より先にある
    rows, matches_problem, spots = [], "", {}
    try:
        listed = asked_matches.result()
        current_app.config["VAL_ACTIVITY"].seen(listed, str(account.get("puuid") or ""))
        current_app.config["VAL_ARCHIVE"].keep(listed, "henrik")
        for match in listed:
            found = stats.find_player(match, str(account.get("puuid") or ""), name, tag)
            if found:
                rows.append(match_row(match, found, rr_changes))
                _add_spots(spots, match, found)
    except HenrikError as error:
        matches_problem = failure_info(error.kind)[1]
    life, heat_maps, rr_all = None, None, rr_changes
    puuid = str(account.get("puuid") or "").lower()
    window = _act_window(act) if span == "act" else None
    span = "act" if window else "all" if span else ""
    if span and puuid:
        life = _lifetime(puuid, region, name, tag, mode, rr_changes, window)
        rows, heat_maps, rr_all = life.pop("rows"), life.pop("heat_maps"), life.pop("rr")
        if rows:
            matches_problem = ""  # HenrikDev は今答えなかったが、プレイヤーの持っている試合はある
    pages = max(1, math.ceil(len(rows) / LIFE_PAGE)) if life else 1
    page = min(max(1, page), pages)
    shown = rows[(page - 1) * LIFE_PAGE : page * LIFE_PAGE] if life else rows
    current = _current((mmr or {}).get("current") or {}, rr_changes, bool(own))
    rank = rank_info(current.get("tier")) if current else None
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
    guessed = smurf.guess(
        rank["tier"] if rank else 0,
        _tier_number(account.get("account_level")) or None,
        len((mmr or {}).get("seasons") or []) if mmr else None,
        summary.get("games", 0) if summary else 0,
        summary.get("win_rate") if summary else None,
        scores["value"] if scores else None,
        scores["games"] if scores else 0,
    )
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
        "peak": peak_info(mmr),
        "act_peak": _act_peak(mmr, rank),
        "smurf": guessed,
        "standing": _standing(current),
        "acts": _acts((mmr or {}).get("seasons") or []),
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
    """プレイヤーの数値を数えるアクト（span "act"）: 頼まれたもの、無ければ今のもの、
    それも無ければ最新: ``{"uuid", "name", "starts", "ends", "current", "from": 訪問者の言葉での最初の日}``。
    アクトがわからなければ None（valorant-api.com が答えなかった）。"""
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
    """プレイヤーページで選べるアクト: 持っている最初の試合のアクトから今のアクトまで（と、表示中のもの）、新しい順。"""
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
    """プレイヤーページが数える形の、持っているプレイヤーの全試合（archive.py）
    : ``{"rows", "heat_maps", "rr", "count", "whole", "thin", "since", "first", "reading"}``。``window``（_act_window）
    があればそのアクトの試合だけ。残りの履歴を裏に読むよう頼む。行と絵の計算結果は、
    その人のものが何か増えるまで（か LIFE_KEEP まで）持つ。"""
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
                row = match_row(match, found, rr_all)
                # Riot の記録はプレイヤー以外の名前を出さない（pdmatch.py）: パーティーは省く
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
    """保存した成績（henrik._slim_stored: プレイヤーの数値、ラウンド無し）を一覧の試合 1 つとして。"""
    match_id = str(line.get("id") or "")
    try:
        moment = datetime.fromisoformat(str(line.get("started") or "")).astimezone(stats.JST)
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
    agent = agent_info({"id": line.get("agent")})
    return {
        "id": match_id,
        "region": region,
        "map": _map({"map": {"id": line.get("map_id") or "", "name": line.get("map") or ""}}),
        "queue": queue_name(queue_id),
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
        "then": _then({"tier": line.get("tier")}, rr_changes.get(match_id)),
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
    """訪問者がプレイヤーの通算を頼んだら（``?span=all``）"all"、1 つのアクトなら（``?span=act``、
    ``&act=`` にその uuid）"act"、それ以外は ""（最新の試合）。"""
    asked = request.args.get("span")
    return asked if asked in ("all", "act") else ""


def _act() -> str:
    """頼まれたアクト（``?act=``、uuid）、または ""。"""
    asked = str(request.args.get("act") or "").lower()
    return asked if re.fullmatch(r"[0-9a-f-]{36}", asked) else ""


def _page() -> int:
    try:
        return max(1, int(request.args.get("page", "1")))
    except ValueError:
        return 1


def _read(at: float) -> dict | None:
    """ページの数値を HenrikDev から読んだ時刻と、「データを更新」でもう頼み直せるか。"""
    if not at:
        return None
    age = max(0, time.time() - at)
    return {"minutes": int(age // 60), "again": age >= FRESH_AFTER}


def _add_spots(spots: dict, match: dict, player: dict) -> None:
    """試合でのプレイヤーのキルとデス（heatmap.points）をマップのものに足す。"""
    everyone = [item for item in match.get("players") or [] if isinstance(item, dict)]
    mine = next((number for number, item in enumerate(everyone) if item is player), -1)
    found = heatmap.points(match, mine)
    if found:
        known = _map_item(match.get("metadata") or {})
        if known.get("uuid"):
            spots.setdefault(str(known["uuid"]).lower(), (known, []))[1].extend(found)


def _heat_maps(spots: dict, most: int | None = None) -> list[dict]:
    """マップごとのプレイヤーのキルとデスの絵（heatmap.drawing）、多いマップから。
    ``most``: 各マップに描く点の最大数（新しいものから）。"""
    drawn = []
    for known, found in spots.values():
        plan = heatmap.plan(known)
        drawing = heatmap.drawing(plan, found, most=most) if plan else None
        if drawing:
            drawn.append(dict(drawing, name=str(known.get("displayName") or ""), id=str(known["uuid"]).lower()))
    drawn.sort(key=lambda item: -len(item["dots"]))
    return drawn[: heatmap.MAPS_SHOWN]


#: ラウンドの終わり方 -> 絵のタイトルの文。
ENDINGS = {
    "elim": "全員倒した",
    "defuse": "スパイク解除",
    "detonate": "スパイク爆発",
    "time": "時間切れ",
    "surrender": "降参",
}


#: チームの購入（_buy）-> 名前。
BUYS = {"eco": "エコ", "semi-eco": "セミエコ", "semi-buy": "セミバイ", "full": "フルバイ"}


def _clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _buy(loadout: int, players: int) -> str:
    """ロードアウトからのチームの購入: エコ、セミエコ、セミバイ、フルバイ（1 人あたりの平均で）。"""
    each = loadout / players if players else 0
    return "eco" if each < 1000 else "semi-eco" if each < 2000 else "semi-buy" if each < 3900 else "full"


#: 経済の行の購入の種類（ピストルラウンドは別に数える）。
ECONOMY = ("pistol", "eco", "semi-eco", "semi-buy", "full")


def _economy(rounds: list, team_sizes: dict, my_team: str, half: int) -> dict | None:
    """ラウンドごとの各チームの購入（ロードアウトの 1 人あたり）と、購入ごとのラウンド勝率
    : ``{"teams": [{"id", "mine", "kinds": [{"kind", "won", "played"}]}], "rounds": [{"number", "won": チーム ID,
    "spent": {チーム: クレジット}, "buys": {チーム: 種類}}], "top"}``。ロードアウトの無い試合は None。"""
    teams = sorted(
        {team for item in rounds for team in as_dict(item.get("l"))}, key=lambda team: (team != my_team, team)
    )
    if len(teams) != 2:
        return None
    shown, counted = [], {team: {kind: [0, 0] for kind in ECONOMY} for team in teams}
    for number, item in enumerate(rounds):
        spent = as_dict(item.get("l"))
        each = {
            team: round(to_int(spent.get(team)) / team_sizes[team]) if team_sizes.get(team) else 0 for team in teams
        }
        pistol = number == 0 or (bool(half) and number == half)
        buys = {team: "pistol" if pistol else _buy(each[team], 1) for team in teams}
        for team in teams:
            counted[team][buys[team]][1] += 1
            counted[team][buys[team]][0] += item["w"] == team
        shown.append(
            {
                "number": number + 1,
                "won": item["w"],
                "spent": each,
                "buys": buys,
                "turn": bool(half) and number in (half, 2 * half),
            }
        )
    if not any(any(item["spent"].values()) for item in shown):
        return None
    return {
        "teams": [
            {
                "id": team,
                "mine": team == my_team,
                "kinds": [
                    {"kind": kind, "won": won, "played": played}
                    for kind, (won, played) in counted[team].items()
                    if played
                ],
            }
            for team in teams
        ],
        "rounds": shown,
        "top": max(max(item["spent"].values()) for item in shown) or 1,
    }


def _own_match(region: str, match_id: str) -> tuple[dict, dict] | None:
    """HenrikDev が（まだ）持っていない（試合後数分かかり、届かない試合もある）か答えないときの、
    持ち主用のゲーム自身のサーバー（PD）からの試合: （保存するもの, ページに出すもの）。 PD は全プレイヤーの名前を出すので、
    保存するものには持ち主自身の名前だけ残す（pdmatch）。ページには持ち主と同じパーティーだった人の名前も出す
    （ゲームもパーティーの仲間には名前を見せる）。保存した試合は誰でも見られるページ（通算）に使うので、そちらには入れない。
    訪問者、連携アカウントが無いとき、Riot が持っていないときは None。"""
    if not security.is_owner():
        return None
    riot = current_app.config["VAL_RIOT"]
    try:
        status, puuid = riot.status(game=True), riot.me(game=True)
        if not puuid:
            return None
        details = riot.match_details(region, match_id)
    except RiotError:
        return None
    maps = current_app.config["VAL_CONTENT"].map_index("en-GB")
    raw = pdmatch.to_v4(details, region, maps, puuid, status["name"], status["tag"])
    if not raw or not raw["metadata"].get("is_completed", True):
        return None
    mates = pdmatch.party_of(details, puuid)
    shown = pdmatch.to_v4(details, region, maps, puuid, status["name"], status["tag"], mates) if mates else raw
    return slim_match(raw), slim_match(shown)


def _match_page(found: dict, me: str) -> dict:
    """試合ページに出すもの: スコアボード（ラウンドの数値つき）、ラウンドごととそのキル、パーティー、
    訪問者が来たプレイヤーなら相手ごとの撃ち合い。"""
    metadata = found.get("metadata") or {}
    rounds = stats.rounds_played(found)
    digest = found.get("digest") or {}
    everyone = [player for player in found.get("players") or [] if isinstance(player, dict)]
    me = me.lower()
    mine = next(
        (n for n, player in enumerate(everyone) if f"{player.get('name')}#{player.get('tag')}".lower() == me), -1
    )
    # 2 人以上のパーティー。出てきた順に番号を付ける
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
            "agent": agent_info(player.get("agent")),
            "rank": rank_info(player.get("tier")),
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
        person["score"] = match_score(person["line"], rounds, queue_id, person["tier"], person["won"])

    def lines(team_id: str | None) -> list[dict]:
        listed = [person for person in people if team_id is None or person["team"] == team_id]
        # コンバットスコア順。ラウンドが無い（デスマッチなど）とスコアも無いのでキル順
        listed.sort(key=lambda person: -(person["line"]["acs"] if rounds else person["line"]["kills"]))
        return listed

    teams = []
    for team in found.get("teams") or []:
        team_id = str(team.get("team_id") or "")
        teams.append({"id": team_id, "result": stats.team_result(found, team_id), "players": lines(team_id)})
    if not any(team["players"] for team in teams):  # デスマッチ: 全員が個人戦、1 つの表に
        teams = [{"id": "", "result": None, "players": lines(None)}]
    my_team = people[mine]["team"] if mine >= 0 else ""
    teams.sort(key=lambda team: team["id"] != my_team)  # 訪問者が来たプレイヤーのチームを先に
    team_sizes = {team["id"]: len(team["players"]) for team in teams}
    half = rounds_module.HALF.get(queue_id, 0)
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
                "end_words": tr(ENDINGS[item["e"]]) if item["e"] in ENDINGS else "",
                "ceremony": item["c"],
                "site": item["s"],
                "buys": {team: _buy(value, team_sizes.get(team, 0)) for team, value in (item.get("l") or {}).items()},
                # 後半とオーバータイムの前に間を空ける
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
    average = rank_info({"id": round(sum(tiers) / len(tiers))}) if tiers else None
    # 各ラウンド後のプレイヤーの数値（ゲームはプレイ中にこれを出さない）
    course = []
    if mine >= 0 and digest.get("kills") and digest.get("rounds"):
        kills = deaths = head = hits = 0
        counted = any(item.get("h") for item in digest["rounds"])
        for number, item in enumerate(digest["rounds"]):
            happened = by_round.get(number, [])
            took = sum(1 for kill in happened if kill[2] == mine and people[kill[3]]["team"] != my_team)
            died = any(kill[3] == mine for kill in happened)
            kills, deaths = kills + took, deaths + died
            shot = next((line for line in item.get("h") or [] if line[0] == mine), None)
            if shot:
                head, hits = head + shot[1], hits + shot[2]
            course.append(
                {
                    "number": number + 1,
                    "won": item["w"] == my_team if my_team else None,
                    "took": took,
                    "died": died,
                    "kills": kills,
                    "deaths": deaths,
                    "kd": round(kills / max(deaths, 1), 2),
                    "hs": round(100 * head / hits) if counted and hits else None,
                    "turn": bool(half) and number in (half, 2 * half),
                }
            )
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
        "minutes": to_int(metadata.get("game_length_in_ms")) // 60000,
        "rounds": rounds,
        "teams": teams,
        "timeline": timeline,
        "course": course,
        "course_hs": any(row["hs"] is not None for row in course),
        "my_team": my_team,
        "detailed": bool(digest.get("players")),
        "duels": duels,
        "average": average,
        "me": people[mine] if mine >= 0 else None,
        "parties": bool(parties),
        "buy_words": {key: tr(words) for key, words in BUYS.items()} | {"pistol": tr("ピストル")},
        "economy": _economy(digest.get("rounds") or [], team_sizes, my_team, half),
    }


def _me() -> dict | None:
    """トップページの自分の枠用の、持ち主の連携 Riot アカウント（riot.Riot.status: このサーバー自身のファイルから読み、
    Riot は呼ばない）。訪問者には None。"""
    if not security.is_owner():
        return None
    return current_app.config["VAL_RIOT"].status()


def _no_key() -> bool:
    """持ち主がまだ HenrikDev のキーを持っていない（PC アプリの初回起動）: トップページで入れる場所を案内する。"""
    return security.is_owner() and not current_app.config["VAL_SETTINGS"].henrik_key()


def register(app: Flask) -> None:
    @app.get("/")
    def top():
        return render_template("home.html", query="", problem="", me=_me(), no_key=_no_key())

    @app.get("/search")
    def search():
        query = from_link(request.args.get("q", "").strip())[:40]
        found = split_riot_id(query)
        if not found:
            # タグの無い名前: 足りないものを、その名前を例にして言う。"Name JP1"（# の場所に空白、たぶん。
            # 名前に空白が入ることもあるので Riot ID は候補として出すだけ）
            name_only = query if query and "#" not in query.replace("＃", "#") and NAME.fullmatch(query) else ""
            head, _, last = name_only.rpartition(" ")
            guess = split_riot_id(f"{head}#{last}") if head.strip() else None
            if name_only:
                example = "#".join(guess) if guess else f"{name_only}#JP1"
                problem = tr("タグも入力してください。例: {example}", example=example)
            else:
                problem = tr("「名前#タグ」の形式で入力してください（例: Name#JP1）。") if query else ""
            values = {"query": query, "problem": problem, "name_only": name_only, "guess": guess, "me": _me()}
            values["no_key"] = _no_key()
            return render_template("home.html", **values), 400 if query else 200
        return redirect(url_for("player", name=found[0], tag=found[1]), 303)

    @app.get("/p/<name>/<tag>")
    def player(name: str, tag: str):
        """すぐ出す: HenrikDev がもう答えたもの、またはランクと試合の無いページを出し、
        ページのスクリプトがあとで頼む（player_part）。HenrikDev は数秒かかり、その間ブラウザーは何も出していなかった。
        ``?full=1`` なら待つ（JavaScript の無いブラウザー用のリンク）。"""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        henrik, mode, fresh = current_app.config["VAL_HENRIK"], _mode(), bool(request.args.get("fresh"))
        kept = henrik.has_player(name, tag, mode) and not fresh
        if request.args.get("full") or not henrik.key() or kept:  # すぐ答える
            values, status = player_page(name, tag, mode, fresh, _span(), _page(), _act())
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
        )

    @app.get("/p/<name>/<tag>/part")
    def player_part(name: str, tag: str):
        """プレイヤーのランクと試合。スクリプトが差し込むページの部分として。"""
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        start = time.monotonic()
        values, status = player_page(name, tag, _mode(), bool(request.args.get("fresh")), _span(), _page(), _act())
        response = make_response(render_template("_player.html", **values), status)
        response.headers["Cache-Control"] = "no-store"
        # HenrikDev にかかった時間（ブラウザーの開発者ツールの Network -> Timing 用）
        response.headers["Server-Timing"] = f"henrik;dur={(time.monotonic() - start) * 1000:.0f}"
        return response

    @app.get("/m/<region>/<match_id>")
    def match(region: str, match_id: str):
        if region not in REGIONS:
            abort(404)
        own = False
        try:
            found = current_app.config["VAL_HENRIK"].match(region, match_id)
        except HenrikError as error:
            both = _own_match(region, match_id)
            if both is None:
                status, problem, settings_link = failure_info(error.kind)
                if error.kind == "notfound":
                    abort(404)
                return render_template("match.html", problem=problem, settings_link=settings_link), status
            kept, found = both
            own = True
            current_app.config["VAL_ARCHIVE"].keep([kept], "riot")
        else:
            current_app.config["VAL_ACTIVITY"].seen([found])
            current_app.config["VAL_ARCHIVE"].keep([found], "henrik")
        me = request.args.get("me", "")  # 訪問者がその一覧から来たプレイヤー: その行を目立たせる
        response = make_response(render_template("match.html", problem="", **_match_page(found, me)))
        if own:  # 持ち主だけの名前（パーティーの仲間）が入っている: どこにも残さない
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/agents")
    def agents():
        """全エージェント、またはロールのもの（``?role=`` にその uuid）: 一覧の上にロールのリンク。"""
        listed = current_app.config["VAL_CONTENT"].agents(g.lang)
        roles: dict[str, dict] = {}
        for item in listed:
            role = as_dict(item.get("role"))
            if role.get("uuid"):
                roles.setdefault(str(role["uuid"]).lower(), role)
        chosen = request.args.get("role", "").lower()
        chosen = chosen if chosen in roles else ""
        shown = [item for item in listed if not chosen or str(as_dict(item.get("role")).get("uuid")).lower() == chosen]
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
        from valtracker.world import agent_numbers

        return render_template("agent.html", agent=found, abilities=abilities, **agent_numbers(uuid.lower()[:40]))

    @app.get("/app/latest")
    def app_latest():
        """PC アプリの最新ビルド。アプリ自身の「新しいものがあります」用（release.py）。
        サイトだけ: アプリ自身には読むクローンが無い。"""
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
        """ニュース・eスポーツの絵（media.OUTSIDE）。ゲームの絵と同じくここに残す。"""
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
        """持ち主自身のスキン、ガンバディ、カードなど。Riot アカウントから（riot.py）。"""
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
        from valtracker.owner import equip_said, sets_of

        sets = [item for item in current_app.config["VAL_SETS"].load().get("sets") or [] if isinstance(item, dict)]
        sets = sets_of(sets, current_app.config["VAL_RIOT"].me())
        values = owned_items.page(found, current_app.config["VAL_CONTENT"], g.lang, show, weapon, sets)

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
                saved = tr("API キーを削除しました。")
            elif do == "riot":
                from valtracker.owner import claim

                claim(riot.me())  # ここまで残したものは、今まで使っていたアカウントのもの
                try:
                    linked = riot.sign_in(request.form.get("riot", ""))
                    saved = tr(
                        "Riot アカウント {account} と連携しました。", account=f"{linked['name']}#{linked['tag']}"
                    )
                except RiotError as error:
                    riot_problem, riot_detail = riot_failure(error.kind), error.detail
            elif do == "riot_use":
                from valtracker.owner import claim

                claim(riot.me())
                try:
                    shown = riot.switch(request.form.get("account", "")[:36])
                    saved = tr("{account} に切り替えました。", account=f"{shown['name']}#{shown['tag']}")
                except RiotError as error:
                    riot_problem = riot_failure(error.kind)
            elif do == "riot_off":
                riot.sign_out(request.form.get("account", "")[:36])
                saved = tr("Riot アカウントとの連携を解除しました。")
            elif do == "look" and current_app.config["VAL_UPDATER"] is not None:
                # PC アプリ: 新しいビルドが出たか今サイトに聞く（update.py）
                try:
                    found = current_app.config["VAL_UPDATER"].look()
                except UpdateError:
                    riot_problem = tr("アップデートを確認できませんでした。少し待ってからお試しください。")
                else:
                    if found and found["ready"]:
                        saved = tr("アップデートがあります。上の「いますぐ更新」から更新できます。")
                    elif found:
                        saved = tr("新しいバージョンを準備中です。数分後にもう一度確認してください。")
                    else:
                        saved = tr("最新バージョンです。")
            elif do in ("app", "alert_test") and current_app.config["VAL_LOCAL"] is not None:
                # PC アプリ自身の選択（pc.py）: マッチ成立の通知、Windows 起動時の開始
                store.save(**{key: request.form.get(key) == "on" for key in pc.DEFAULTS})
                saved = tr("設定を保存しました。")
                if autostart is not None and autostart.possible:
                    wanted = request.form.get("autostart") == "on"
                    if not autostart.set(wanted):
                        saved, riot_problem = "", tr("スタートアップに登録できませんでした。")
                if do == "alert_test" and current_app.config["VAL_ALERT"] is not None:
                    current_app.config["VAL_ALERT"]("found", always=True)
                    saved = tr("通知をテストしました。")
            else:
                key = request.form.get("henrik_key", "").strip()
                if key:
                    store.save(henrik_key=key[:200])
                    saved = tr("API キーを保存しました。")
        key = store.henrik_key()
        shown = f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("●●●●" if key else "")
        status = riot.status()
        until = datetime.fromtimestamp(status["until"], stats.JST).strftime("%H:%M") if status["until"] else ""
        return render_template(
            "settings.html",
            shown=shown,
            saved=saved,
            riot=status,
            accounts=riot.accounts(),
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

    from valtracker import compare, maps, owner, seasons, skinlib, tools, weapons, world

    tools.register(app)
    seasons.register(app)
    world.register(app)
    skinlib.register(app)
    owner.register(app)
    compare.register(app)
    weapons.register(app)
    maps.register(app)

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", status=404, message=tr("ページが見つかりません")), 404

    @app.errorhandler(400)
    @app.errorhandler(403)
    def refused(error):
        return render_template("error.html", status=error.code, message=tr("この操作はできませんでした。")), error.code
