"""各サーバーの混み具合と、次の試合までの待ち時間: このサイトが見た試合から計算する（Riot も HenrikDev も、
何人遊んでいるかもキューの長さも教えないため）。

サイトが読む試合（プレイヤーの一覧、試合ページ、下のコレクター）ごとに 1 行残す: リージョン、
サーバー（``cluster``: 東京、香港など）、キュー、開始、長さ、プレイヤーの平均ランク。
同じプレイヤーの連続する 2 試合からはその間の時間を残す: 1 つ目の終わりから次の開始まで＝キュー・エージェント選択・
ロードの合計（休憩はもっと長いので省く）。プレイヤーの名前も ID も残さない: 間は 2 つの試合だけでわかる。

そこからサーバーごとに: 1 日のどの時間に試合が始まるか（その日の試合に対する割合なので、
見た試合が何件でも混む時間が目立つ）、キューとランク帯ごとの待ち時間の中央値。標本からの推定なので、ページにもそう書き、
何試合からの数字かも出す。

5 対 5 の試合の各プレイヤーの成績も、誰のものかは残さずに残す: ランク、ACS、ADR、K/D、HS%、KAST、DDΔ、チームが勝ったか。
これがプレイヤーページのスコアと「上位 X%」の物差しになる: 同じランク帯の成績の中でどこにいるか（score.py）。

コレクターは裏でさらに何人かを見る（数分に 1 人、見た試合のプレイヤーから、標本の少ないランク帯から）ので、
数字は訪問者だけに頼らない。ページと同じ HenrikDev のキーと予算を使い、キーが無ければ止まる。"""

from __future__ import annotations

import itertools
import json
import random
import sqlite3
import statistics
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from valtracker import pdmatch, stats
from valtracker.henrik import slim_match
from valtracker.riot import RiotError
from valtracker.util import as_dict, as_list, to_int

#: コレクターが回るリージョン（HenrikDev の名前）。
REGIONS = ("ap", "kr", "eu", "na", "latam", "br")
#: これより古い行は捨てる。
KEEP_DAYS = 28
#: ページが数える日数。
WINDOW_DAYS = 14
#: これより長い待ちはキューではなく休憩、これより短いものは本物ではない。
GAP_MAX = 20 * 60
GAP_MIN = 30
#: 待ち時間用のランク帯（コンペティティブの tier、最初..最後）: 低ランクは上位より待ちが短い。
BANDS = (
    ("iron-gold", 3, 14),
    ("plat-diamond", 15, 20),
    ("ascendant", 21, 23),
    ("immortal-radiant", 24, 27),
)
#: 標本がこれより少ない数字は出さない。
FEW = 6
#: 混む時間をページに出すのに必要な試合数: 少ないと 1 人の一晩が「ピーク」になってしまう。
ENOUGH = 48
#: プレイヤーの成績を物差しにする 5 対 5 のキュー（デスマッチなどは似ていない）。
LINE_QUEUES = ("competitive", "unrated", "premier", "swiftplay")
#: 成績のうちほかと比べる数値。
LINE_STATS = ("acs", "adr", "kd", "hs", "kast", "dd")
#: ランク帯が自分の物差しになるのに必要な成績数（足りなければ全帯をまとめる）。全体でこれより少なければ何も比べない。
LINES_ENOUGH = 200
#: ランク分布（とその中のプレイヤーの位置）を出すのにリージョンで必要なコンペティティブの成績数。
RANKS_ENOUGH = 100
#: コレクター: 次のプレイヤーまでの秒数（0: 止める）と、選べる最近のプレイヤーの数。
COLLECT_SECONDS = 240
#: 1 回でゲーム自身の記録から読むプレイヤー 1 人の試合数（Collector._riot）: 数分ごとに数回の呼び出し。
RIOT_MATCHES = 3
CANDIDATES = 600
#: 構成（5 体の組み合わせ）を出す数と、出すのに要る試合数（1 試合の偶然を「構成」にしない）。
COMPS_SHOWN = 10
COMPS_MIN = 2


def _unix(value) -> int:
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except (TypeError, ValueError):
        return 0


def quiet_hours(shares: list[float]) -> tuple[int, int] | None:
    """1 日の割合の中で静かな時間（平均の 0.7 以下）が一番長く続く範囲。
    0 時をまたいでもよい: （最初の時間, 最後の次の時間）、無ければ None。"""
    quiet = [share <= 0.7 * 100 / 24 for share in shares]
    if all(quiet) or not any(quiet):
        return None
    best, start = (0, 0), None
    for index in range(48):  # 2 周して、0 時をまたぐ範囲も見る
        if quiet[index % 24]:
            start = index if start is None else start
            if index - start + 1 > best[1] - best[0] and index - start < 24:
                best = (start, index + 1)
        else:
            start = None
    return best[0] % 24, best[1] % 24


