"""The game's pictures, served from here: fetched once from media.valorant-api.com and kept in
instance/media/ (the pages load nothing from other addresses).

Only picture paths of the shape valorant-api.com uses are asked for (``agents/<uuid>/displayicon.png``,
``competitivetiers/<uuid>/<tier>/smallicon.png``), so the route cannot be used to fetch anything else.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from valtracker import content, net

PATH = re.compile(
    r"^[a-z]{3,24}(/[a-z]{3,24})?(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/[0-9]{1,3}){1,3}"
    r"(/abilities/[a-z0-9]{3,24})?/[a-z]{3,32}2?\.png$"
)
PNG = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 4 * 1024 * 1024
#: A picture that could not be fetched is not asked for again for this long.
MISS_SECONDS = 600


class Media:
    def __init__(self, folder: Path, fetch: net.Fetch = net.fetch):
        self.folder = Path(folder) / "media"
        self.fetch = fetch
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._missed: dict[str, float] = {}

    def file(self, path: str) -> Path | None:
        """The picture on this disk, fetched first if it is not here yet; None when there is none."""
        if not PATH.match(path):
            return None
        target = self.folder / path
        if target.is_file():
            return target
        with self._guard:
            if len(self._locks) > 2000:  # made-up paths must not grow these without end
                self._locks.clear()
                self._missed.clear()
            lock = self._locks.setdefault(path, threading.Lock())
        with lock:  # one fetch per picture, however many pages ask for it at once
            if target.is_file():
                return target
            if time.monotonic() - self._missed.get(path, -MISS_SECONDS) < MISS_SECONDS:
                return None
            status, body = self.fetch(content.MEDIA + path, {"Accept": "image/png"})
            if status != 200 or not body.startswith(PNG) or len(body) > MAX_BYTES:
                self._missed[path] = time.monotonic()
                return None
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(f".{os.getpid()}.tmp")
                temporary.write_bytes(body)
                temporary.replace(target)
            except OSError:
                return None
        return target
