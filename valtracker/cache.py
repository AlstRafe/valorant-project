"""A small in-memory store of recent answers, each kept for its own number of seconds."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class TTLCache:
    """At most ``size`` entries; the oldest used goes first when it is full."""

    def __init__(self, size: int = 512, clock=time.monotonic):
        self.size, self.clock = size, clock
        self._items: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return default
            if item[0] < self.clock():
                del self._items[key]
                return default
            self._items.move_to_end(key)
            return item[1]

    def drop(self, key) -> None:
        with self._lock:
            self._items.pop(key, None)

    def put(self, key, value, seconds: float) -> None:
        with self._lock:
            self._items[key] = (self.clock() + seconds, value)
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)


class Budget:
    """At most ``count`` calls in any ``seconds``: the HenrikDev key's allowance is shared by every visitor,
    so a burst of searches is told to wait instead of using it up for everyone."""

    def __init__(self, count: int, seconds: float, clock=time.monotonic):
        self.count, self.seconds, self.clock = count, seconds, clock
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            now = self.clock()
            self._calls = [at for at in self._calls if at > now - self.seconds]
            if len(self._calls) >= self.count:
                return False
            self._calls.append(now)
            return True

    def room(self) -> int:
        """How many calls can still be made now."""
        with self._lock:
            now = self.clock()
            self._calls = [at for at in self._calls if at > now - self.seconds]
            return self.count - len(self._calls)
