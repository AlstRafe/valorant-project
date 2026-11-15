"""ブラウザーがページで何をしてよいか、どのリクエストなら変更を許すか。

- すべての答えに Content-Security-Policy を付け、このサイト自身のファイルと、インラインのスクリプト 1 つ（テーマ、
  theme.py）だけをハッシュで許す。ゲームの絵は /media/ 経由で、外からは読まない。
  例外はスキン図鑑の動画（1 本数 MB で多すぎて置けない）: Riot 自身の CDN（content.VIDEOS）から、
  訪問者が再生を押したときだけ読む（``preload="none"``）。
- POST（設定のフォーム）にはセッションの CSRF トークン（こちらのページだけが知るランダムな値）が必要。
  ブラウザーがクロスサイトと印を付けたリクエストはトークンを見る前に断る。
- 持ち主専用ページ（設定）は訪問者には断る: Home Page のサーバーは、
  公開名から来て持ち主としてサインインしていないリクエストに印を付ける（``homepage.visitor``）。単独で動かすときは、
  この PC（127.0.0.1）でしか待ち受けないので、開いた人が持ち主。"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session
from flask.sessions import SecureCookieSessionInterface

from valtracker.content import VIDEOS
from valtracker.theme import THEME_HASH

VISITOR_KEY = "homepage.visitor"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' {THEME_HASH}",
        "style-src 'self'",
        "img-src 'self' data:",
        "media-src 'self' " + " ".join(address.rstrip("/") for address in VIDEOS),
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)
HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)


def is_owner() -> bool:
    return not request.environ.get(VISITOR_KEY, False)


def owner_only() -> None:
    """訪問者には「そのページは無い」と答える（訪問者にとっては存在しない）。"""
    if not is_owner():
        abort(404)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def secret_key(path: Path) -> bytes:
    """再起動をまたいで残す（開いたままの設定ページが送るトークンが有効なままになる）。"""
    try:
        data = path.read_bytes()
        if len(data) >= 32:
            return data
    except OSError:
        pass
    data = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except FileExistsError:
        try:
            existing = path.read_bytes()
            if len(existing) >= 32:
                return existing
        except OSError:
            pass
    except OSError:
        pass  # 読み取り専用のフォルダー: 鍵はこのプロセスの間だけ
    return data


class _SessionInterface(SecureCookieSessionInterface):
    """ページが https で来たときはセッション cookie を Secure にする（Home Page のサーバーは公開名のリクエストに
    https の印を付ける）。この PC の http のアドレスでも使える。"""

    def get_cookie_secure(self, app) -> bool:
        return request.is_secure or super().get_cookie_secure(app)


def init_app(app: Flask, instance: Path, mount: str) -> None:
    app.secret_key = secret_key(Path(instance) / "secret_key")
    app.session_interface = _SessionInterface()
    app.config.update(
        # 専用の名前とパス: News Site のセッション cookie が同じホストにある。
        SESSION_COOKIE_NAME="val_session",
        SESSION_COOKIE_PATH=(mount or "") + "/",
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_HTTPONLY=True,
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    @app.before_request
    def _check_csrf():
        if request.method in _SAFE_METHODS:
            return
        if request.headers.get("Sec-Fetch-Site", "") == "cross-site":
            abort(403)
        expected = session.get("csrf")
        supplied = request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(str(supplied), str(expected)):
            abort(400)
        return

    @app.after_request
    def _headers(response):
        for name, value in HEADERS:
            response.headers.setdefault(name, value)
        return response

    app.jinja_env.globals["csrf_token"] = csrf_token
