"""持ち主のストア日記とほしい物リスト（instance/diary.json、持ち主専用）。

- 持ち主のストアを読むたび（shop.html）、その日の 4 スキンとナイトマーケットをストアの日付（UTC の日付。
  日本時間 9:00 から）で残す。ストア履歴のページで過去の分と、何日続けて見たか（ゲームには無いログイン連続日数）を出す。
- 持ち主のほしいスキン（図鑑で印を付ける）: ストアに出たら印を付ける。

ストアは Riot アカウントごとなので日付もアカウントごと: ``days`` は ``account``（日記を付け始めた最初のアカウント。
複数連携できる前のファイルの形）、``more``（``{puuid: days}``）はほかのアカウントの分。
ほしい物リストはどのアカウントを表示していても持ち主のもの。"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from valtracker.settings import Settings

#: 日記に残す日数。
KEEP_DAYS = 180
#: ほしい物リストの最大数。
WANTS_MAX = 200


def store_day(until: float, now: float) -> str:
    """ストアの日付（``"2026-11-06"``）: 最後の 1 秒の UTC の日付（``until``: 切り替わる時刻）。"""
    moment = until - 1 if until > now else now
    return datetime.fromtimestamp(moment, UTC).date().isoformat()


class Diary:
    def __init__(self, folder: Path):
        self.store = Settings(folder, "diary.json")
        self._lock = threading.Lock()

    def _first(self, saved: dict, account: str) -> bool:
        """``account`` の日付がファイルの ``days``（最初のアカウントのもの、またはまだ誰のものでもない）か。"""
        first = str(saved.get("account") or "")
        return not account or not first or account == first

    def _days(self, account: str = "") -> dict:
        saved = self.store.load()
        more = saved.get("more") if isinstance(saved.get("more"), dict) else {}
        days = saved.get("days") if self._first(saved, account) else more.get(account)
        return days if isinstance(days, dict) else {}

    def claim(self, account: str) -> None:
        """ここまでの日付を ``account`` のものにする（別のアカウントを連携・表示する前に呼ぶ）。
        すでに誰かのものなら何もしない。"""
        with self._lock:
            if account and not self.store.load().get("account"):
                self.store.save(account=account)

    def note(self, day: str, daily: list[dict], night: list[dict] | None, account: str = "") -> None:
        """``account``（puuid）のその日のストアを残す: ``[{"uuid", "price"}]``（ナイトマーケットは ``"discount"`` も）。"""
        if not daily:
            return
        entry = {"daily": daily, **({"night": night} if night else {})}
        with self._lock:
            days = self._days(account)
            if days.get(day) == entry:
                return
            days[day] = entry
            kept = dict(sorted(days.items(), reverse=True)[:KEEP_DAYS])
            saved = self.store.load()
            if self._first(saved, account):
                self.store.save(days=kept, account=account or saved.get("account"))
            else:
                more = saved.get("more") if isinstance(saved.get("more"), dict) else {}
                self.store.save(more={**more, account: kept})

    def days(self, account: str = "") -> list[tuple[str, dict]]:
        """新しい順。"""
        found = sorted(self._days(account).items(), reverse=True)
        return [(day, entry) for day, entry in found if isinstance(entry, dict)]

    def streak(self, today: str, account: str = "") -> int:
        """``today`` までストアを何日続けて見たか。"""
        noted = set(self._days(account))
        try:
            day = date.fromisoformat(today)
        except ValueError:
            return 0
        count = 0
        while day.isoformat() in noted:
            count += 1
            day -= timedelta(days=1)
        return count

    def wants(self) -> list[str]:
        found = self.store.load().get("wants")
        return [str(item) for item in found] if isinstance(found, list) else []

    def want(self, skin: str, on: bool) -> None:
        with self._lock:
            wants = [item for item in self.wants() if item != skin]
            if on:
                wants.insert(0, skin)
            self.store.save(wants=wants[:WANTS_MAX] or None)