def band(tier: int) -> str:
    for name, low, high in BANDS:
        if low <= tier <= high:
            return name
    return ""


def _tier(player: dict) -> int:
    """HenrikDev のプレイヤーの tier（番号か {"id": 番号}）。"""
    tier = player.get("tier")
    return to_int(tier.get("id") if isinstance(tier, dict) else tier)


def _teams(match: dict) -> dict[str, bool]:
    """チーム ID → 勝ったか。"""
    return {
        str(team.get("team_id") or ""): bool(team.get("won"))
        for team in match.get("teams") or []
        if isinstance(team, dict) and team.get("team_id")
    }


def _facts(match: dict) -> dict | None:
    """（小さくした）試合から行に残すもの: 開始かサーバーの無いものは None。"""
    metadata = as_dict(match.get("metadata"))
    queue = as_dict(metadata.get("queue"))
    started = _unix(metadata.get("started_at"))
    match_id = str(metadata.get("match_id") or "")
    if not started or not match_id:
        return None
    tiers = [tier for player in match.get("players") or [] if isinstance(player, dict) and (tier := _tier(player)) > 2]
    return {
        "id": match_id,
        "region": str(metadata.get("region") or "").lower(),
        "cluster": str(metadata.get("cluster") or ""),
        "queue": str(queue.get("id") or "").lower(),
        "started": started,
        "length": to_int(metadata.get("game_length_in_ms")) // 1000,
        "tier": round(sum(tiers) / len(tiers)) if tiers else 0,
    }


def _map_id(match: dict) -> str:
    """試合のマップの uuid（valorant-api.com のもの、小文字）。無ければ ""。"""
    return str(as_dict(as_dict(match.get("metadata")).get("map")).get("id") or "").lower()


def _picks(match: dict) -> list[tuple]:
    """2 チームの試合での各チームのエージェントと、
    そのチームが勝ったか: ``(match, team, agent, won, queue, tier, map, started)``。プレイヤーは残さず、
    エージェントとチームだけ。"""
    facts = _facts(match)
    teams = _teams(match)
    if not facts or len(teams) != 2 or not any(teams.values()):
        return []  # デスマッチか引き分け: 数えるものが無い
    map_id = _map_id(match)
    found = {}
    for player in match.get("players") or []:
        if not isinstance(player, dict):
            continue
        agent = as_dict(player.get("agent"))
        team, uuid = str(player.get("team_id") or ""), str(agent.get("id") or "").lower()
        if team in teams and uuid:
            found[(team, uuid)] = (
                facts["id"],
                team,
                uuid,
                int(teams[team]),
                facts["queue"],
                facts["tier"],
                map_id,
                facts["started"],
            )
    return list(found.values())


def _sides(match: dict) -> tuple | None:
    """2 チームの試合の攻守とサイト（ダイジェストのラウンドから）: ``(match, map, queue, tier, started,
    攻撃側が取ったラウンド, 攻守のわかるラウンド, サイトの JSON {"A": [設置したラウンド, そのうち攻撃側が取った数]})``。
    ラウンドの無い試合（コレクターの一覧など）と途中で終わった試合は None。"""
    facts = _facts(match)
    map_id = _map_id(match)
    if not facts or not map_id:
        return None
    won = played = 0
    sites: dict[str, list[int]] = {}
    for item in as_list(as_dict(match.get("digest")).get("rounds")):
        if not isinstance(item, dict) or not item.get("a") or not item.get("w"):
            continue
        attacked = item["w"] == item["a"]
        played += 1
        won += attacked
        site = str(item.get("s") or "")[:4]
        if site:  # 設置したラウンドだけサイトがある（rounds._round）
            counted = sites.setdefault(site, [0, 0])
            counted[0] += 1
            counted[1] += attacked
    if played < 5:
        return None
    return (facts["id"], map_id, facts["queue"], facts["tier"], facts["started"], won, played, json.dumps(sites))


