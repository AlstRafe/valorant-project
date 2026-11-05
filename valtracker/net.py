"""Asking the outside APIs (HenrikDev, valorant-api.com, the owner's Riot account): small functions the rest of the
app is given, so the tests can hand in their own answers instead of going to the network."""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

#: (url, headers) -> (HTTP status, body). Status 0: no answer (no network, a timeout, a refused TLS).
Fetch = Callable[[str, dict], tuple[int, bytes]]
#: (method, url, headers, body) -> (HTTP status, the answer's headers, body), redirects not followed (riot.py).
Send = Callable[[str, str, dict, bytes | None], tuple[int, list[tuple[str, str]], bytes]]

USER_AGENT = "VAL-Tracker/0.1 (+https://github.com/AlstRafe/valorant-project)"
TIMEOUT = 10.0
#: An answer bigger than this is not one of the answers we ask for (HenrikDev's 10 latest matches, with every
#: round and kill in them, are about 5 MB).
MAX_BYTES = 24 * 1024 * 1024

_TLS = ssl.create_default_context()
#: Riot's sign-in refuses Python's usual TLS greeting (2023); the cipher order the game's own tools use gets through.
_RIOT_TLS = ssl.create_default_context()
_RIOT_TLS.minimum_version = ssl.TLSVersion.TLSv1_2
try:
    _RIOT_TLS.set_ciphers(
        "ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:"
        "ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
        "AES128-GCM-SHA256:AES256-GCM-SHA384:AES128-SHA:AES256-SHA"
    )
except ssl.SSLError:
    pass  # this OpenSSL has none of them: its own order


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


class _Stay(urllib.request.HTTPRedirectHandler):
    """A redirect is the answer (Riot's sign-in puts the tokens in the address it sends the browser to)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_Stay, urllib.request.HTTPSHandler(context=_RIOT_TLS))


def send(method: str, url: str, headers: dict | None = None, body: bytes | None = None):
    """Like ``fetch`` for the owner's Riot account: any method, a body, the answer's headers (Set-Cookie,
    Location), and a redirect handed back instead of followed."""
    request = urllib.request.Request(
        url, data=body, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as answer:
            return answer.status, list(answer.headers.items()), answer.read(MAX_BYTES + 1)[:MAX_BYTES]
    except urllib.error.HTTPError as error:
        try:
            content = error.read(64 * 1024)
        except OSError:
            content = b""
        return error.code, list(error.headers.items()) if error.headers else [], content
    except (OSError, ValueError):
        return 0, [], b""
