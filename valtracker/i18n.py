"""サイトの言語。このホストの全サイトと同じ選び方（Home Page の homepage/i18n.py）。

訪問者はどのアドレスでも ``?lang=<code>`` を付けるか（Home Page のサーバーがホスト全体の ``lang`` cookie で答える。
単独で動かすときはこのアプリが同じことをする）、ヘッダーのメニューで言語を選ぶ。
選んでいなければブラウザーの言語（Accept-Language）で決め、何も送ってこなければ日本語。

文は日本語で書き、その日本語で引く（``text(lang, "検索")``）。訳の無い文は日本語で出す。訳は locale.py に 1 文ずつ。"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode

from markupsafe import Markup

from valtracker.locale import TEXTS

LANGS = ("ja", "ko", "en-GB", "zh-TW")
DEFAULT = "ja"
#: 各言語の自称（メニュー用）。
NAMES = {"ja": "日本語", "ko": "한국어", "en-GB": "English (UK)", "zh-TW": "中文（台灣）"}
#: og:locale
LOCALES = {"ja": "ja_JP", "ko": "ko_KR", "en-GB": "en_GB", "zh-TW": "zh_TW"}
#: valorant-api.com の名前の言語（英語はアメリカ英語だけ）。
CONTENT = {"ja": "ja-JP", "ko": "ko-KR", "en-GB": "en-US", "zh-TW": "zh-TW"}
COOKIE = "lang"
COOKIE_SECONDS = 365 * 24 * 3600


#: 句の切れ目: 日本語・中国語の読点と句点のあと（文の終わりは除く）。空白で語を分ける言語（英語、韓国語）は
#: 語の間で折れるので分けない（長い句が 1 つ丸ごと次の行へ送られ、前の行に 1 語だけ残ってしまう）。
_PHRASE = re.compile(r"(?<=[、。，．！？：；])(?=\S)")


def phrases(said) -> Markup:
    """見出しや短い文を句ごとの <span class="ph">（app.css で inline-block）に: 行は句の切れ目で分かれ、
    1 つの句が 1 行に入らないときだけ句の中で折れる（どの画面幅でも「もう / ひとつの画面」のように切れない）。"""
    said = str(said)
    pieces, start = [], 0
    for found in _PHRASE.finditer(said):
        pieces.append((said[start : found.start()], found.group()))
        start = found.end()
    pieces.append((said[start:], ""))
    if len(pieces) < 2:
        return Markup.escape(said)
    return Markup("").join(Markup('<span class="ph">{}</span>{}').format(piece, gap) for piece, gap in pieces if piece)


def text(lang: str, ja: str, **values) -> str:
    """``ja`` の ``lang`` での訳（無ければ日本語）。``{name}`` を埋める。"""
    said = TEXTS.get(ja, {}).get(lang, ja) if lang != DEFAULT else ja
    return said.format(**values) if values else said


def chosen(environ) -> str:
    """訪問者の言語: ``lang`` cookie、無ければブラウザーの言語のうちこちらにある最初のもの、それも無ければ日本語。"""
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get("HTTP_COOKIE", ""))
    except Exception:  # 壊れた cookie ヘッダーは選択ではない
        cookie = SimpleCookie()
    value = cookie[COOKIE].value if COOKIE in cookie else ""
    return value if value in LANGS else from_browser(environ.get("HTTP_ACCEPT_LANGUAGE", ""))


def from_browser(header: str) -> str:
    """Accept-Language を LANGS のどれかに: zh-* は繁体字中国語、en-* はイギリス英語。
    こちらの言語をどれも挙げないブラウザーには英語（日本語より読まれやすい）、何も送らなければ日本語。"""
    if not header.strip():
        return DEFAULT
    ranked = []
    for number, part in enumerate(header.split(",")):
        tag, _, rest = part.strip().partition(";")
        try:
            weight = float(rest.strip()[2:]) if rest.strip().startswith("q=") else 1.0
        except ValueError:
            weight = 0.0
        ranked.append((-weight, number, tag.strip().lower()))
    for weight, _, tag in sorted(ranked):
        if weight >= 0:
            continue
        for prefix, lang in (("ja", "ja"), ("ko", "ko"), ("zh", "zh-TW"), ("en", "en-GB")):
            if tag == prefix or tag.startswith(prefix + "-"):
                return lang
    return "en-GB"


def asked(environ) -> tuple[str, str] | None:
    """GET の ``?lang=<code>``: （コード, それを除いたクエリ）、無ければ None。"""
    if environ.get("REQUEST_METHOD", "GET") not in ("GET", "HEAD"):
        return None
    pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    values = [value for key, value in pairs if key == COOKIE]
    if not values or values[-1] not in LANGS:
        return None
    return values[-1], urlencode([(key, value) for key, value in pairs if key != COOKIE])


def cookie_header(lang: str, secure: bool = False) -> str:
    """このホストの全サイトで ``lang`` を覚える Set-Cookie の値。"""
    return f"{COOKIE}={lang}; Path=/; Max-Age={COOKIE_SECONDS}; SameSite=Lax" + ("; Secure" if secure else "")


def menu(lang: str) -> str:
    """全サイトのヘッダーにある言語メニュー: スクリプト不要の <details>（各選択肢は ?lang= のリンク）。今の言語に印。"""
    current = ' aria-current="true"'
    items = "".join(
        f'<li><a href="?{COOKIE}={code}" lang="{code}" hreflang="{code}"{current if code == lang else ""}>'
        f"{escape(NAMES[code])}</a></li>"
        for code in LANGS
    )
    label = escape(text(lang, "言語"))
    return (
        f'<details class="lang"><summary title="{label}" aria-label="{label}: {escape(NAMES[lang])}">'
        '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
        '<path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>'
        f"<span>{escape(NAMES[lang])}</span></summary><ul>{items}</ul></details>"
    )


_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def when(lang: str, moment) -> str:
    """試合の開始を短く: 11月4日 21:05 / 4 Nov 21:05（ほかの時刻と同じく日本時間）。
    今年でなければ年も付ける（通算は何年も前までさかのぼる）。"""
    clock = f"{moment.hour}:{moment.minute:02d}"
    if moment.year != datetime.now(moment.tzinfo).year:
        return f"{day(lang, moment)} {clock}"
    if lang == "en-GB":
        return f"{moment.day} {_EN_MONTHS[moment.month - 1]} {clock}"
    if lang == "ko":
        return f"{moment.month}월 {moment.day}일 {clock}"
    return f"{moment.month}月{moment.day}日 {clock}"


def day(lang: str, moment) -> str:
    """年つきの日付: 2025年3月4日 / 4 Mar 2025 / 2025년 3월 4일。"""
    if lang == "en-GB":
        return f"{moment.day} {_EN_MONTHS[moment.month - 1]} {moment.year}"
    if lang == "ko":
        return f"{moment.year}년 {moment.month}월 {moment.day}일"
    return f"{moment.year}年{moment.month}月{moment.day}日"


_EN_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def weekday(lang: str, moment) -> str:
    """今年の日付と曜日: 11月6日（金） / Fri 6 Nov / 11월 6일 (금) / 11月6日（五）。"""
    number = moment.weekday()
    if lang == "en-GB":
        return f"{_EN_DAYS[number]} {moment.day} {_EN_MONTHS[moment.month - 1]}"
    if lang == "ko":
        return f"{moment.month}월 {moment.day}일 ({'월화수목금토일'[number]})"
    if lang == "zh-TW":
        return f"{moment.month}月{moment.day}日（{'一二三四五六日'[number]}）"
    return f"{moment.month}月{moment.day}日（{'月火水木金土日'[number]}）"
