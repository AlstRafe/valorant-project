"""スキン図鑑の評価: スキンのページを開いた人なら誰でも 1〜5 の星を付けられる（アカウント不要）。

ブラウザーは自分の cookie（``val_voter``）のランダムな値で見分け、ここには鍵付きハッシュだけを置く。
評価はスキンごとに 1 つで、付け直すと置き換わる。プレイヤーの名前・アドレス・ID は持たない。
スクリプトで埋められないよう、1 ブラウザー 1 日 VOTER_DAY スキンまで、
サイト全体で 10 分に SITE_TEN_MINUTES 件まで（超えたらページで「あとで」と出す）。"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import time
from pathlib import Path

VOTER_DAY = 200
SITE_TEN_MINUTES = 300
#: この件数から平均を出す（それより少ないときは件数だけ）。
SHOWN_FROM = 3


class Ratings:
    def __init__(self, folder: Path, secret: bytes, clock=time.time):
        self.path = Path(folder) / "ratings.sqlite"
        self.secret, self.clock = secret, clock
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS ratings (skin TEXT NOT NULL, voter TEXT NOT NULL, stars INTEGER NOT NULL,"
                " at INTEGER NOT NULL, PRIMARY KEY (skin, voter))"
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS ratings_at ON ratings (at)")
        return self._db

    def _voter(self, cookie: str) -> str:
        return hmac.new(self.secret, cookie.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def score(self, skin: str, cookie: str = "") -> dict:
        """``{"count", "average"（SHOWN_FROM 未満は None）, "mine"（このブラウザーの星、無ければ 0）}``。"""
        found = {"count": 0, "average": None, "mine": 0}
        try:
            with self._lock:
                db = self._connect()
                count, total = db.execute(
                    "SELECT COUNT(*), COALESCE(SUM(stars), 0) FROM ratings WHERE skin = ?", (skin,)
                ).fetchone()
                mine = (
                    db.execute(
                        "SELECT stars FROM ratings WHERE skin = ? AND voter = ?", (skin, self._voter(cookie))
                    ).fetchone()
                    if cookie
                    else None
                )
        except sqlite3.Error:
            return found
        found["count"] = int(count)
        if count >= SHOWN_FROM:
            found["average"] = round(total / count, 1)
        found["mine"] = int(mine[0]) if mine else 0
        return found

    def top(self, limit: int = 12) -> list[tuple[str, float, int]]:
        """評価の高いスキン（SHOWN_FROM 件以上）: ``[(skin, average, count)]``。"""
        try:
            with self._lock:
                rows = (
                    self._connect()
                    .execute(
                        "SELECT skin, AVG(stars) AS mean, COUNT(*) AS n FROM ratings GROUP BY skin HAVING n >= ?"
                        " ORDER BY mean DESC, n DESC LIMIT ?",
                        (SHOWN_FROM, limit),
                    )
                    .fetchall()
                )
        except sqlite3.Error:
            return []
        return [(skin, round(mean, 1), int(count)) for skin, mean, count in rows]

    def rate(self, skin: str, cookie: str, stars: int) -> bool:
        """このブラウザーの ``skin`` の評価を残す（False: 今は多すぎて残さなかった）。"""
        if not cookie or not 1 <= stars <= 5:
            return False
        now = int(self.clock())
        voter = self._voter(cookie)
        try:
            with self._lock:
                db = self._connect()
                (site,) = db.execute("SELECT COUNT(*) FROM ratings WHERE at > ?", (now - 600,)).fetchone()
                (mine,) = db.execute(
                    "SELECT COUNT(*) FROM ratings WHERE voter = ? AND at > ?", (voter, now - 86400)
                ).fetchone()
                if site >= SITE_TEN_MINUTES or mine >= VOTER_DAY:
                    return False
                with db:
                    db.execute(
                        "INSERT INTO ratings (skin, voter, stars, at) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT (skin, voter) DO UPDATE SET stars = excluded.stars, at = excluded.at",
                        (skin, voter, stars, now),
                    )
        except sqlite3.Error:
            return False
        return True

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
