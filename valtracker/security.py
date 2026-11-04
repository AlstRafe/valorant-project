"""What the browser may do with our pages, and which requests may change something.

- Every answer carries a Content-Security-Policy that allows only this site's own files, and the one inline
  script (the theme, theme.py) by its hash; pictures of the game come through /media/, never from outside.
- A POST (the settings form) must carry the session's CSRF token (a random value only our own page knows),
  and a request the browser marks cross-site is refused before the token is looked at.
- Pages for the owner only (settings) are refused to visitors: Home Page's server marks a request from the
  public name that is not signed in as the owner (``homepage.visitor``). Run on its own, the app listens on
  this PC only (127.0.0.1), so whoever opens it is the owner.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, session
from flask.sessions import SecureCookieSessionInterface

from valtracker.theme import THEME_HASH

VISITOR_KEY = "homepage.visitor"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' {THEME_HASH}",
        "style-src 'self'",
        "img-src 'self' data:",
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
    """Visitors are told there is no such page (it does not exist for them)."""
    if not is_owner():
        abort(404)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def secret_key(path: Path) -> bytes:
    """Kept across restarts, so an open settings page still sends a valid token."""
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
        pass  # a read-only folder: the key lives as long as this process
    return data


class _SessionInterface(SecureCookieSessionInterface):
    """The session cookie is Secure whenever the page came over https (Home Page's server marks the public
    name's requests https) and still works over this PC's plain-http address."""

    def get_cookie_secure(self, app) -> bool:
        return request.is_secure or super().get_cookie_secure(app)


def init_app(app: Flask, instance: Path, mount: str) -> None:
    app.secret_key = secret_key(Path(instance) / "secret_key")
    app.session_interface = _SessionInterface()
    app.config.update(
        # Its own name and path: News Site's session cookie lives on the same host.
        SESSION_COOKIE_NAME="val_session",
        SESSION_COOKIE_PATH=(mount or "") + "/",
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_HTTPONLY=True,
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    @app.before_request
    def _check_csrf():
        if request.method in _SAFE_METHODS:
            return None
        if request.headers.get("Sec-Fetch-Site", "") == "cross-site":
            abort(403)
        expected = session.get("csrf")
        supplied = request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(str(supplied), str(expected)):
            abort(400)
        return None

    @app.after_request
    def _headers(response):
        for name, value in HEADERS:
            response.headers.setdefault(name, value)
        return response

    app.jinja_env.globals["csrf_token"] = csrf_token
