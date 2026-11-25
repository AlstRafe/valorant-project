"""VAL Tracker: このホストのサイト群のための VALORANT トラッカー（プレイヤーのランクと試合、エージェント、
持ち主のコレクション）。

``create_app`` が Flask アプリ（WSGI）を作る。Home Page のサーバーは hosting.py 経由で読み込みマウント（``/val``）
の下で配る。``python -m valtracker`` はこの PC だけで、``python -m valtracker.desktop``（PC アプリ、 desktop.py）
は専用のウィンドウで、Riot Client のサインインを使って動かす。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, current_app, g, request, url_for

from valtracker import content, i18n, net, paths, phrases, release, security, theme
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
#: 公開サイト: PC アプリでコピーしたリンクの行き先（アプリ自身のアドレス＝127.0.0.1 と変わるポートは、
#: ほかの端末からはどこにも行かない）。VAL_PUBLIC_SITE で別のものを指定できる。
PUBLIC_SITE = "https://haruxera.duckdns.org/val"


def shared_url(endpoint: str, **values) -> str:
    """ほかの人に渡すページの完全なアドレス: PC アプリでは公開サイトのもの、ほかはこのサイトのもの。"""
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
    """``mount``: サイトを配るパス（セッション cookie のパス）。 ``instance``: このマシンのデータ（設定、
    保存した一覧と絵）。 ``fetch``、``send``: 外部 API と持ち主の Riot アカウントへの聞き方（テストは自前のもの）。
    ``local``: この PC の Riot Client（localclient.LocalClient）。デスクトップアプリだけ。"""
    instance = Path(instance) if instance is not None else paths.INSTANCE
    app = Flask(__name__, instance_path=str(instance))
    app.config["VAL_SETTINGS"] = settings = Settings(instance)
    app.config["VAL_HENRIK"] = Henrik(settings.henrik_key, fetch)
    app.config["VAL_RIOT_WEB"] = RiotWeb(fetch)
    app.config["VAL_CONTENT"] = Content(instance, fetch)
    app.config["VAL_MEDIA"] = Media(instance, fetch)
    app.config["VAL_LOCAL"] = local
    app.config["VAL_PUBLIC"] = (
        (os.environ.get("VAL_PUBLIC_SITE") or PUBLIC_SITE).rstrip("/") if local is not None else ""
    )
    # PC アプリ: リリースに新しいビルドがあれば {"version", "url"}（desktop.py が時々見る）
    app.config["VAL_UPDATE"] = None
    app.config["VAL_UPDATE_LOOKED"] = 0.0  # 最後にリリースを見た時刻（0: まだ）
    app.config["VAL_BUILD"] = None  # ビルドしたアプリの {"commit", "date"}（desktop.py）。ソースから動かすなら None
    # PC アプリ（desktop.py）: ゲームに譲るためにしたこと、マッチ成立の通知、Windows 起動時の開始（pc.py）
    app.config["VAL_CALM"] = []
    app.config["VAL_ALERT"] = None
    app.config["VAL_AUTOSTART"] = None
    # PC アプリの自動更新（update.py）と、更新した直後か（1 回だけ知らせる）
    app.config["VAL_UPDATER"] = None
    app.config["VAL_UPDATED"] = False
    app.config["VAL_RIOT"] = Riot(instance, send, app.config["VAL_CONTENT"].client_version, local=local)
    app.config["VAL_DIARY"] = Diary(instance)
    # 持ち主が保存したロードアウト（owner.py: コレクションのセット）
    app.config["VAL_SETS"] = Settings(instance, "loadout_sets.json")
    app.config["VAL_ACTIVITY"] = activity = Activity(instance)
    if local is not None:
        # PC アプリ: サイトの数字（サイトは一日中試合を見ている。アプリ自身のファイルには持ち主のものしか無い）
        public = app.config["VAL_PUBLIC"]

        def shared(kind: str, params: dict):
            if not public.startswith("https://"):
                return None
            status, body = fetch(f"{public}/numbers/{kind}?{urlencode(params)}", {"Accept": "application/json"})
            return json.loads(body) if status == 200 else None

        activity.shared = shared
    # VAL_COLLECT_SECONDS=0 で、裏でほかのプレイヤーを見るのを止める（activity.py）
    seconds = float(os.environ.get("VAL_COLLECT_SECONDS", COLLECT_SECONDS) or 0)
    game = app.config["VAL_CONTENT"]
    # サイトはほかのプレイヤーの試合もゲーム自身の記録から読む（持ち主の連携アカウント）。
    # PC アプリはしない（ゲーム以外に Riot に何も聞かない）。サイトの数字を出す
    app.config["VAL_COLLECTOR"] = collector = Collector(
        activity,
        app.config["VAL_HENRIK"],
        seconds,
        app.config["VAL_RIOT"] if local is None else None,
        lambda: game.map_index("en-GB"),
    )
    app.config["VAL_ARCHIVE"] = archive = Archive(instance)
    # プレイヤーの全履歴を裏で読む（archive.py）。VAL_FILL_SECONDS=0 で止める
    app.config["VAL_FILLER"] = filler = Filler(
        archive,
        app.config["VAL_RIOT"],
        app.config["VAL_HENRIK"],
        lambda: game.map_index("en-GB"),
        float(os.environ.get("VAL_FILL_SECONDS", FILL_SECONDS) or 0),
    )
    #: プレイヤーページが数える通算（views._lifetime）
    app.config["VAL_LIVES"] = TTLCache(6)
    app.config["VAL_PHRASES"] = (
        True  # ページの日本語・中国語に文節の <wbr>（テストは書いた HTML そのままを見るので切る）
    )
    security.init_app(app, instance, mount)
    app.config["VAL_RATINGS"] = Ratings(instance, app.config["SECRET_KEY"])  # security.secret_key(): bytes

    @app.before_request
    def _collect():
        if not app.testing:
            collector.start()  # 1 回だけ。最初に見るまで COLLECT_SECONDS 待つ
            filler.start()  # 1 回だけ。プレイヤーの通算が求められるまで待つ

    @app.before_request
    def _language():
        asked = i18n.asked(request.environ)
        if asked:  # Home Page のサーバーがこちらより先に答える。単独で動かすときは同じようにする
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
        # 絵とファイルは全員同じ: cookie にかかわらずブラウザーに残す（"Vary: Cookie" の答えはセッション cookie
        # が変わるまでしか残らず、/live の絵が何度も取り直されていた）
        if request.endpoint not in ("static", "media", "picture"):
            response.headers.add("Vary", "Cookie, Accept-Language")
        return response

    @app.after_request
    def _phrases(response):
        # 日本語・中国語は文節の間でだけ折り返す（どのブラウザでも同じに。valtracker/phrases、CSS の word-break: keep-all）
        if app.config["VAL_PHRASES"] and response.mimetype == "text/html" and not response.direct_passthrough:
            response.set_data(phrases.mark_page(response.get_data(as_text=True), getattr(g, "lang", i18n.DEFAULT)))
        return response

    versions: dict[str, str] = {}

    def asset(name: str, external: bool = False) -> str:
        """バージョン付きの static/ のファイル: 変わったファイルはアドレスが変わるので 1 年残せる。
        ``external``: 完全なアドレス（https://name/val/...）。リンクのプレビューに必要。"""
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
        """/pic/ 経由のニュース・eスポーツの絵（media.OUTSIDE）。ほかのアドレスは ""。"""
        path = outside_path(url)
        return url_for("picture", path=path) if path else ""

    app.add_template_filter(content.plain, "plain")
    app.add_template_filter(i18n.phrases, "phrases")

    def _updated_once() -> bool:
        """アプリを更新した直後か: 更新後に最初に描くページだけ true。"""
        if not app.config["VAL_UPDATED"] or request.endpoint in ("static", "media", "picture"):
            return False
        app.config["VAL_UPDATED"] = False
        return True

    @app.context_processor
    def _page():
        from valtracker.owner import update_state

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
            # 持ち主の Riot アカウント（別のものを表示するボタン用、_accounts.html）
            "own_accounts": lambda: app.config["VAL_RIOT"].accounts() if security.is_owner() else [],
            # PC アプリ（desktop.py）: ゲームが変わったら Riot Client が知らせる（/live/next）
            "desktop": local is not None,
            "push_version": g.get("push_version", local.version) if local is not None else 0,
            "public_site": app.config["VAL_PUBLIC"],
            "shared_url": shared_url,
            "update": app.config["VAL_UPDATE"],
            "data_folder": app.instance_path if local is not None else "",
            "app_page": release.PAGE,
            # PC アプリの自動更新（update.py）: どこまで進んだかと、更新した直後か（1 回だけ知らせる）
            "updating": update_state() if local is not None else None,
            "updated": _updated_once(),
        }

    from valtracker import views

    views.register(app)
    return app
