"""Every match the site has read of a player, kept on disk, so a player's page can count all of them ("通算"), not
only the latest RECENT that HenrikDev lists.

Where the matches come from:

- each match HenrikDev gives the site (a player's latest, a match page): whole, with every player's line;
- the game's own records (Riot's PD, read with the owner's linked account), once a visitor opens a player's
  lifetime: their match history, then each match not kept yet, one call every FILL_SECONDS in the background
  (Filler), and the RR each competitive match moved. PD's names are left out (pdmatch.py);
- only when Riot cannot be asked (no linked account, or it refuses): HenrikDev's older pages of the player's
  matches;
- for what Riot no longer gives: HenrikDev's stored matches (v1), one line of the player per match without its
  rounds (no KAST, first kills, weapons or places: "thin").

Kept in instance/archive.sqlite: each match once (compressed, as henrik._slim_match keeps it), who played in it,
the thin lines, the RR changes, the matches still to read, and how far each player's history has been read.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path

from valtracker import pdmatch
from valtracker.henrik import PAGE, HenrikError, _slim_match
from valtracker.riot import HISTORY_PAGE, RiotError

#: Seconds between two of the background's calls to Riot (0: off): slow enough to leave the owner's own pages
#: (the live match, the store) room, and Riot's limits too.
FILL_SECONDS = 2.5
#: Seconds between two calls to HenrikDev, whose allowance every visitor shares; and the calls of that allowance
#: left for the visitors before the background asks.
HENRIK_SECONDS = 15
HENRIK_ROOM = 12
#: After Riot says "too many" (429), seconds before it is asked again.
REST = 120
#: A player whose history was read through is read again (the new matches only) when their lifetime is opened
#: this long after.
AGAIN = 20 * 60
#: Stored matches (HenrikDev v1) are read again after this long.
STORED_AGAIN = 6 * 3600
#: Where a match came from: HenrikDev's names every player, so it is kept over Riot's, which names one.
SOURCES = {"riot": 1, "henrik": 2}


def _pack(value) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":")).encode("utf-8"), 6)


def _unpack(blob) -> dict:
    try:
        value = json.loads(zlib.decompress(blob).decode("utf-8"))
    except (zlib.error, ValueError, UnicodeDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _started(match: dict) -> int:
    raw = str((match.get("metadata") or {}).get("started_at") or "")
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _queue(match: dict) -> str:
    queue = (match.get("metadata") or {}).get("queue")
    return str((queue or {}).get("id") or "").lower() if isinstance(queue, dict) else ""


class Archive:
    def __init__(self, folder: Path, clock=time.time):
        self.path = Path(folder) / "archive.sqlite"
        self.clock = clock
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, started INTEGER, queue TEXT, source INTEGER, data BLOB);
                CREATE TABLE IF NOT EXISTS played (
                    puuid TEXT, id TEXT, started INTEGER, queue TEXT, PRIMARY KEY (puuid, id));
                CREATE INDEX IF NOT EXISTS played_started ON played (puuid, started);
                CREATE TABLE IF NOT EXISTS thin (
                    puuid TEXT, id TEXT, started INTEGER, queue TEXT, data TEXT, PRIMARY KEY (puuid, id));
                CREATE TABLE IF NOT EXISTS rr (
                    puuid TEXT, id TEXT, at INTEGER, tier INTEGER, rr INTEGER, change INTEGER, elo INTEGER,
                    PRIMARY KEY (puuid, id));
                CREATE TABLE IF NOT EXISTS todo (puuid TEXT, id TEXT, at INTEGER, PRIMARY KEY (puuid, id));
                CREATE TABLE IF NOT EXISTS players (
                    puuid TEXT PRIMARY KEY, region TEXT, name TEXT, tag TEXT, wanted REAL, total INTEGER,
                    scanned INTEGER, rr_scanned INTEGER, rr_done INTEGER, older INTEGER, stored REAL,
                    finished REAL, again INTEGER, via TEXT, problem TEXT);
                """
            )
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # -- what is kept -----------------------------------------------------------------------------------

    def keep(self, matches: list, source: str = "henrik") -> int:
        """Finished matches (henrik._slim_match's shape) into the archive; how many were new. A match kept from
        HenrikDev is not replaced by Riot's (which names no one but the player)."""
        rank = SOURCES.get(source, 0)
        rows, played = [], []
        for match in matches or []:
            metadata = match.get("metadata") if isinstance(match, dict) else None
            if not isinstance(metadata, dict) or not metadata.get("match_id") or not metadata.get("is_completed", 1):
                continue
            match_id = str(metadata["match_id"]).lower()
            started, queue = _started(match), _queue(match)
            rows.append((match_id, started, queue, rank, _pack(match)))
            for player in match.get("players") or []:
                if isinstance(player, dict) and player.get("puuid"):
                    played.append((str(player["puuid"]).lower(), match_id, started, queue))
        if not rows:
            return 0
        try:
            with self._lock:
                db = self._connect()
                before = db.total_changes
                for row in rows:
                    db.execute(
                        "INSERT INTO matches VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                        "data = excluded.data, source = excluded.source WHERE excluded.source > matches.source",
                        row,
                    )
                new = db.total_changes - before
                db.executemany("INSERT OR IGNORE INTO played VALUES (?, ?, ?, ?)", played)
                db.executemany("DELETE FROM todo WHERE id = ?", [(row[0],) for row in rows])
                db.commit()
            return new
        except sqlite3.Error:
            return 0

    def keep_thin(self, puuid: str, lines: list) -> None:
        """A player's stored lines (henrik._slim_stored's shape)."""
        rows = [
            (puuid, str(line["id"]).lower(), int(line.get("at") or 0), str(line.get("queue") or ""), json.dumps(line))
            for line in lines or []
            if isinstance(line, dict) and line.get("id")
        ]
        self._write("INSERT OR REPLACE INTO thin VALUES (?, ?, ?, ?, ?)", rows)

    def keep_rr(self, puuid: str, changes: dict) -> None:
        """The RR each competitive match moved (henrik._slim_rr's shape)."""
        rows = [
            (
                puuid,
                str(match_id).lower(),
                int(item.get("at") or 0),
                int(item.get("tier") or 0),
                int(item.get("rr") or 0),
                int(item.get("change") or 0),
                int(item.get("elo") or 0),
            )
            for match_id, item in (changes or {}).items()
            if isinstance(item, dict)
        ]
        self._write("INSERT OR REPLACE INTO rr VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def _write(self, query: str, rows: list) -> None:
        if not rows:
            return
        try:
            with self._lock:
                db = self._connect()
                db.executemany(query, rows)
                db.commit()
        except sqlite3.Error:
            pass

    def _read(self, query: str, values: tuple = ()) -> list:
        try:
            with self._lock:
                return self._connect().execute(query, values).fetchall()
        except sqlite3.Error:
            return []

    # -- what the pages read ----------------------------------------------------------------------------

    def known(self, ids: list[str]) -> set[str]:
        """Which of these matches are kept whole."""
        ids = [str(item).lower() for item in ids]
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {row[0] for row in self._read(f"SELECT id FROM matches WHERE id IN ({marks})", tuple(ids))}

    def matches(self, puuid: str, queue: str = ""):
        """The player's whole matches, newest first (of one queue, or of all): each as henrik._slim_match keeps it."""
        query = "SELECT m.data FROM played p JOIN matches m ON m.id = p.id WHERE p.puuid = ?"
        values: tuple = (puuid,)
        if queue:
            query += " AND p.queue = ?"
            values += (queue,)
        for (blob,) in self._read(query + " ORDER BY p.started DESC", values):
            found = _unpack(blob)
            if found:
                yield found

    def thin(self, puuid: str, queue: str = "") -> list[dict]:
        """The player's stored lines of the matches not kept whole, newest first."""
        query = (
            "SELECT t.data FROM thin t WHERE t.puuid = ? AND NOT EXISTS "
            "(SELECT 1 FROM played p WHERE p.puuid = t.puuid AND p.id = t.id)"
        )
        values: tuple = (puuid,)
        if queue:
            query += " AND t.queue = ?"
            values += (queue,)
        found = []
        for (text,) in self._read(query + " ORDER BY t.started DESC", values):
            try:
                line = json.loads(text)
            except ValueError:
                continue
            if isinstance(line, dict):
                found.append(line)
        return found

    def rr(self, puuid: str) -> dict:
        """Every RR change kept of the player: henrik._slim_rr's shape."""
        return {
            row[0]: {"at": row[1], "tier": row[2], "rr": row[3], "change": row[4], "elo": row[5]}
            for row in self._read("SELECT id, at, tier, rr, change, elo FROM rr WHERE puuid = ?", (puuid,))
        }

    def version(self, puuid: str) -> tuple:
        """Changes whenever something of the player is added (the pages keep what they worked out until then)."""
        found = self._read(
            "SELECT (SELECT COUNT(*) FROM played WHERE puuid = ?), (SELECT COUNT(*) FROM thin WHERE puuid = ?), "
            "(SELECT COUNT(*) FROM rr WHERE puuid = ?)",
            (puuid, puuid, puuid),
        )
        return tuple(found[0]) if found else (0, 0, 0)

    def first(self, puuid: str) -> int:
        """When the player's oldest kept match started (unix seconds; 0 without one)."""
        found = self._read(
            "SELECT MIN(started) FROM (SELECT started FROM played WHERE puuid = ? AND started > 0 "
            "UNION ALL SELECT started FROM thin WHERE puuid = ? AND started > 0)",
            (puuid, puuid),
        )
        return int(found[0][0] or 0) if found else 0

    # -- how far each player has been read ---------------------------------------------------------------

    FIELDS = (
        "puuid",
        "region",
        "name",
        "tag",
        "wanted",
        "total",
        "scanned",
        "rr_scanned",
        "rr_done",
        "older",
        "stored",
        "finished",
        "again",
        "via",
        "problem",
    )

    def player(self, puuid: str) -> dict | None:
        found = self._read(f"SELECT {', '.join(self.FIELDS)} FROM players WHERE puuid = ?", (puuid,))
        return dict(zip(self.FIELDS, found[0], strict=True)) if found else None

    def want(self, puuid: str, region: str, name: str, tag: str) -> dict:
        """A visitor opened the player's lifetime: read their history (again, for the new matches only, when it
        was read through over AGAIN ago)."""
        now = self.clock()
        state = self.player(puuid)
        if state is None:
            # older: the next of HenrikDev's older pages (-2: not begun, -1: read through)
            state = dict.fromkeys(self.FIELDS, 0) | {"puuid": puuid, "total": -1, "older": -2, "via": "", "problem": ""}
        elif state["finished"] and now - state["finished"] > AGAIN:
            # Riot first again (an account may have been linked since), the newest pages only
            state |= {"scanned": 0, "total": -1, "rr_scanned": 0, "rr_done": 0, "finished": 0, "again": 1}
            state |= {"via": "", "older": -2, "problem": ""}
        state |= {"region": region, "name": name, "tag": tag, "wanted": now}
        self.save(state)
        return state

    def save(self, state: dict) -> None:
        self._write(
            f"INSERT OR REPLACE INTO players VALUES ({', '.join('?' * len(self.FIELDS))})",
            [tuple(state.get(key) for key in self.FIELDS)],
        )

    def busy(self) -> dict | None:
        """The player most lately wanted whose history is not read through, or None."""
        found = self._read(
            f"SELECT {', '.join(self.FIELDS)} FROM players WHERE finished = 0 ORDER BY wanted DESC LIMIT 1"
        )
        return dict(zip(self.FIELDS, found[0], strict=True)) if found else None

    def add_todo(self, puuid: str, matches: list[dict]) -> None:
        """Matches of the player's history to read (pdmatch.history's ``{"id", "at"}``)."""
        rows = [(puuid, str(item["id"]).lower(), int(item.get("at") or 0)) for item in matches]
        self._write("INSERT OR IGNORE INTO todo VALUES (?, ?, ?)", rows)

    def next_todo(self, puuid: str) -> str:
        """The newest match still to read ("" when none is)."""
        found = self._read("SELECT id FROM todo WHERE puuid = ? ORDER BY at DESC LIMIT 1", (puuid,))
        return str(found[0][0]) if found else ""

    def drop_todo(self, puuid: str, match_id: str) -> None:
        self._write("DELETE FROM todo WHERE puuid = ? AND id = ?", [(puuid, match_id)])

    def todo_count(self, puuid: str) -> int:
        found = self._read("SELECT COUNT(*) FROM todo WHERE puuid = ?", (puuid,))
        return int(found[0][0]) if found else 0


class Filler:
    """Reads the wanted players' histories in a thread of its own, one call at a time (see the module's words)."""

    def __init__(self, archive: Archive, riot, henrik, maps, seconds: float = FILL_SECONDS, clock=time.time):
        """``maps``: () -> valorant-api.com's maps by their address name (content.Content.map_index)."""
        self.archive, self.riot, self.henrik, self.maps = archive, riot, henrik, maps
        self.seconds, self.clock = seconds, clock
        self._started = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._henrik_at = 0.0
        self.resting_until = 0.0

    def want(self, region: str, puuid: str, name: str, tag: str) -> dict:
        state = self.archive.want(puuid.lower(), region, name, tag)
        self._wake.set()  # the thread (started with the app's first page) goes on at once
        return state

    def start(self) -> None:
        with self._lock:
            if self._started or not self.seconds:
                return
            self._started = True
        threading.Thread(target=self._run, name="archive", daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                did = self.step()
            except Exception:  # noqa: BLE001 - a failed call is tried again next time
                did = "failed"
            if did:
                time.sleep(self.seconds)
            else:
                self._wake.wait(300)
                self._wake.clear()

    def step(self) -> str:
        """One call for the player most lately wanted: what it was ("" when there is nothing to do)."""
        state = self.archive.busy()
        if state is None:
            return ""
        if self.clock() < self.resting_until:
            return "rest"
        if state["via"] != "henrik":
            try:
                return self._riot(state)
            except RiotError as error:
                if error.kind == "busy":
                    self.resting_until = self.clock() + REST
                    return "rest"
                # no linked account, or Riot does not give this player's history: HenrikDev's pages instead
                state |= {"via": "henrik", "problem": error.kind, "older": -2}
                self.archive.save(state)
                return "switch"
        return self._henrik(state)

    def _riot(self, state: dict) -> str:
        puuid, region = state["puuid"], state["region"]
        match_id = self.archive.next_todo(puuid)
        if match_id:
            try:
                details = self.riot.match_details(region, match_id)
            except RiotError as error:
                if error.kind != "down" or " 404" not in f" {error.detail}":
                    raise
                details = {}  # a match Riot no longer has: the others are still read
            raw = pdmatch.to_v4(details, region, self.maps(), puuid, state["name"], state["tag"])
            if raw:
                self.archive.keep([_slim_match(raw)], "riot")
            self.archive.drop_todo(puuid, match_id)
            return "match"
        if state["total"] < 0 or state["scanned"] < state["total"]:
            page = self.riot.history(region, puuid, state["scanned"])
            ids = [item["id"] for item in page["matches"]]
            known = self.archive.known(ids)
            new = [item for item in page["matches"] if item["id"] not in known]
            self.archive.add_todo(puuid, new)
            state["scanned"] += len(ids)
            state["total"] = max(page["total"], state["scanned"]) if ids else state["scanned"]
            if state["again"] and not new:
                state["total"] = state["scanned"]  # a look for new matches: the rest were read before
            self.archive.save(state)
            return "history"
        if not state["rr_done"]:
            page = self.riot.rr_updates(region, puuid, state["rr_scanned"])
            kept = self.archive.rr(puuid)
            self.archive.keep_rr(puuid, page["changes"])
            state["rr_scanned"] += page["count"]
            state["rr_done"] = int(
                page["count"] < HISTORY_PAGE or (bool(state["again"]) and all(key in kept for key in page["changes"]))
            )
            self.archive.save(state)
            return "rr"
        return self._stored(state)

    def _henrik(self, state: dict) -> str:
        if not self.henrik.key():
            return self._finish(state)
        if self.clock() - self._henrik_at < HENRIK_SECONDS or self.henrik.budget.room() < HENRIK_ROOM:
            return "wait"
        if state["older"] != -1:
            start = max(0, state["older"])
            self._henrik_at = self.clock()
            try:
                found = self.henrik.older(state["region"], state["name"], state["tag"], start)
            except HenrikError as error:
                if error.kind == "busy":
                    return "wait"
                found = []
            new = self.archive.keep(found, "henrik")
            ended = len(found) < PAGE or (bool(state["again"]) and not new)
            state["older"] = -1 if ended else start + PAGE
            self.archive.save(state)
            return "older"
        return self._stored(state)

    def _stored(self, state: dict) -> str:
        """HenrikDev's stored lines, for the matches Riot no longer gives (once in STORED_AGAIN); then done."""
        if self.henrik.key() and self.clock() - float(state["stored"] or 0) > STORED_AGAIN:
            if self.clock() - self._henrik_at < HENRIK_SECONDS or self.henrik.budget.room() < HENRIK_ROOM:
                return "wait"
            self._henrik_at = self.clock()
            try:
                lines = self.henrik.stored_all(state["region"], state["name"], state["tag"])
            except HenrikError as error:
                if error.kind == "busy":
                    return "wait"
                lines = []
            self.archive.keep_thin(state["puuid"], lines)
            state["stored"] = self.clock()
            self.archive.save(state)
            return "stored"
        return self._finish(state)

    def _finish(self, state: dict) -> str:
        state |= {"finished": self.clock(), "again": 0}
        self.archive.save(state)
        return "done"


def progress(state: dict | None, archive: Archive) -> dict:
    """What the page says of a player's reading: ``{"busy", "via", "read", "total", "left", "minutes"}``."""
    if not state:
        return {"busy": False, "via": "", "read": 0, "total": 0, "left": 0, "minutes": 0}
    busy = not state["finished"]
    left = archive.todo_count(state["puuid"]) if state["via"] != "henrik" else 0
    total = max(0, int(state["total"] or 0))
    if busy and state["via"] != "henrik" and total:
        left += max(0, total - int(state["scanned"] or 0))  # not looked at yet: about one call each
    return {
        "busy": busy,
        "via": state["via"] or "riot",
        "total": total,
        "left": left,
        "minutes": round(left * FILL_SECONDS / 60) if busy else 0,
    }
