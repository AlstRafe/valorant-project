"""VAL Tracker: a VALORANT tracker (players' ranks and matches, agents, the owner's collection) for the sites of
this host.

``create_app`` makes the Flask app (WSGI). Home Page's server loads it through hosting.py and serves it under
its mount (``/val``); ``python -m valtracker`` serves it on its own on this PC, and ``python -m valtracker.desktop``
(the PC app, desktop.py) in a window of its own, signed in through the Riot Client.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from flask import Flask, current_app, g, request, url_for

from valtracker import content, i18n, net, paths, security, theme
from valtracker.activity import COLLECT_SECONDS, Activity, Collector
from valtracker.archive import FILL_SECONDS, Archive, Filler
from valtracker.cache import TTLCache
from valtracker.content import Content
from valtracker.diary import Diary
from valtracker.henrik import Henrik
from valtracker.media import Media, outside_path
from valtracker.ratings import Ratings
from valtracker.riot import Riot
from valtracker.riotweb import RiotWeb
from valtracker.settings import Settings

NAME = "VAL Tracker"
#: The public site: where a link copied in the PC app leads (the app's own address, 127.0.0.1 and a port that
#: changes, leads nowhere on another device). VAL_PUBLIC_SITE names another.
PUBLIC_SITE = "https://haruxera.duckdns.org/val"


def shared_url(endpoint: str, **values) -> str:
    """A page's full address to give to someone else: the public site's in the PC app, else this site's own."""
    public = current_app.config.get("VAL_PUBLIC")
    if public:
        return public + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)


def create_app(
    mount: str = "",
    instance: Path | None = None,
    fetch: net.Fetch = net.fetch,
    send: net.Send = net.send,
    local=None,
) -> Flask:
    """``mount``: the path the site is served under (the session cookie's path). ``instance``: this machine's
    data (settings, saved lists and pictures). ``fetch``, ``send``: how the outside APIs and the owner's Riot
    account are asked (the tests' own). ``local``: the Riot Client on this PC (localclient.LocalClient), in the
    desktop app only."""
    instance = Path(instance) if instance is not None else paths.INSTANCE
    app = Flask(__name__, instance_path=str(instance), instance_relative_config=False)
    app.config["VAL_SETTINGS"] = settings = Settings(instance)
    app.config["VAL_HENRIK"] = Henrik(settings.henrik_key, fetch)
    app.config["VAL_RIOT_WEB"] = RiotWeb(fetch)
    app.config["VAL_CONTENT"] = Content(instance, fetch)
    app.config["VAL_MEDIA"] = Media(instance, fetch)
    app.config["VAL_LOCAL"] = local
    app.config["VAL_PUBLIC"] = (
        (os.environ.get("VAL_PUBLIC_SITE") or PUBLIC_SITE).rstrip("/") if local is not None else ""
    )
    # the PC app: {"version", "url"} when a newer build is on the release (desktop.py looks now and then)
    app.config["VAL_UPDATE"] = None
    app.config["VAL_UPDATE_LOOKED"] = 0.0  # when the release was last looked at (0: not yet)
    app.config["VAL_BUILD"] = None  # the built app's {"commit", "date"} (desktop.py), None when run from source
    app.config["VAL_RIOT"] = Riot(instance, send, app.config["VAL_CONTENT"].client_version, local=local)
    app.config["VAL_DIARY"] = Diary(instance)
    # the owner's saved loadouts (owner.py: the collection's sets)
    app.config["VAL_SETS"] = Settings(instance, "loadout_sets.json")
    app.config["VAL_ACTIVITY"] = activity = Activity(instance)
    # VAL_COLLECT_SECONDS=0 turns the background look at more players off (activity.py)
    seconds = float(os.environ.get("VAL_COLLECT_SECONDS", COLLECT_SECONDS) or 0)
    app.config["VAL_COLLECTOR"] = collector = Collector(activity, app.config["VAL_HENRIK"], seconds)
    app.config["VAL_ARCHIVE"] = archive = Archive(instance)
    # the background reading of a player's whole history (archive.py); VAL_FILL_SECONDS=0 turns it off
    game = app.config["VAL_CONTENT"]
    app.config["VAL_FILLER"] = filler = Filler(
        archive,
        app.config["VAL_RIOT"],
        app.config["VAL_HENRIK"],
        lambda: game.map_index("en-GB"),
        float(os.environ.get("VAL_FILL_SECONDS", FILL_SECONDS) or 0),
    )
    #: players' lifetimes as their page counts them (views._lifetime)
    app.config["VAL_LIVES"] = TTLCache(6)
    security.init_app(app, instance, mount)
    app.config["VAL_RATINGS"] = Ratings(instance, app.secret_key)

    @app.before_request
    def _collect():
        if not app.testing:
            collector.start()  # once; it waits COLLECT_SECONDS before its first look
            filler.start()  # once; it waits until a player's lifetime is wanted

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
        elif request.endpoint not in ("media", "picture"):
            response.headers.setdefault("Cache-Control", "no-cache")
        # the pictures and files are the same for everyone: kept by the browser whatever the cookies (a browser
        # keeps a "Vary: Cookie" answer only until the session cookie changes, and /live's pictures were fetched
        # again and again)
        if request.endpoint not in ("static", "media", "picture"):
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

    def picture(url) -> str:
        """A news or esports picture through /pic/ (media.OUTSIDE), or "" for any other address."""
        path = outside_path(url)
        return url_for("picture", path=path) if path else ""

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
            "picture": picture,
            "site_name": NAME,
            "home": request.environ.get("homepage.home", ""),
            "owner": security.is_owner(),
            # the PC app (desktop.py): the Riot Client tells when the game changes (/live/next)
            "desktop": local is not None,
            "push_version": g.get("push_version", local.version) if local is not None else 0,
            "public_site": app.config["VAL_PUBLIC"],
            "shared_url": shared_url,
            "update": app.config["VAL_UPDATE"],
            "data_folder": app.instance_path if local is not None else "",
        }

    from valtracker import views

    views.register(app)
    return app
