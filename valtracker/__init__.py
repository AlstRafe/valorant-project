"""VAL Tracker: a VALORANT tracker (players' ranks and matches, agents, the owner's collection) for the sites of
this host.

``create_app`` makes the Flask app (WSGI). Home Page's server loads it through hosting.py and serves it under
its mount (``/val``); ``python -m valtracker`` serves it on its own on this PC.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from flask import Flask, g, request, url_for

from valtracker import content, i18n, net, paths, security, theme
from valtracker.content import Content
from valtracker.henrik import Henrik
from valtracker.media import Media
from valtracker.riot import Riot
from valtracker.settings import Settings

NAME = "VAL Tracker"


def create_app(
    mount: str = "", instance: Path | None = None, fetch: net.Fetch = net.fetch, send: net.Send = net.send
) -> Flask:
    """``mount``: the path the site is served under (the session cookie's path). ``instance``: this machine's
    data (settings, saved lists and pictures). ``fetch``, ``send``: how the outside APIs and the owner's Riot
    account are asked (the tests' own)."""
    instance = Path(instance) if instance is not None else paths.INSTANCE
    app = Flask(__name__, instance_path=str(instance), instance_relative_config=False)
    app.config["VAL_SETTINGS"] = settings = Settings(instance)
    app.config["VAL_HENRIK"] = Henrik(settings.henrik_key, fetch)
    app.config["VAL_CONTENT"] = Content(instance, fetch)
    app.config["VAL_MEDIA"] = Media(instance, fetch)
    app.config["VAL_RIOT"] = Riot(instance, send, app.config["VAL_CONTENT"].client_version)
    security.init_app(app, instance, mount)

    @app.before_request
    def _language():
        asked = i18n.asked(request.environ)
        if asked:  # Home Page's server answers this before us; run on its own, we do it the same way
            code, rest = asked
            response = app.redirect(request.script_root + request.path + (f"?{rest}" if rest else ""), 303)
            response.headers.add("Set-Cookie", i18n.cookie_header(code, request.is_secure))
            response.headers["Cache-Control"] = "no-store"
            return response
        g.lang = i18n.chosen(request.environ)
        return None

    @app.after_request
    def _caching(response):
        if request.endpoint == "static" and request.args.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.endpoint != "media":
            response.headers.setdefault("Cache-Control", "no-cache")
        response.headers.add("Vary", "Cookie, Accept-Language")
        return response

    versions: dict[str, str] = {}

    def asset(name: str, external: bool = False) -> str:
        """A file of static/ at its version: a changed file has a new address, so it can be kept for a year.
        ``external``: the full address (https://name/val/...), which link previews need."""
        if name not in versions:
            try:
                versions[name] = hashlib.sha256((paths.PACKAGE / "static" / name).read_bytes()).hexdigest()[:10]
            except OSError:
                versions[name] = "0"
        return url_for("static", filename=name, v=versions[name], _external=external)

    def media(url) -> str:
        path = content.media_path(url)
        return url_for("media", path=path) if path else ""

    @app.context_processor
    def _page():
        lang = getattr(g, "lang", i18n.DEFAULT)
        return {
            "lang": lang,
            "locale": i18n.LOCALES[lang],
            "t": lambda ja, **values: i18n.text(lang, ja, **values),
            "language_menu": i18n.menu(lang),
            "theme_button": theme.button(lang),
            "theme_script": theme.THEME_SCRIPT,
            "asset": asset,
            "media": media,
            "site_name": NAME,
            "home": request.environ.get("homepage.home", ""),
            "owner": security.is_owner(),
        }

    from valtracker import views

    views.register(app)
    return app