def _lines(match: dict) -> list[tuple]:
    """5 対 5 の試合での各プレイヤーの成績: ``(match, slot, queue, tier, acs, adr, kd, hs, kast, dd, won, started)``。
    ``won`` は 1、0.5（引き分け）、0。プレイヤーは残さない: ``slot`` は一覧での位置。"""
    facts = _facts(match)
    if not facts or facts["queue"] not in LINE_QUEUES:
        return []
    rounds = stats.rounds_played(match)
    if rounds < 5:  # 途中で終わった（リメイク）: 試合 1 つ分の数値にならない
        return []
    teams = _teams(match)
    draw = len(teams) == 2 and not any(teams.values())
    rows = []
    for slot, player in enumerate(match.get("players") or []):
        if not isinstance(player, dict) or not player.get("stats"):
            continue
        number = _tier(player)
        found = stats.line(player, rounds, stats.advanced(match, player))
        won = 0.5 if draw else float(teams.get(str(player.get("team_id") or ""), False))
        rows.append(
            (
                facts["id"],
                slot,
                facts["queue"],
                number,
                found["acs"],
                found["adr"],
                found["kd"],
                found["hs"],
                found["kast"],
                found["dd"],
                won,
                facts["started"],
            )
        )
    return rows


def _shaped(kind: str, answer):
    """その名前のメソッドが返す形のサイトの答え（JSON）。その形でなければ None。"""
    if kind == "yardstick":
        if not (isinstance(answer, list) and len(answer) == 3 and isinstance(answer[0], dict)):
            return None
        stick = {str(stat): [float(v) for v in values] for stat, values in answer[0].items() if stat in LINE_STATS}
        return stick, int(answer[1]), str(answer[2])
    if not isinstance(answer, dict):
        return None
    if kind == "ranks":
        tiers = as_dict(answer.get("tiers"))
        return {"lines": int(answer.get("lines") or 0), "tiers": {int(tier): int(n) for tier, n in tiers.items()}}
    wanted = {
        "agents": ("matches", "agents", "maps"),
        "servers": ("servers", "waits", "lengths", "matches"),
        "maps": ("matches", "maps"),
        "comps": ("teams", "comps"),
        "agent": ("matches", "picks", "wins", "maps", "bands"),
    }[kind]
    return answer if all(name in answer for name in wanted) else None


