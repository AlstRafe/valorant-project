"""The app with the tests' made-up answers of the outside APIs (tests/helpers.py), for looking at the pages and for
tests/ui/audit.js: the owner signed in to Riot (so /queues and the owner's store show), made-up matches on the servers
page, pictures a browser can show, one player with the longest name there can be, and a ``visitor=1`` cookie that
shows a page as a visitor sees it.

    python tests/ui/serve.py [port, default 5077]
"""

import math
import os
import random
import struct
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(TESTS), str(TESTS.parent)]
os.environ["VAL_HENRIK_KEY"] = ""

import helpers  # noqa: E402
from helpers import make_app  # noqa: E402

#: The made-up servers and how many matches each had in 14 days (Mumbai: too few to tell its busy hours).
SERVERS = {"Tokyo": 900, "Hong Kong": 420, "Singapore": 380, "Sydney": 260, "Mumbai": 30}


#: The longest Riot ID there can be, in wide letters (16 + 5): one made-up player has it, for long names in tables.
LONGEST = ("長い名前のプレイヤーです一二三四", "WWWWW")


def _fixture(name: str, original=helpers.fixture) -> bytes:
    data = original(name)
    if name == "henrik_matches.json":
        longest, tag = LONGEST
        data = data.replace(b'"name": "Other"', f'"name": "{longest}"'.encode())
        data = data.replace(b'"tag": "EUW"', f'"tag": "{tag}"'.encode())
    return data


def _png(size: int = 64) -> bytes:
    """A grey square that a browser can show (the tests' PNG is only its first bytes): a picture takes its room on
    the pages as the real ones do, so the layout check sees what a visitor sees."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\x88\x88\x88" * size for _ in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _match(match_id: str, started: float, cluster: str, queue: str, tier: int) -> dict:
    return {
        "metadata": {
            "match_id": match_id,
            "region": "ap",
            "cluster": cluster,
            "queue": {"id": queue},
            "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
            "game_length_in_ms": 2_100_000,
        },
        "players": [{"puuid": "", "tier": {"id": tier}}],
    }


def seed(activity, now: float) -> None:
    """Matches over the last 14 days, most in the evening (Japan time), and waits between pairs of them."""
    pick = random.Random(4)
    listed = []
    for cluster, size in SERVERS.items():
        for number in range(size):
            while True:  # evenings busy, mornings quiet
                hour = pick.random() * 24
                if pick.random() < 0.15 + 0.85 * (0.5 + 0.5 * math.cos((hour - 22) / 12 * math.pi)) ** 2:
                    break
            started = now // 86400 * 86400 - pick.randint(0, 13) * 86400 + (hour - 9) * 3600
            started = started - 86400 if started > now else started
            listed.append(_match(f"{cluster}-{number}", started, cluster, "competitive", pick.randint(8, 24)))
    activity.seen(listed)
    for number in range(60):
        start = now - pick.randint(1, 13) * 86400
        tier = pick.choice((10, 12, 17, 18, 22, 25))
        queue = "competitive" if number % 3 else "unrated"
        wait = int(pick.gauss(150 + tier * 6, 40))
        first = _match(f"pair-{number}-a", start, "Tokyo", queue, tier)
        second = _match(f"pair-{number}-b", start + 2100 + wait, "Tokyo", queue, tier)
        activity.seen([first, second], f"player-{number}")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5077
    helpers.PNG = _png()
    helpers.fixture = _fixture
    app, _, _ = make_app()
    app.config["VAL_RIOT"].sign_in("ssid-1")
    seed(app.config["VAL_ACTIVITY"], time.time())
    # a few days of the owner's store, and a skin wanted that is in today's (diary.py)
    diary = app.config["VAL_DIARY"]
    for back in range(1, 4):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - back * 86400))
        diary.note(day, [{"uuid": "ac000000-0000-4000-8000-000000000001", "price": 3550}], None)
    diary.want("ac000000-0000-4000-8000-000000000001", True)
    inner = app.wsgi_app

    def wsgi(environ, start):
        if "visitor=1" in environ.get("HTTP_COOKIE", ""):
            environ["homepage.visitor"] = True
        # /live?phase=menus, queue (the menus, matchmaking), pregame or ingame: where the owner's made-up game is
        # (helpers.FakeRiot); none: the game is closed
        if environ.get("PATH_INFO") == "/live":
            phase = environ.get("QUERY_STRING", "").removeprefix("phase=")
            app.riot.phase = "menus" if phase == "queue" else phase if phase in ("menus", "pregame", "ingame") else ""
            party = app.riot.pd["party"]
            party["State"] = "MATCHMAKING" if phase == "queue" else "DEFAULT"
            party["QueueEntryTime"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 95))
                if phase == "queue"
                else "0001-01-01T00:00:00Z"
            )
            app.config["VAL_RIOT"].cache.put("live", None, 0)
        return inner(environ, start)

    app.wsgi_app = wsgi
    app.run("127.0.0.1", port, threaded=True)


if __name__ == "__main__":
    main()
