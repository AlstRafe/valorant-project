"""The owner's store diary and wish list (instance/diary.json, the owner's only).

- Each day the owner's store is read (shop.html), its four skins and the night market are noted under the store's
  day (the UTC date it runs on: from 9:00 in Japan), so the shop's history page shows what came before, and how
  many days in a row the owner looked (a check-in streak, as the game itself does not keep one).
- The skins the owner wants (marked on the skin library): the store marks them when they come up.

Each Riot account has its own store, so its own days: ``days`` are those of ``account`` (the first account the
diary was kept for: the file as it was before several could be linked), ``more`` (``{puuid: days}``) the other
accounts'. The wish list is the owner's, whichever account is shown.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from valtracker.settings import Settings

#: Days kept in the diary.
KEEP_DAYS = 180
#: Skins on the wish list at most.
WANTS_MAX = 200


def store_day(until: float, now: float) -> str:
    """The store's day (``"2026-11-06"``): the UTC date of its last second (``until``: when it changes)."""
    moment = until - 1 if until > now else now
    return datetime.fromtimestamp(moment, UTC).date().isoformat()


class Diary:
    def __init__(self, folder: Path):
        self.store = Settings(folder, "diary.json")
        self._lock = threading.Lock()

    def _first(self, saved: dict, account: str) -> bool:
        """Whether ``account``'s days are the file's ``days`` (the first account's, or nobody's yet)."""
        first = str(saved.get("account") or "")
        return not account or not first or account == first

    def _days(self, account: str = "") -> dict:
        saved = self.store.load()
        more = saved.get("more") if isinstance(saved.get("more"), dict) else {}
        days = saved.get("days") if self._first(saved, account) else more.get(account)
        return days if isinstance(days, dict) else {}

    def claim(self, account: str) -> None:
        """The days kept so far are ``account``'s (said before another account is linked or shown), unless
        they are someone's already."""
        with self._lock:
            if account and not self.store.load().get("account"):
                self.store.save(account=account)

    def note(self, day: str, daily: list[dict], night: list[dict] | None, account: str = "") -> None:
        """Keeps the day's store of ``account`` (a puuid): ``[{"uuid", "price"}]`` (and the night market's,
        ``"discount"`` too)."""
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
        """Newest first."""
        found = sorted(self._days(account).items(), reverse=True)
        return [(day, entry) for day, entry in found if isinstance(entry, dict)]

    def streak(self, today: str, account: str = "") -> int:
        """The days in a row, up to ``today``, the store was looked at."""
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
