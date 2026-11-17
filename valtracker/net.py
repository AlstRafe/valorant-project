"""外部 API（HenrikDev、valorant-api.com、持ち主の Riot アカウント）への問い合わせ。アプリには小さな関数として渡すので、
テストはネットに出ずに自分の答えを渡せる。"""

from __future__ import annotations

import http.client
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

#: (url, headers) -> (HTTP ステータス, 本文)。ステータス 0: 答えが無い（ネット無し、タイムアウト、TLS 拒否）。
Fetch = Callable[[str, dict], tuple[int, bytes]]
#: (method, url, headers, body) -> (HTTP ステータス, 答えのヘッダー, 本文)。リダイレクトは追わない（riot.py）。
Send = Callable[[str, str, dict, bytes | None], tuple[int, list[tuple[str, str]], bytes]]

USER_AGENT = "VAL-Tracker/0.1 (+https://github.com/AlstRafe/valorant-project)"
TIMEOUT = 10.0
#: これより大きい答えはこちらが頼んだものではない（HenrikDev の最新 10 試合を全ラウンド・全キル込みで約 5 MB）。
MAX_BYTES = 24 * 1024 * 1024

_TLS = ssl.create_default_context()
#: Riot のサインインは Python の普通の TLS の挨拶を拒む（2023 年）。ゲーム用ツールと同じ暗号の順なら通る。
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
    pass  # この OpenSSL にはどれも無い: 既定の順


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
    except (OSError, ValueError, http.client.HTTPException):
        # HTTPException: 途中で切れた答え（IncompleteRead）や壊れた答え（BadStatusLine）。OSError ではない
        return 0, b""


class _Stay(urllib.request.HTTPRedirectHandler):
    """リダイレクトをそのまま答えにする（Riot のサインインはブラウザーを送る先のアドレスにトークンを入れる）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_Stay, urllib.request.HTTPSHandler(context=_RIOT_TLS))


def send(method: str, url: str, headers: dict | None = None, body: bytes | None = None):
    """持ち主の Riot アカウント用の ``fetch``: 任意のメソッド、本文、答えのヘッダー（Set-Cookie、Location）。
    リダイレクトは追わずに返す。"""
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
    except (OSError, ValueError, http.client.HTTPException):
        return 0, [], b""
