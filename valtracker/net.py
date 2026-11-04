"""Asking the outside APIs (HenrikDev, valorant-api.com): one small function the rest of the app is given,
so the tests can hand in their own answers instead of going to the network."""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

#: (url, headers) -> (HTTP status, body). Status 0: no answer (no network, a timeout, a refused TLS).
Fetch = Callable[[str, dict], tuple[int, bytes]]

USER_AGENT = "VAL-Tracker/0.1 (+https://github.com/AlstRafe/valorant-project)"
TIMEOUT = 10.0
#: An answer bigger than this is not one of the answers we ask for.
MAX_BYTES = 8 * 1024 * 1024

_TLS = ssl.create_default_context()


def fetch(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=_TLS) as answer:
            return answer.status, answer.read(MAX_BYTES + 1)[:MAX_BYTES]
    except urllib.error.HTTPError as error:
        try:
            body = error.read(64 * 1024)
        except OSError:
            body = b""
        return error.code, body
    except (OSError, ValueError):
        return 0, b""
