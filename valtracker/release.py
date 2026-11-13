"""Which build of the PC app is the newest.

The site says it (/app/latest), from the clone it runs from: the last commit that changed what the app is built
from. (When this was written the repository was private and GitHub answered 404 for its release to anyone not
signed in; it is public since 2026-11-13, and the site is still the one asked: one small answer, no API limit.) desktop.yml stamps each build with the same answer (``python -m valtracker.release <file>``
writes valtracker/BUILD), and the app compares the two (desktop.look_for_updates).
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from valtracker.paths import ROOT

#: What the app is built from: desktop.yml's ``paths``. A commit that changes none of these builds no new app.
PATHS = ("valtracker", "desktop", "requirements*.txt", ".github/workflows/desktop.yml")
#: Where the newest build is downloaded (the owner is signed in to GitHub in their browser).
PAGE = "https://github.com/AlstRafe/valorant-project/releases/tag/desktop"
#: A commit is told as a newer build only this long after it was made: desktop.yml has built and released it by
#: then (it takes some ten minutes).
BUILT_AFTER = 20 * 60
#: The site reads its clone again at most this often.
KEPT = 300.0
_HASH = re.compile(r"[0-9a-f]{40}")
_kept: dict = {"at": 0.0, "found": None}
_lock = threading.Lock()


def source(folder: Path = ROOT) -> dict | None:
    """The last commit that changed the app in the clone at ``folder``: {"commit", "time": when it was made
    (seconds since 1970)}. None without a clone or without git (the built app, a copy of the files)."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments
            ["git", "-C", str(folder), "log", "-1", "--format=%H %ct", "--", *PATHS],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(done.stdout)


def parse(text: str) -> dict | None:
    """ "<commit> <time>" (git's answer, and valtracker/BUILD) as {"commit", "time"}, None when it is not that."""
    parts = text.split()
    if len(parts) != 2 or not _HASH.fullmatch(parts[0]) or not parts[1].isdigit():
        return None
    return {"commit": parts[0], "time": int(parts[1])}


def latest(folder: Path = ROOT, now: float | None = None) -> dict | None:
    """source(), read again at most every KEPT seconds: what the site answers at /app/latest."""
    now = time.monotonic() if now is None else now
    with _lock:
        if _kept["found"] is None or now - _kept["at"] >= KEPT:
            _kept["found"], _kept["at"] = source(folder), now
        return _kept["found"]


def day(when: int) -> str:
    """A build's time as this PC's clock says it: 2026-11-13 09:41."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(when))


def read_build(file: Path) -> dict | None:
    """The built app's stamp (valtracker/BUILD): {"commit", "time", "date"}. None when run from the source."""
    try:
        found = parse(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found | {"date": day(found["time"])} if found else None


def newer(build: dict | None, told: object, now: float) -> dict | None:
    """What the site ``told`` (/app/latest) when it is a newer build than this one: {"commit", "date", "url":
    PAGE, "ready": desktop.yml has had the time to release it}. None when this is the newest, when this app was not
    built, or when the answer is not one. The link is always PAGE: nothing the answer says is opened."""
    if not build or not isinstance(told, dict):
        return None
    commit, when = told.get("commit"), told.get("time")
    if (
        not isinstance(commit, str)
        or not _HASH.fullmatch(commit)
        or isinstance(when, bool)
        or not isinstance(when, int)
    ):
        return None
    if commit == build["commit"] or when <= build["time"]:
        return None
    return {"commit": commit, "date": day(when), "url": PAGE, "ready": now - when >= BUILT_AFTER}


if __name__ == "__main__":
    # desktop.yml: the stamp of the build being made
    found = source()
    if not found or len(sys.argv) != 2:
        sys.exit("No commit found for the app's files (a clone with its history is needed).")
    Path(sys.argv[1]).write_text(f"{found['commit']} {found['time']}", encoding="ascii")
    print(f"{found['commit']} {found['time']} ({day(found['time'])})")
