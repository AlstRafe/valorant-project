"""How busy each server is, and how long the wait for the next match is: worked out from the matches this site sees,
because neither Riot nor HenrikDev tells how many play or how long the queues are.

Every match the site reads (a player's list, a match page, and the collector below) leaves one row: its region,
server (``cluster``: Tokyo, Hong Kong, ...), queue, start, length and the average rank of its players. Two matches
of one player in a row leave the time between them: from the end of one to the start of the next, which is the
queue, the agent select and the loading together (a break is longer, and is left out). No names or ids of players
are kept: a gap is known by its two matches only.

From these, per server: in which hours of the day its matches start (the share of the day's matches, so a busy
hour stands out however many matches were seen), and per queue and rank band the middle of the waits. They are an
estimate from a sample, and the pages say so, with how many matches it rests on.

The collector looks at a few more players in the background (one every few minutes, from the players of the
matches already seen, the rank bands with the fewest samples first), so the numbers do not depend on visitors
alone. It uses the same HenrikDev key and budget as the pages, and stops when there is no key.
"""

from __future__ import annotations

import random
import sqlite3
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

#: The regions the collector goes round (HenrikDev's names).
REGIONS = ("ap", "kr", "eu", "na", "latam", "br")
#: Rows older than this are dropped.
KEEP_DAYS = 28
#: The days the pages count.
WINDOW_DAYS = 14
#: A wait longer than this is a break, not the queue; shorter than this is not a real one.
GAP_MAX = 20 * 60
GAP_MIN = 30
#: Rank bands (competitive tiers, first..last) for the waits: low ranks wait less than the top.
BANDS = (
    ("iron-gold", 3, 14),
    ("plat-diamond", 15, 20),
    ("ascendant", 21, 23),
    ("immortal-radiant", 24, 27),
)
#: A number from fewer samples than this is not shown.
FEW = 6
#: Matches a server needs before the page says when it is busy: with fewer, one evening of one player is a "peak".
ENOUGH = 48
#: The collector: seconds between two players (0: off), and how many recent players it can pick from.
COLLECT_SECONDS = 240
CANDIDATES = 600


def _unix(value) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def quiet_hours(shares: list[float]) -> tuple[int, int] | None:
    """The longest run of quiet hours (at most 0.7 of the average hour) in a day of shares, going round midnight:
    (first hour, the hour after the last), or None."""
    quiet = [share <= 0.7 * 100 / 24 for share in shares]
    if all(quiet) or not any(quiet):
        return None
    best, start = (0, 0), None
    for index in range(48):  # twice round, for a run that crosses midnight
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


def _facts(match: dict) -> dict | None:
    """What a row keeps of a (slim) match: None for one without a start or a server."""
    metadata = match.get("metadata") or {}
    queue = metadata.get("queue") if isinstance(metadata.get("queue"), dict) else {}
    started = _unix(metadata.get("started_at"))
    match_id = str(metadata.get("match_id") or "")
    if not started or not match_id:
        return None
    tiers = []
    for player in match.get("players") or []:
        tier = player.get("tier") if isinstance(player, dict) else None
        number = tier.get("id") if isinstance(tier, dict) else tier
        try:
            if int(number or 0) > 2:
                tiers.append(int(number))
        except (TypeError, ValueError):
            pass
    return {
        "id": match_id,
        "region": str(metadata.get("region") or "").lower(),
        "cluster": str(metadata.get("cluster") or ""),
        "queue": str(queue.get("id") or "").lower(),
        "started": started,
        "length": int(int(metadata.get("game_length_in_ms") or 0) / 1000),
        "tier": round(sum(tiers) / len(tiers)) if tiers else 0,
    }


