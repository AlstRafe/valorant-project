"""VAL Tracker as one site of Home Page's server (``~/Documents/Claude/Home Page``, homepage/server.py).

That server owns the address and lists the sites on "/". Its sites.toml names this site:

    [[site]]
    mount = "/val"
    python = "../VALORANT-Project"
    entry = "valtracker.hosting:site"

and calls ``site`` once at its start. It handles ``?lang=``, the public name (https, HSTS) and marks a
visitor's requests (``homepage.visitor``), so the owner-only pages can tell them apart (security.py).
"""

from __future__ import annotations

from valtracker import NAME, create_app, paths

#: Under the mount, for crawlers to leave alone.
DISALLOW = ("/media/", "/search", "/settings")


def site(mount: str = "/val", host: dict | None = None) -> dict:
    """The WSGI ``app`` and what Home Page's front page shows. ``host`` (where the host answers) is not needed."""
    return {
        "app": create_app(mount=mount),
        "name": NAME,
        "subtitle": "VALORANT の戦績トラッカー",
        "disallow": DISALLOW,
        "version": paths.deployed_version,
    }
