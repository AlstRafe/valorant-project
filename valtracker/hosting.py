"""Home Page のサーバー（``~/Documents/Claude/Home Page``、homepage/server.py）の 1 サイトとしての VAL Tracker。

アドレスはあちらのサーバーが持ち、"/" にサイトを並べる。sites.toml でこのサイトを次のように指定する:

[[site]]
mount = "/val"
python = "../VALORANT-Project"
entry = "valtracker.hosting:site"

起動時に ``site`` を 1 回呼ぶ。``?lang=``、公開名（https、HSTS）を扱い、
訪問者のリクエストに印を付ける（``homepage.visitor``）ので、持ち主専用ページは見分けられる（security.py）。"""

from __future__ import annotations

from valtracker import NAME, create_app, paths

#: クローラーに触らせないマウント下のパス。
DISALLOW = (
    "/media/",
    "/pic/",
    "/search",
    "/settings",
    "/collection",
    "/leaderboard/part",
    "/status/notice",
    "/crosshair?",
    "/sensitivity?",
    "/queues?",
)


def site(mount: str = "/val", host: dict | None = None) -> dict:
    """WSGI の ``app`` と、Home Page のトップに出すもの。``host``（ホストの答える場所）は使わない。"""
    return {
        "app": create_app(mount=mount),
        "name": NAME,
        "subtitle": "VALORANT の戦績トラッカー",
        "disallow": DISALLOW,
        "version": paths.deployed_version,
    }
