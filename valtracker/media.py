"""The game's pictures, served from here: fetched once from media.valorant-api.com and kept in
instance/media/ (the pages load nothing from other addresses).

Only picture paths of the shape valorant-api.com uses are asked for (``agents/<uuid>/displayicon.png``,
``competitivetiers/<uuid>/<tier>/smallicon.png``), so the route cannot be used to fetch anything else.

The news' and esports' pictures (Riot's news site, the esports teams' logos) come the same way from the few
addresses in OUTSIDE, each with the one shape of path it uses (``outside``, served at /pic/).
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
#: /pic/<key>/<path>: the address it is fetched from (with what is added to ask for a smaller picture), and the
#: only shape of path asked for there.
OUTSIDE = {
    "rgpub": (
        "https://cmsassets.rgpub.io/",
        "?w=960&fm=jpg&q=80",
        re.compile(
            r"^sanity/images/[a-z0-9]{4,16}/[a-z0-9_-]{2,40}/[0-9a-f]{20,64}-[0-9]{1,5}x[0-9]{1,5}\.(jpg|png|webp)$"
        ),
    ),
    "contentstack": (
        "https://images.contentstack.io/",
        "?width=960&format=jpg&quality=80",
        re.compile(r"^v3/assets/blt[0-9a-f]{8,24}/blt[0-9a-f]{8,24}/[A-Za-z0-9._-]{1,120}\.(jpg|jpeg|png|webp)$"),
    ),
    "lolesports": (
        "https://static.lolesports.com/",
        "",
        re.compile(r"^(teams|leagues)/[A-Za-z0-9._-]{1,120}\.(png|jpg|jpeg|webp)$"),
    ),
}
#: The kinds of picture served, by their first bytes.
KINDS = ((PNG, "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"RIFF", "image/webp"))


def kind(body: bytes) -> str | None:
    """A picture's media type from its first bytes (None when it is not one we serve)."""
    for start, name in KINDS:
        if body.startswith(start) and (name != "image/webp" or body[8:12] == b"WEBP"):
            return name
    return None


def outside_path(url) -> str | None:
    """An outside picture's address -> its path under /pic/ (None when it is not one of OUTSIDE's)."""
    url = str(url or "").split("?", 1)[0]
    for key, (base, _, shape) in OUTSIDE.items():
        if url.startswith(base) and shape.match(url[len(base) :]):
            return f"{key}/{url[len(base) :]}"
    return None


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
        return self._kept(path, self.folder / path, content.MEDIA + path, png_only=True)

    def outside(self, path: str) -> Path | None:
        """A news or esports picture (``<key>/<path>`` of OUTSIDE), fetched first if it is not here yet."""
        key, _, rest = path.partition("/")
        if key not in OUTSIDE or ".." in rest or not OUTSIDE[key][2].match(rest):
            return None
        base, smaller, _ = OUTSIDE[key]
        return self._kept(path, self.folder / "outside" / path, base + rest + smaller, png_only=False)

    def _kept(self, path: str, target: Path, address: str, png_only: bool) -> Path | None:
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
            status, body = self.fetch(address, {"Accept": "image/png" if png_only else "image/*"})
            found = kind(body) if status == 200 else None
            if not found or (png_only and found != "image/png") or len(body) > MAX_BYTES:
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