class Activity:
    def __init__(self, folder: Path, clock=time.time):
        self.path = Path(folder) / "activity.sqlite"
        self.clock = clock
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._summary: dict[tuple, tuple[float, dict]] = {}
        self._pruned = 0.0
        #: players seen in recent matches, for the collector: (region, puuid, tier); in memory only
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
                """
            )
        return self._db

    # -- what is seen ---------------------------------------------------------------------------------

    def seen(self, matches: list, puuid: str = "") -> int:
        """Keep the matches (a player's list when ``puuid`` is theirs: the waits between them too). Returns how
        many were new."""
        facts = [found for match in matches or [] if isinstance(match, dict) for found in [_facts(match)] if found]
        if not facts:
            return 0
        gaps = []
        if puuid:
            ordered = sorted(facts, key=lambda item: item["started"])
            for before, after in zip(ordered, ordered[1:], strict=False):
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
        rows = [(f["id"], f["region"], f["cluster"], f["queue"], f["started"], f["length"], f["tier"]) for f in facts]
        try:
            with self._lock:
                db = self._connect()
                before = db.total_changes
                db.executemany("INSERT OR IGNORE INTO matches VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
                new = db.total_changes - before
                db.executemany("INSERT OR IGNORE INTO gaps VALUES (?, ?, ?, ?, ?, ?, ?)", gaps)
                self._prune(db)
                db.commit()
        except sqlite3.Error:
            return 0  # the numbers are an extra: a full or locked disk must not break a page
        if new:
            self._summary.clear()
        return new

    def _remember(self, matches: list) -> None:
        for match in matches or []:
            region = str(((match or {}).get("metadata") or {}).get("region") or "").lower()
            for player in (match or {}).get("players") or []:
                if not isinstance(player, dict) or not player.get("puuid"):
                    continue
                tier = player.get("tier")
                number = tier.get("id") if isinstance(tier, dict) else tier
                try:
                    number = int(number or 0)
                except (TypeError, ValueError):
                    number = 0
                self.candidates.append((region, str(player["puuid"]), number))
        del self.candidates[:-CANDIDATES]

    def _prune(self, db: sqlite3.Connection) -> None:
        now = self.clock()
        if now - self._pruned < 3600:
            return
        self._pruned = now
        oldest = int(now - KEEP_DAYS * 86400)
        db.execute("DELETE FROM matches WHERE started < ?", (oldest,))
        db.execute("DELETE FROM gaps WHERE at < ?", (oldest,))

    # -- what the pages show -----------------------------------------------------------------------------

    def servers(self, region: str, offset_hours: float = 9) -> dict:
        """``{"servers": [{"name", "matches", "hours": [24 shares], "now": share, "level", "peak", "quiet",
        "tier"}], "waits": [{"queue", "band", "seconds", "samples"}], "lengths": [{"queue", "seconds", "samples"}],
        "matches", "hour_now"}`` for a region over the last WINDOW_DAYS, the hours in the visitor's time
        (``offset_hours`` from UTC). A server with fewer than ENOUGH matches has the level "few" and no peak or
        quiet hours. Kept a few minutes."""
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
                    "SELECT queue, tier, seconds, at FROM gaps WHERE region = ? AND at >= ?", (region, since)
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
            # this hour beside the server's average hour (100/24 % of its matches)
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
        for queue, tier, seconds, _ in waits:
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
    """Looks at one more player every COLLECT_SECONDS in a thread of its own (see the module's words)."""

    def __init__(self, activity: Activity, henrik, seconds: float = COLLECT_SECONDS):
        self.activity, self.henrik, self.seconds = activity, henrik, seconds
        self._started = False
        self._lock = threading.Lock()
        self.looked: set[str] = set()
        #: how many players of each region were looked at
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
            except Exception:  # noqa: BLE001 - a failed look is tried again next time
                pass

    def pick(self) -> tuple[str, str] | None:
        """A player not looked at yet: from the region, then the rank band, with the fewest of them so far."""
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
        """One player's latest matches into the numbers; how many matches were new. Now and then (and when no one
        is left to look at) a player of the leaderboard of the region looked at least, so every region fills."""
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
