"""The languages of the site: the same choice as every site of this host (Home Page's homepage/i18n.py).

A visitor picks a language with ``?lang=<code>`` on any address (Home Page's server answers it with the
``lang`` cookie for the whole host; run on its own, this app does the same) or with the header's menu.
Without a choice the browser's languages decide (Accept-Language), and Japanese when it sends none.

Texts are written in Japanese and looked up by that Japanese (``text(lang, "検索")``); a text without a
translation is shown in Japanese. The translations are in locale.py, one text per entry.
"""

from __future__ import annotations

from html import escape
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode

from valtracker.locale import TEXTS

LANGS = ("ja", "ko", "en-GB", "zh-TW")
DEFAULT = "ja"
#: Each language in its own words (the menu).
NAMES = {"ja": "日本語", "ko": "한국어", "en-GB": "English (UK)", "zh-TW": "中文（台灣）"}
#: og:locale
LOCALES = {"ja": "ja_JP", "ko": "ko_KR", "en-GB": "en_GB", "zh-TW": "zh_TW"}
#: The language valorant-api.com names things in (it has American English only).
CONTENT = {"ja": "ja-JP", "ko": "ko-KR", "en-GB": "en-US", "zh-TW": "zh-TW"}
COOKIE = "lang"
COOKIE_SECONDS = 365 * 24 * 3600


def text(lang: str, ja: str, **values) -> str:
    """``ja`` in ``lang`` (Japanese when there is no translation), with ``{name}`` filled in."""
    said = TEXTS.get(ja, {}).get(lang, ja) if lang != DEFAULT else ja
    return said.format(**values) if values else said


def chosen(environ) -> str:
    """The visitor's language: the ``lang`` cookie, else the browser's first language we have, else Japanese."""
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get("HTTP_COOKIE", ""))
    except Exception:  # noqa: BLE001 - a malformed cookie header is no choice
        cookie = SimpleCookie()
    value = cookie[COOKIE].value if COOKIE in cookie else ""
    return value if value in LANGS else from_browser(environ.get("HTTP_ACCEPT_LANGUAGE", ""))


def from_browser(header: str) -> str:
    """Accept-Language to one of LANGS: zh-* reads Traditional Chinese, en-* British English; a browser
    that names none of ours gets English (more likely read than Japanese), one that sends nothing Japanese."""
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
    """``?lang=<code>`` on a GET: (the code, the query without it), or None."""
    if environ.get("REQUEST_METHOD", "GET") not in ("GET", "HEAD"):
        return None
    pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    values = [value for key, value in pairs if key == COOKIE]
    if not values or values[-1] not in LANGS:
        return None
    return values[-1], urlencode([(key, value) for key, value in pairs if key != COOKIE])


def cookie_header(lang: str, secure: bool = False) -> str:
    """The Set-Cookie value that remembers ``lang`` for every site of this host."""
    return f"{COOKIE}={lang}; Path=/; Max-Age={COOKIE_SECONDS}; SameSite=Lax" + ("; Secure" if secure else "")


def menu(lang: str) -> str:
    """The language menu every site shows in its header: a <details> that needs no script
    (each choice is a link with ?lang=), the current language marked."""
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
    """A match's start, short: 11月4日 21:05 / 4 Nov 21:05 (the time in Japan, as the site's other times)."""
    clock = f"{moment.hour}:{moment.minute:02d}"
    if lang == "en-GB":
        return f"{moment.day} {_EN_MONTHS[moment.month - 1]} {clock}"
    if lang == "ko":
        return f"{moment.month}월 {moment.day}일 {clock}"
    return f"{moment.month}月{moment.day}日 {clock}"
