"""サイトが読んだプレイヤーの全試合をディスクに残す。プレイヤーページで、
HenrikDev が出す最新 RECENT 試合だけでなく全部を数えられる（「通算」）。

試合の出どころ:

- HenrikDev がサイトに渡す試合（プレイヤーの最新、試合ページ）: 全プレイヤーの成績入りでまるごと
- ゲーム自身の記録（Riot の PD。持ち主の連携アカウントで読む）。訪問者がプレイヤーの通算を開いたら: 試合履歴、
  次にまだ無い試合を 1 つずつ、裏で FILL_SECONDS ごとに 1 回（Filler）、コンペティティブの各試合の RR の動き。
  PD の名前は消す（pdmatch.py）
- Riot に聞けないとき（連携アカウントが無い、または断られた）だけ: HenrikDev のプレイヤーの古い試合のページ
- Riot がもう出さないもの: HenrikDev の保存済み試合（v1）。試合ごとにプレイヤーの成績 1 行、ラウンド無し（KAST、
  ファーストキル、武器、場所が無い:「薄い」）

instance/archive.sqlite に残す: 各試合 1 回（圧縮、henrik.slim_match の形）、出た人、薄い成績、RR の変化、まだ読む試合、
各プレイヤーの履歴をどこまで読んだか。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path

from valtracker import pdmatch
from valtracker.henrik import PAGE, HenrikError, slim_match
from valtracker.riot import HISTORY_PAGE, RiotError

#: 裏から Riot を呼ぶ間隔の秒数（0: 止める）: 持ち主自身のページ（ライブ、ストア）にも Riot の制限にも余裕を残す遅さ。
FILL_SECONDS = 2.5
#: HenrikDev を呼ぶ間隔の秒数（呼び出し回数は訪問者全員で分け合う）と、裏が聞く前に訪問者用に残す回数。
HENRIK_SECONDS = 15
HENRIK_ROOM = 12
#: Riot が「多すぎる」（429）と言ったあと、次に聞くまでの秒数。
REST = 120
#: 最後まで読んだプレイヤーは、この時間が過ぎてから通算を開かれたら読み直す（新しい試合だけ）。
AGAIN = 20 * 60
#: 保存済み試合（HenrikDev v1）はこの時間が過ぎたら読み直す。
STORED_AGAIN = 6 * 3600
#: 試合の出どころ: HenrikDev は全プレイヤーの名前を出すので、1 人しか名前の無い Riot のものより優先して残す。
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
        return int(datetime.fromisoformat(raw).timestamp())
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

    # -- 残すもの ---------------------------------------------------------------------------------------

    def keep(self, matches: list, source: str = "henrik") -> int:
        """終わった試合（henrik.slim_match の形）をアーカイブに入れる。新しかった数を返す。
        HenrikDev から残した試合は Riot のもの（プレイヤー以外の名前が無い）で置き換えない。"""
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
        """プレイヤーの保存済みの成績（henrik._slim_stored の形）。"""
        rows = [
            (puuid, str(line["id"]).lower(), int(line.get("at") or 0), str(line.get("queue") or ""), json.dumps(line))
            for line in lines or []
            if isinstance(line, dict) and line.get("id")
        ]
        self._write("INSERT OR REPLACE INTO thin VALUES (?, ?, ?, ?, ?)", rows)

    def keep_rr(self, puuid: str, changes: dict) -> None:
        """コンペティティブの各試合の RR の動き（henrik._slim_rr の形）。"""
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

    # -- ページが読むもの ------------------------------------------------------------------------------

    def known(self, ids: list[str]) -> set[str]:
        """このうち、まるごと残している試合。"""
        ids = [str(item).lower() for item in ids]
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {row[0] for row in self._read(f"SELECT id FROM matches WHERE id IN ({marks})", tuple(ids))}

    def matches(self, puuid: str, queue: str = ""):
        """プレイヤーのまるごとの試合（新しい順、1 つのキューか全部）: henrik.slim_match の形。"""
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
        """まるごと残していない試合の、プレイヤーの保存済みの成績（新しい順）。"""
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
        """残しているプレイヤーの RR の変化全部: henrik._slim_rr の形。"""
        return {
            row[0]: {"at": row[1], "tier": row[2], "rr": row[3], "change": row[4], "elo": row[5]}
            for row in self._read("SELECT id, at, tier, rr, change, elo FROM rr WHERE puuid = ?", (puuid,))
        }

    def version(self, puuid: str) -> tuple:
        """プレイヤーのものが何か増えるたびに変わる値（ページはそれまで計算したものを持ち続ける）。"""
        found = self._read(
            "SELECT (SELECT COUNT(*) FROM played WHERE puuid = ?), (SELECT COUNT(*) FROM thin WHERE puuid = ?), "
            "(SELECT COUNT(*) FROM rr WHERE puuid = ?)",
            (puuid, puuid, puuid),
        )
        return tuple(found[0]) if found else (0, 0, 0)

    def first(self, puuid: str) -> int:
        """残しているプレイヤーの一番古い試合の開始時刻（unix 秒。無ければ 0）。"""
        found = self._read(
            "SELECT MIN(started) FROM (SELECT started FROM played WHERE puuid = ? AND started > 0 "
            "UNION ALL SELECT started FROM thin WHERE puuid = ? AND started > 0)",
            (puuid, puuid),
        )
        return int(found[0][0] or 0) if found else 0

    # -- 各プレイヤーをどこまで読んだか -----------------------------------------------------------------

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
        """訪問者がプレイヤーの通算を開いた: 履歴を読む（最後まで読んだのが AGAIN より前なら、新しい試合だけ読み直す）。"""
        now = self.clock()
        state = self.player(puuid)
        if state is None:
            # older: 次に読む HenrikDev の古いページ（-2: 始めていない、-1: 最後まで読んだ）
            state = dict.fromkeys(self.FIELDS, 0) | {"puuid": puuid, "total": -1, "older": -2, "via": "", "problem": ""}
        elif state["finished"] and now - state["finished"] > AGAIN:
            # また Riot から（その後アカウントを連携したかもしれない）。新しいページだけ
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
        """一番最近求められた、履歴を最後まで読んでいないプレイヤー。無ければ None。"""
        found = self._read(
            f"SELECT {', '.join(self.FIELDS)} FROM players WHERE finished = 0 ORDER BY wanted DESC LIMIT 1"
        )
        return dict(zip(self.FIELDS, found[0], strict=True)) if found else None

    def add_todo(self, puuid: str, matches: list[dict]) -> None:
        """これから読むプレイヤーの履歴の試合（pdmatch.history の ``{"id", "at"}``）。"""
        rows = [(puuid, str(item["id"]).lower(), int(item.get("at") or 0)) for item in matches]
        self._write("INSERT OR IGNORE INTO todo VALUES (?, ?, ?)", rows)

    def next_todo(self, puuid: str) -> str:
        """まだ読む試合のうち一番新しいもの（無ければ ""）。"""
        found = self._read("SELECT id FROM todo WHERE puuid = ? ORDER BY at DESC LIMIT 1", (puuid,))
        return str(found[0][0]) if found else ""

    def drop_todo(self, puuid: str, match_id: str) -> None:
        self._write("DELETE FROM todo WHERE puuid = ? AND id = ?", [(puuid, match_id)])

    def todo_count(self, puuid: str) -> int:
        found = self._read("SELECT COUNT(*) FROM todo WHERE puuid = ?", (puuid,))
        return int(found[0][0]) if found else 0


class Filler:
    """求められたプレイヤーの履歴を専用のスレッドで 1 回ずつ読む（モジュールの説明を参照）。"""

    def __init__(self, archive: Archive, riot, henrik, maps, seconds: float = FILL_SECONDS, clock=time.time):
        """``maps``: () -> valorant-api.com のマップをアドレス名で（content.Content.map_index）。"""
        self.archive, self.riot, self.henrik, self.maps = archive, riot, henrik, maps
        self.seconds, self.clock = seconds, clock
        self._started = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._henrik_at = 0.0
        self.resting_until = 0.0

    def want(self, region: str, puuid: str, name: str, tag: str) -> dict:
        state = self.archive.want(puuid.lower(), region, name, tag)
        self._wake.set()  # スレッド（アプリの最初のページで始まる）はすぐ続ける
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
            except Exception:  # 失敗した呼び出しは次の回にまた試す
                did = "failed"
            if did:
                time.sleep(self.seconds)
            else:
                self._wake.wait(300)
                self._wake.clear()

    def step(self) -> str:
        """一番最近求められたプレイヤーのための 1 回の呼び出し: 何をしたか（することが無ければ ""）。"""
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
                # 連携アカウントが無い、または Riot がこのプレイヤーの履歴を出さない: 代わりに HenrikDev のページ
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
                details = {}  # Riot がもう持っていない試合: ほかは引き続き読む
            raw = pdmatch.to_v4(details, region, self.maps(), puuid, state["name"], state["tag"])
            if raw:
                self.archive.keep([slim_match(raw)], "riot")
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
                state["total"] = state["scanned"]  # 新しい試合を見に来ただけ: 残りは前に読んだ
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
        """Riot がもう出さない試合のための HenrikDev の保存済みの成績（STORED_AGAIN に 1 回）。それで終わり。"""
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
    """プレイヤーの読み込みについてページに出すもの: ``{"busy", "via", "left", "minutes"}``。"""
    if not state:
        return {"busy": False, "via": "", "left": 0, "minutes": 0}
    busy = not state["finished"]
    left = archive.todo_count(state["puuid"]) if state["via"] != "henrik" else 0
    total = max(0, int(state["total"] or 0))
    if busy and state["via"] != "henrik" and total:
        left += max(0, total - int(state["scanned"] or 0))  # まだ見ていない: 1 つにつき約 1 回の呼び出し
    return {
        "busy": busy,
        "via": state["via"] or "riot",
        "left": left,
        "minutes": round(left * FILL_SECONDS / 60) if busy else 0,
    }