class Activity:
    def __init__(self, folder: Path, clock=time.time):
        #: PC アプリ: サイトの数字の聞き方 (kind, params) -> JSON（_ask）。サイトでは None
        self.shared: Callable[[str, dict], dict | None] | None = None
        self._shared: dict = {}
        self.path = Path(folder) / "activity.sqlite"
        self.clock = clock
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._summary: dict[tuple, tuple[float, dict]] = {}
        #: ランク帯ごと（"" は全帯）: （読んだ時刻, {stat: 並べた値}, 成績の数）
        self._yardsticks: dict[str, tuple[float, dict, int]] = {}
        self._pruned = 0.0
        #: 最近の試合で見たプレイヤー（コレクター用）: (region, puuid, tier)。メモリだけ
        self.candidates: list[tuple[str, str, int]] = []

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, region TEXT, cluster TEXT, queue TEXT,
                    started INTEGER, length INTEGER, tier INTEGER);
                CREATE INDEX IF NOT EXISTS matches_started ON matches (started);
                CREATE TABLE IF NOT EXISTS gaps (
                    before TEXT, after TEXT, region TEXT, queue TEXT, tier INTEGER, at INTEGER, seconds INTEGER,
                    PRIMARY KEY (before, after));
                CREATE INDEX IF NOT EXISTS gaps_at ON gaps (at);
                CREATE TABLE IF NOT EXISTS picks (
                    match TEXT, team TEXT, agent TEXT, won INTEGER, queue TEXT, tier INTEGER, map TEXT,
                    started INTEGER, PRIMARY KEY (match, team, agent));
                CREATE INDEX IF NOT EXISTS picks_started ON picks (started);
                CREATE TABLE IF NOT EXISTS lines (
                    match TEXT, slot INTEGER, queue TEXT, tier INTEGER, acs REAL, adr REAL, kd REAL, hs REAL,
                    kast REAL, dd REAL, won REAL, started INTEGER, PRIMARY KEY (match, slot));
                CREATE INDEX IF NOT EXISTS lines_started ON lines (started);
                CREATE TABLE IF NOT EXISTS sides (
                    match TEXT PRIMARY KEY, map TEXT, queue TEXT, tier INTEGER, started INTEGER,
                    atk INTEGER, rounds INTEGER, sites TEXT);
                CREATE INDEX IF NOT EXISTS sides_started ON sides (started);
                """
            )
        return self._db

    # -- 見たもの ---------------------------------------------------------------------------------------

    def _ask(self, kind: str, params: dict):
        """PC アプリ用のサイト自身の数字（``shared``: サイトへの聞き方。サイト自身では None）
        : サイトは一日中試合を見ていて、アプリ自身のファイルには持ち主が開いたものしか無い。 10 分持つ。
        サイトが答えなければ None（5 分後に聞き直す）で、アプリ自身の数字を出す。"""
        if self.shared is None:
            return None
        key = (kind, tuple(sorted(params.items())))
        now = self.clock()
        kept = self._shared.get(key)
        if kept and now < kept[0]:
            return kept[1]
        try:
            found = _shaped(kind, self.shared(kind, params))
        except Exception:  # 途中で何が起きても: アプリ自身の数字
            found = None
        if len(self._shared) > 200:
            self._shared.clear()
        self._shared[key] = (now + (600 if found is not None else 300), found)
        return found

    def seen(self, matches: list, puuid: str = "") -> int:
        """試合を残す（``puuid`` がその人のものならプレイヤーの一覧として、試合の間の待ちも）。新しかった数を返す。"""
        facts = [found for match in matches or [] if isinstance(match, dict) for found in [_facts(match)] if found]
        if not facts:
            return 0
        gaps = []
        if puuid:
            ordered = sorted(facts, key=lambda item: item["started"])
            for before, after in itertools.pairwise(ordered):
                wait = after["started"] - (before["started"] + before["length"])
                if before["length"] and GAP_MIN <= wait <= GAP_MAX and before["queue"] == after["queue"]:
                    gaps.append(
                        (
                            before["id"],
                            after["id"],
                            after["region"],
                            after["queue"],
                            after["tier"],
                            after["started"],
                            wait,
                        )
                    )
        self._remember(matches)
        picks = [row for match in matches or [] if isinstance(match, dict) for row in _picks(match)]
        lines = [row for match in matches or [] if isinstance(match, dict) for row in _lines(match)]
        sides = [found for match in matches or [] if isinstance(match, dict) for found in [_sides(match)] if found]
        rows = [(f["id"], f["region"], f["cluster"], f["queue"], f["started"], f["length"], f["tier"]) for f in facts]
        try:
            with self._lock:
                db = self._connect()
                before = db.total_changes
                db.executemany("INSERT OR IGNORE INTO matches VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                new = db.total_changes - before
                db.executemany("INSERT OR IGNORE INTO gaps VALUES (?, ?, ?, ?, ?, ?, ?)", gaps)
                db.executemany("INSERT OR IGNORE INTO picks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", picks)
                # 一覧で先に見た試合（ラウンド無し）を、あとでラウンドつきで読んだときも入る
                counted = db.total_changes
                db.executemany("INSERT OR IGNORE INTO sides VALUES (?, ?, ?, ?, ?, ?, ?, ?)", sides)
                sided = db.total_changes != counted
                fresh = db.total_changes
                db.executemany("INSERT OR IGNORE INTO lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", lines)
                if db.total_changes != fresh:
                    self._yardsticks.clear()
                self._prune(db)
                db.commit()
        except (sqlite3.Error, OverflowError):  # OverflowError: SQLite の整数に入らない数（壊れた答え）
            return 0  # 数字はおまけ: ディスクがいっぱい・ロック中でもページを壊さない
        if new or sided:
            self._summary.clear()
        return new

    def _remember(self, matches: list) -> None:
        for match in matches or []:
            region = str(as_dict(as_dict(match).get("metadata")).get("region") or "").lower()
            for player in as_list(as_dict(match).get("players")):
                if not isinstance(player, dict) or not player.get("puuid"):
                    continue
                self.candidates.append((region, str(player["puuid"]), _tier(player)))
        del self.candidates[:-CANDIDATES]

    def _prune(self, db: sqlite3.Connection) -> None:
        now = self.clock()
        if now - self._pruned < 3600:
            return
        self._pruned = now
        oldest = int(now - KEEP_DAYS * 86400)
        db.execute("DELETE FROM matches WHERE started < ?", (oldest,))
        db.execute("DELETE FROM gaps WHERE at < ?", (oldest,))
        db.execute("DELETE FROM picks WHERE started < ?", (oldest,))
        db.execute("DELETE FROM lines WHERE started < ?", (oldest,))
        db.execute("DELETE FROM sides WHERE started < ?", (oldest,))

    # -- ページに出すもの -------------------------------------------------------------------------------

    def servers(self, region: str, offset_hours: float = 9) -> dict:
        """リージョンの直近 WINDOW_DAYS
        日の ``{"servers": [{"name", "matches", "hours": [24 個の割合], "now": 割合, "level", "peak", "quiet", "tier"}], "waits": [{"queue", "band", "seconds", "samples"}], "lengths": [{"queue", "seconds", "samples"}], "matches",
        "hour_now"}``。時間は訪問者の時刻（UTC から ``offset_hours``）。試合が ENOUGH 未満のサーバーは level "few" で、
        ピークも静かな時間も無し。数分持つ。"""
        shared = self._ask("servers", {"region": region, "offset": offset_hours})
        if shared is not None:
            return shared
        key = (region, offset_hours)
        now = self.clock()
        kept = self._summary.get(key)
        if kept and now - kept[0] < 300:
            return kept[1]
        since = int(now - WINDOW_DAYS * 86400)
        try:
            with self._lock:
                db = self._connect()
                matches = db.execute(
                    "SELECT cluster, started, tier FROM matches WHERE region = ? AND started >= ? AND cluster != ''",
                    (region, since),
                ).fetchall()
                lengths = db.execute(
                    "SELECT queue, length FROM matches WHERE region = ? AND started >= ? AND length > 0",
                    (region, since),
                ).fetchall()
                waits = db.execute(
                    "SELECT queue, tier, seconds FROM gaps WHERE region = ? AND at >= ?", (region, since)
                ).fetchall()
        except sqlite3.Error:
            matches, waits, lengths = [], [], []
        hour_now = int(((now / 3600) + offset_hours) % 24)
        by_server: dict[str, list] = {}
        for cluster, started, tier in matches:
            by_server.setdefault(cluster, []).append((int(((started / 3600) + offset_hours) % 24), tier))
        servers = []
        for name, items in by_server.items():
            counts = [0] * 24
            for hour, _ in items:
                counts[hour] += 1
            shares = [round(100 * count / len(items), 1) for count in counts]
            tiers = [tier for _, tier in items if tier]
            # サーバーの平均の 1 時間（試合の 100/24 %）と比べた今の時間
            level = shares[hour_now] / (100 / 24)
            enough = len(items) >= ENOUGH
            servers.append(
                {
                    "name": name,
                    "matches": len(items),
                    "hours": shares,
                    "now": shares[hour_now],
                    "level": ("busy" if level >= 1.3 else "quiet" if level <= 0.7 else "usual") if enough else "few",
                    "peak": shares.index(max(shares)) if enough else None,
                    "quiet": quiet_hours(shares) if enough else None,
                    "tier": round(sum(tiers) / len(tiers)) if tiers else 0,
                }
            )
        servers.sort(key=lambda server: -server["matches"])
        grouped: dict[tuple[str, str], list[int]] = {}
        for queue, tier, seconds in waits:
            grouped.setdefault((queue, ""), []).append(seconds)
            if queue == "competitive" and band(tier):
                grouped.setdefault((queue, band(tier)), []).append(seconds)
        listed = [
            {"queue": queue, "band": name, "seconds": round(statistics.median(values)), "samples": len(values)}
            for (queue, name), values in grouped.items()
            if len(values) >= FEW
        ]
        by_queue: dict[str, list[int]] = {}
        for queue, length in lengths:
            by_queue.setdefault(queue, []).append(length)
        found = {
            "servers": servers,
            "lengths": sorted(
                (
                    {"queue": queue, "seconds": round(statistics.median(values)), "samples": len(values)}
                    for queue, values in by_queue.items()
                    if len(values) >= FEW
                ),
                key=lambda item: -item["samples"],
            ),
            "waits": sorted(
                listed,
                key=lambda item: (-sum(i["samples"] for i in listed if i["queue"] == item["queue"]), item["band"]),
            ),
            "matches": len(matches),
            "hour_now": hour_now,
        }
        self._summary[key] = (now, found)
        return found

    def agents(self, queue: str = "competitive", band_name: str = "", map_id: str = "") -> dict:
        """直近 WINDOW_DAYS 日に見た試合での各エージェントのピック率とチームの勝率:
        ``{"matches", "agents": [{"agent", "picks", "wins", "pick"（チームに対する %）, "win"（%）
        }], "maps": {map: 試合数}}``（ピックの多い順）。キュー、ランク帯（BANDS の名前）、マップで絞る。数分持つ。"""
        shared = self._ask("agents", {"queue": queue, "band": band_name, "map": map_id})
        if shared is not None:
            return shared
        key = ("agents", queue, band_name, map_id)
        now = self.clock()
        kept = self._summary.get(key)
        if kept and now - kept[0] < 300:
            return kept[1]
        since = int(now - WINDOW_DAYS * 86400)
        try:
            with self._lock:
                rows = (
                    self._connect()
                    .execute(
                        "SELECT match, agent, won, tier, map FROM picks WHERE queue = ? AND started >= ?",
                        (queue, since),
                    )
                    .fetchall()
                )
        except sqlite3.Error:
            rows = []
        if band_name:
            rows = [row for row in rows if band(row[3]) == band_name]
        maps: dict[str, set] = {}
        for row in rows:
            if row[4]:
                maps.setdefault(row[4], set()).add(row[0])
        if map_id:
            rows = [row for row in rows if row[4] == map_id]
        matches = len({row[0] for row in rows})
        counts: dict[str, list[int]] = {}
        for _, agent, won, _, _ in rows:
            counts.setdefault(agent, [0, 0])
            counts[agent][0] += 1
            counts[agent][1] += won
        listed = [
            {
                "agent": agent,
                "picks": picks,
                "wins": wins,
                "pick": round(100 * picks / (2 * matches), 1) if matches else 0.0,
                "win": round(100 * wins / picks, 1) if picks else 0.0,
            }
            for agent, (picks, wins) in counts.items()
        ]
        listed.sort(key=lambda item: (-item["picks"], item["agent"]))
        found = {"matches": matches, "agents": listed, "maps": {key: len(seen) for key, seen in maps.items()}}
        self._summary[key] = (now, found)
        return found

    def _select(self, query: str, params: tuple) -> list:
        """読むだけの問い合わせの行（読めなければ空: 数字はおまけ）。"""
        try:
            with self._lock:
                return self._connect().execute(query, params).fetchall()
        except sqlite3.Error:
            return []

    def _kept(self, key: tuple, seconds: float = 300):
        """数分持つ集計（無いか古ければ None）。"""
        kept = self._summary.get(key)
        return kept[1] if kept and self.clock() - kept[0] < seconds else None

    def maps(self, queue: str = "competitive") -> dict:
        """直近 WINDOW_DAYS 日のマップごとの攻守とサイト（ラウンドまで読んだ試合から）: ``{"matches", "maps": {map:
        {"matches", "atk": [攻撃側が取ったラウンド, ラウンド], "sites": {"A": [設置, そのうち攻撃側が取った]}}}}``。"""
        shared = self._ask("maps", {"queue": queue})
        if shared is not None:
            return shared
        key = ("maps", queue)
        found = self._kept(key)
        if found is not None:
            return found
        since = int(self.clock() - WINDOW_DAYS * 86400)
        rows = self._select(
            "SELECT map, atk, rounds, sites FROM sides WHERE queue = ? AND started >= ?", (queue, since)
        )
        maps: dict[str, dict] = {}
        for map_id, won, played, sites in rows:
            item = maps.setdefault(map_id, {"matches": 0, "atk": [0, 0], "sites": {}})
            item["matches"] += 1
            item["atk"][0] += won
            item["atk"][1] += played
            try:
                planted = json.loads(sites or "{}")
            except ValueError:
                planted = {}
            for site, counted in as_dict(planted).items():
                if isinstance(counted, list) and len(counted) == 2:
                    total = item["sites"].setdefault(str(site), [0, 0])
                    total[0] += to_int(counted[0])
                    total[1] += to_int(counted[1])
        found = {"matches": len(rows), "maps": maps}
        self._summary[key] = (self.clock(), found)
        return found

    def comps(self, queue: str = "competitive", map_id: str = "", agent: str = "") -> dict:
        """直近 WINDOW_DAYS 日の 5 体の構成ごとの試合数と勝率: ``{"teams": 5 体がそろったチームの数, "comps":
        [{"agents": [uuid × 5、並べ替え済み], "games", "wins", "win"（%）}]}``（多い順に COMPS_SHOWN まで、
        COMPS_MIN 試合から）。マップと、構成に入っているエージェントで絞る。"""
        shared = self._ask("comps", {"queue": queue, "map": map_id, "agent": agent})
        if shared is not None:
            return shared
        key = ("comps", queue, map_id, agent)
        found = self._kept(key)
        if found is not None:
            return found
        since = int(self.clock() - WINDOW_DAYS * 86400)
        rows = self._select(
            "SELECT match, team, agent, won, map FROM picks WHERE queue = ? AND started >= ?", (queue, since)
        )
        teams: dict[tuple[str, str], list] = {}
        for match, team, uuid, won, place in rows:
            if map_id and place != map_id:
                continue
            teams.setdefault((match, team), [set(), won])[0].add(uuid)
        counts: dict[tuple, list[int]] = {}
        full = 0
        for agents, won in teams.values():
            if len(agents) != 5:
                continue
            full += 1
            if agent and agent not in agents:
                continue
            counted = counts.setdefault(tuple(sorted(agents)), [0, 0])
            counted[0] += 1
            counted[1] += won
        listed = [
            {"agents": list(agents), "games": games, "wins": wins, "win": round(100 * wins / games, 1)}
            for agents, (games, wins) in counts.items()
            if games >= COMPS_MIN
        ]
        listed.sort(key=lambda item: (-item["games"], -item["win"], item["agents"]))
        found = {"teams": full, "comps": listed[:COMPS_SHOWN]}
        self._summary[key] = (self.clock(), found)
        return found

    def agent(self, uuid: str, queue: str = "competitive") -> dict:
        """1 体のエージェントの直近 WINDOW_DAYS 日: ``{"matches"（全試合）, "picks", "wins", "maps": {map: [ピック, 勝ち,
        そのマップの試合]}, "bands": {帯: [ピック, 勝ち, その帯の試合]}}``。ピック率はピック ÷ (2 × 試合)。"""
        shared = self._ask("agent", {"queue": queue, "agent": uuid})
        if shared is not None:
            return shared
        key = ("agent", queue, uuid)
        found = self._kept(key)
        if found is not None:
            return found
        since = int(self.clock() - WINDOW_DAYS * 86400)
        rows = self._select(
            "SELECT match, agent, won, tier, map FROM picks WHERE queue = ? AND started >= ?", (queue, since)
        )
        matches: set[str] = set()
        by_map: dict[str, set] = {}
        by_band: dict[str, set] = {}
        found = {"matches": 0, "picks": 0, "wins": 0, "maps": {}, "bands": {}}
        for match, picked, won, tier, place in rows:
            matches.add(match)
            named = band(tier) if queue == "competitive" else ""
            if place:
                by_map.setdefault(place, set()).add(match)
            if named:
                by_band.setdefault(named, set()).add(match)
            if picked != uuid:
                continue
            found["picks"] += 1
            found["wins"] += won
            for part, name in (("maps", place), ("bands", named)):
                if name:
                    counted = found[part].setdefault(name, [0, 0, 0])
                    counted[0] += 1
                    counted[1] += won
        found["matches"] = len(matches)
        for part, seen in (("maps", by_map), ("bands", by_band)):
            for name, counted in found[part].items():
                counted[2] = len(seen.get(name, ()))
        self._summary[key] = (self.clock(), found)
        return found

    def ranks(self, region: str) -> dict:
        """リージョンの直近 WINDOW_DAYS 日のコンペティティブの成績（1 試合 1 人 1 件）
        が各ランクに何件あるか: ``{"lines", "tiers": {tier: 件数}}``。
        多く遊ぶ人ほど多く数えられる（見分けるためのプレイヤーを残さない）。10 分ごとに読み直す。"""
        shared = self._ask("ranks", {"region": region})
        if shared is not None:
            return shared
        key = ("ranks", region)
        now = self.clock()
        kept = self._summary.get(key)
        if kept and now - kept[0] < 600:
            return kept[1]
        query = (
            "SELECT lines.tier, COUNT(*) FROM lines JOIN matches ON matches.id = lines.match"
            " WHERE lines.queue = 'competitive' AND lines.started >= ? AND lines.tier > 2 AND matches.region = ?"
            " GROUP BY lines.tier"
        )
        try:
            with self._lock:
                rows = self._connect().execute(query, (int(now - WINDOW_DAYS * 86400), region)).fetchall()
        except sqlite3.Error:
            rows = []
        found = {"lines": sum(count for _, count in rows), "tiers": {int(tier): count for tier, count in rows}}
        self._summary[key] = (now, found)
        return found

    def yardstick(self, tier: int) -> tuple[dict, int, str]:
        """直近 WINDOW_DAYS 日の ``tier`` のランク帯の成績。数値ごとに並べたもの（``{stat: [値]}``）、成績の数、
        帯の名前（全帯が物差しのときは ""。帯の成績が LINES_ENOUGH 未満のときなど）。
        全体で LINES_ENOUGH 未満なら何も無し（``{}, 0, ""``）。 10 分ごとに読み直す。"""
        shared = self._ask("yardstick", {"tier": int(tier)})
        if shared is not None:
            return shared
        name = band(tier)
        own = self._sticks(name) if name else ({}, 0)
        if own[1] >= LINES_ENOUGH:
            return own[0], own[1], name
        every = self._sticks("")
        return (every[0], every[1], "") if every[1] >= LINES_ENOUGH else ({}, 0, "")

    def _sticks(self, name: str) -> tuple[dict, int]:
        now = self.clock()
        kept = self._yardsticks.get(name)
        if kept and now - kept[0] < 600:
            return kept[1], kept[2]
        since = int(now - WINDOW_DAYS * 86400)
        query = f"SELECT tier, {', '.join(LINE_STATS)} FROM lines WHERE started >= ?"
        try:
            with self._lock:
                rows = self._connect().execute(query, (since,)).fetchall()
        except sqlite3.Error:
            rows = []
        if name:
            rows = [row for row in rows if band(row[0]) == name]
        sorted_stats = {
            stat: sorted(row[index + 1] for row in rows if row[index + 1] is not None)
            for index, stat in enumerate(LINE_STATS)
        }
        self._yardsticks[name] = (now, sorted_stats, len(rows))
        return sorted_stats, len(rows)

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def count(self) -> int:
        try:
            with self._lock:
                return self._connect().execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        except sqlite3.Error:
            return 0


class Collector:
    """専用のスレッドで COLLECT_SECONDS ごとに 1 人ずつ見る（モジュールの説明を参照）。"""

    def __init__(self, activity: Activity, henrik, seconds: float = COLLECT_SECONDS, riot=None, maps=None):
        self.activity, self.henrik, self.seconds = activity, henrik, seconds
        #: ゲーム自身の記録用の、持ち主の Riot アカウント（riot.Riot）とマップの索引（呼び出せるもの）
        self.riot, self.maps = riot, maps
        #: ゲーム自身の記録から読んだ試合。もう読まない
        self.read: set[str] = set()
        self._started = False
        self._lock = threading.Lock()
        self.looked: set[str] = set()
        #: リージョンごとに見たプレイヤーの数
        self.regions: dict[str, int] = {}

    def start(self) -> None:
        with self._lock:
            if self._started or not self.seconds:
                return
            self._started = True
        threading.Thread(target=self._run, name="activity", daemon=True).start()

    def _run(self) -> None:
        while True:
            time.sleep(self.seconds)
            try:
                self.once()
            except Exception:  # 失敗した回は次にまた試す
                pass

    def _riot(self) -> int | None:
        """ゲーム自身の記録（PD の試合履歴と試合詳細、持ち主の連携アカウント）
        からの 1 人のプレイヤーの最新の試合: 持ち主が会ったことのない人のまるごとの試合を、
        HenrikDev のキーも予算も使わずに。持ち主自身のリージョンだけ（記録はリージョンごと）。最初は持ち主、
        次に読んだ試合のプレイヤー。名前は読まない（pdmatch）。 None: 今回は無し（アカウントが無い、
        リージョンに新しい人がいない、 Riot が断った）なので、今までどおり HenrikDev に聞く。"""
        if self.riot is None or self.maps is None:
            return None
        try:
            status = self.riot.status()
            region = status["shard"]
            if not status["linked"] or status["signed_out"] or region not in REGIONS:
                return None
            fresh = [item for item in self.activity.candidates if item[0] == region and item[1] not in self.looked]
            puuid = random.choice(fresh)[1] if fresh else self.riot.me()
            if not puuid or puuid in self.looked:
                return None
            self.looked.add(puuid)
            self.regions[region] = self.regions.get(region, 0) + 1
            new = [item["id"] for item in self.riot.history(region, puuid)["matches"] if item["id"] not in self.read]
            matches = []
            for match_id in new[:RIOT_MATCHES]:
                self.read.add(match_id)
                raw = pdmatch.to_v4(self.riot.match_details(region, match_id), region, self.maps())
                if raw and raw["metadata"].get("is_completed", True):
                    matches.append(slim_match(raw))
        except RiotError:
            return None
        if len(self.read) > 5000:
            self.read.clear()
        return self.activity.seen(matches, puuid)

    def pick(self) -> tuple[str, str] | None:
        """まだ見ていないプレイヤー: これまで一番少ないリージョン、次にランク帯から。"""
        fresh = [item for item in self.activity.candidates if item[1] not in self.looked and item[0]]
        if not fresh:
            return None
        bands: dict[str, int] = {}
        for _, puuid, tier in self.activity.candidates:
            if puuid in self.looked:
                bands[band(tier)] = bands.get(band(tier), 0) + 1
        fresh.sort(key=lambda item: (self.regions.get(item[0], 0), bands.get(band(item[2]), 0), random.random()))
        region, puuid, _ = fresh[0]
        return region, puuid

    def once(self) -> int:
        """1 人のプレイヤーの最新の試合を数字に入れる。新しかった試合数を返す。時々（と、見る人がいなくなったとき）
        一番見ていないリージョンのランキングから 1 人選び、どのリージョンも埋まるようにする。"""
        own = self._riot()
        if own is not None:
            return own
        if not self.henrik.key():
            return 0
        chosen = self.pick()
        if chosen is None or random.random() < 0.1:
            region = min(REGIONS, key=lambda key: (self.regions.get(key, 0), random.random()))
            board = self.henrik.leaderboard(region)
            players = [item for item in board.get("players") or [] if item.get("puuid") and not item.get("hidden")]
            if players:
                chosen = (region, random.choice(players)["puuid"])
        if chosen is None:
            return 0
        region, puuid = chosen
        self.looked.add(puuid)
        self.regions[region] = self.regions.get(region, 0) + 1
        if len(self.looked) > 20 * CANDIDATES:
            self.looked.clear()
        return self.activity.seen(self.henrik.matches_by_puuid(region, puuid), puuid)
