"""Where a line may break in Japanese and Traditional Chinese text: between phrases (文節), never inside a word.

Browsers break Japanese and Chinese between any two characters, so a headline can end up as 「ブラウザの中で練 / 習」
depending on the device's width and fonts. ``word-break: auto-phrase`` fixes that in Chrome only. Instead the portal
marks the places between phrases with ``<wbr>`` and the stylesheet sets ``word-break: keep-all`` (break only there,
and at spaces and punctuation) with ``overflow-wrap: anywhere`` (a phrase too long for the line still breaks), which
every browser follows.

The phrases are found with BudouX (https://github.com/google/budoux, Apache License 2.0, ``LICENSE`` here): its
trained models for Japanese (``ja.json``) and Traditional Chinese (``zh-hant.json``) are copied from budoux 0.7.0
unchanged, and ``_chunks`` is its ``Parser.parse``. Its Chinese model also cuts words where it is unsure
(「瀏覽|器」「練|習題」「每|小|時」), so in Chinese only its surer places count (``THRESHOLD``) and a lone character
joins a neighbour (``_join_singles``). Korean breaks between
words already (``word-break: keep-all``), English at spaces.

BudouX's Japanese model sometimes cuts a kanji word too (「架|空」), so there two kanji always stay together.

A short name in Latin letters inside such text stays on one line (never 「Riot / ID」), and so does a word after
one or a number (never 「Riot ID / を」「iThome / 的報導」「資格 / 60 試験」「約 1 / 分」), and a particle after a
closing bracket (never 「（Kubernetes） / を」, nor 「[[site]] / 를」 in Korean).

The text itself decides the model, not only the page: a news title in Japanese on the English page is marked too
(kana: Japanese; kanji alone: the page's language, Chinese on a Korean or English page).

Copied from Home Page (``homepage/phrases``, the same rule in its docs/CONVENTIONS.md 「改行」) with ``mark_page``
added: VAL Tracker marks every page once it is drawn (``__init__.py``), so every template, translation and name is
covered without changing them. Only text between tags changes; attributes, scripts, the title and form controls stay
as they are.
"""

from __future__ import annotations

import html
import json
import re
from functools import cache, lru_cache

from valtracker.paths import PACKAGE

MODELS = {"ja": "ja.json", "zh-TW": "zh-hant.json"}
#: How sure the model must be of a place between phrases (BudouX: above 0). The Chinese model's places below about
#: 1500 are often inside a word (瀏覽|82器, 隨|88時, 更|403新, 練|1333習題) and its sure ones well above it.
THRESHOLD = {"ja": 0, "zh-TW": 1500}
#: Text no longer than this is never split (a word or a short label).
SHORT = 4
#: Words in Latin letters one after another, with a space between them ("Riot ID", "VAL Tracker 2").
LATIN_WORDS = re.compile(r"[A-Za-z0-9][\w.+#-]*(?: [A-Za-z0-9][\w.+#-]*)+", re.ASCII)
#: Two such words no longer than this together are a name, kept on one line in Japanese, Chinese and Korean text.
NAME = 12
#: Punctuation at the end of a Chinese chunk, not counted as its characters.
CLAUSE_ENDS = "，。、；：！？）」』"
#: Chinese characters that go with the words after them, when the model leaves one alone (each, every, this, and...).
LEADS = set("各每這那第與和及並把被從在對很再又不沒只也就都")
CJK = "\u3041-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
#: Spaces that are no place to break: after a word in Latin letters or a number before Japanese or Chinese
#: ("Riot ID を", "60 試験"), and between such text and a number after it ("資格 60", "約 1 分").
HELD_SPACE = re.compile(rf"(?<=[A-Za-z0-9.)\]]) (?=[{CJK}])|(?<=[{CJK}]) (?=[0-9])")
#: A closing bracket with a particle after it (hiragana, Hangul): a word joiner keeps them together.
AFTER_BRACKET = re.compile(r"(?<=[)\]）」』】])(?=[\u3041-\u309f\uac00-\ud7a3])")


#: The page in pieces: comments, elements whose text is not shown as text or must stay as written (kept whole),
#: tags, and the text between them.
_PIECES = re.compile(
    r"<!--.*?-->"
    r"|<(script|style|textarea|title|template|option|pre|code|kbd|samp|svg)\b[^>]*>.*?</\1\s*>"
    r"|<[^>]*>|[^<]+|<",
    re.DOTALL | re.IGNORECASE,
)


#: Elements that stay on one line (buttons, menus, tabs, badges): no ``<wbr>`` inside them, since Chrome breaks at
#: one even under ``white-space: nowrap`` (「深色模式：/自動」「同じ/パーティー」).
_ONE_LINE = {"button", "summary", "label", "nav"}
#: ...and any element with one of these classes.
_ONE_LINE_CLASS = re.compile(r"""\sclass="[^"]*\b(?:badge|chip|mvp-tag|pn|tag|home-link|home-tab)\b""")
_TAG = re.compile(r"<(/?)([a-zA-Z][\w-]*)")


def mark_page(page: str, lang: str) -> str:
    """A drawn HTML page with ``mark`` applied to the text between its tags (only text with letters beyond ASCII:
    English needs nothing), except inside elements kept on one line (``_ONE_LINE``)."""
    out = []
    held = ""  # the element kept on one line we are inside, and how deep in elements of its name
    depth = 0
    for found in _PIECES.finditer(page):
        piece = found.group()
        if piece[0] == "<":
            tag = _TAG.match(piece)
            if tag and not piece.endswith("/>"):
                closing, name = tag.group(1) == "/", tag.group(2).lower()
                if held:
                    if name == held:
                        depth += -1 if closing else 1
                        if not depth:
                            held = ""
                elif not closing and (name in _ONE_LINE or _ONE_LINE_CLASS.search(piece)):
                    held, depth = name, 1
        elif not held and not piece.isascii():
            piece = mark(html.unescape(piece), lang)
        out.append(piece)
    return "".join(out)


def mark(text: str, lang: str) -> str:
    """``text`` escaped for HTML, with ``<wbr>`` between its phrases when it is Japanese or Chinese.
    ``lang``: the page's language, which says what text in kanji alone is."""
    if len(text) <= SHORT:
        return html.escape(text)
    if not text.isascii():
        text = AFTER_BRACKET.sub("\u2060", HELD_SPACE.sub("\u00a0", LATIN_WORDS.sub(_one_line, text)))
    return _marked(text, _language(text, lang))


def _one_line(words: re.Match) -> str:
    name = words.group()
    return name.replace(" ", "\u00a0") if name.count(" ") == 1 and len(name) <= NAME else name


def _language(text: str, lang: str) -> str:
    """The model for ``text``: Japanese when it has kana, else for kanji the page's language (Chinese on a page
    in neither), else none ("")."""
    if any("\u3041" <= c <= "\u30ff" or "\u31f0" <= c <= "\u31ff" or "\uff66" <= c <= "\uff9f" for c in text):
        return "ja"
    if any("\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" for c in text):
        return lang if lang in MODELS else "zh-TW"
    return ""


@lru_cache(maxsize=2048)
def _marked(text: str, lang: str) -> str:
    if not lang:
        return html.escape(text)
    cuts = _cuts(text, _model(lang), THRESHOLD[lang])
    if lang == "zh-TW":
        _join_singles(text, cuts)
    edges = [0, *cuts, len(text)]
    chunks = [text[a:b] for a, b in zip(edges, edges[1:])]
    if lang == "ja":
        chunks = _join_kanji(chunks)
    out = [html.escape(chunks[0])] if chunks else []
    for before, chunk in zip(chunks, chunks[1:]):
        # No break inside a run of Latin letters and digits ("InfoQ"), nor next to a space (a break is there already,
        # or none may be) or a word joiner
        if (_latin(before[-1]) and _latin(chunk[0])) or _held(before[-1]) or _held(chunk[0]):
            out.append(html.escape(chunk))
        else:
            out.append("<wbr>" + html.escape(chunk))
    return "".join(out)


def _join_singles(text: str, cuts: dict[int, float]) -> None:
    """No chunk of one character (「每|小|時」, 「站，」, 「）」): it joins the chunk before it (「瀏覽器裡的」), or the
    one after it at the start, after punctuation, when it leads the words after it (``LEADS``: 「各特務」「並列」) or
    is the same character again (「看看」)."""
    while True:
        edges = [0, *cuts, len(text)]
        for a, b in reversed(list(zip(edges, edges[1:]))):  # from the end: 「與|各|特務」 -> 「與各特務」
            chunk = text[a:b].strip().rstrip(CLAUSE_ENDS)
            if len(chunk) > 1 or (a not in cuts and b not in cuts):
                continue
            forward = (
                a not in cuts
                or chunk in LEADS
                or text[a - 1] in CLAUSE_ENDS
                or text[a - 1].isspace()
                or text[a] == text[b : b + 1]  # 「看看吧」
            )
            del cuts[b if forward and b in cuts else a]
            break
        else:
            return


def _join_kanji(chunks: list[str]) -> list[str]:
    """Japanese: no break between two kanji (「架|空データ」 is one word; 「毎時|集めて」 may stay together)."""
    joined = chunks[:1]
    for chunk in chunks[1:]:
        if _kanji(joined[-1][-1]) and _kanji(chunk[0]):
            joined[-1] += chunk
        else:
            joined.append(chunk)
    return joined


def _kanji(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff" or "\u3400" <= char <= "\u4dbf" or char in "々〆"


def _held(char: str) -> bool:
    return char.isspace() or char == "\u2060"


def _latin(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "#-_./:'")


@cache
def _model(lang: str) -> dict:
    model = json.loads((PACKAGE / "phrases" / MODELS[lang]).read_text(encoding="utf-8"))
    model["_base"] = -sum(sum(group.values()) for group in model.values()) * 0.5
    return model


def _cuts(sentence: str, model: dict, threshold: float = 0) -> dict[int, float]:
    """BudouX's Parser.parse: a break before character i (and how sure the model is of it, the score) when the
    scores of the characters around it are above ``threshold``. In order."""
    get = lambda group, key: model.get(group, {}).get(key, 0)  # noqa: E731
    cuts = {}
    for i in range(1, len(sentence)):
        score = model["_base"]
        if i > 2:
            score += get("UW1", sentence[i - 3])
        if i > 1:
            score += get("UW2", sentence[i - 2])
        score += get("UW3", sentence[i - 1])
        score += get("UW4", sentence[i])
        if i + 1 < len(sentence):
            score += get("UW5", sentence[i + 1])
        if i + 2 < len(sentence):
            score += get("UW6", sentence[i + 2])
        if i > 1:
            score += get("BW1", sentence[i - 2 : i])
        score += get("BW2", sentence[i - 1 : i + 1])
        if i + 1 < len(sentence):
            score += get("BW3", sentence[i : i + 2])
        if i > 2:
            score += get("TW1", sentence[i - 3 : i])
        if i > 1:
            score += get("TW2", sentence[i - 2 : i + 1])
        if i + 1 < len(sentence):
            score += get("TW3", sentence[i - 1 : i + 2])
        if i + 2 < len(sentence):
            score += get("TW4", sentence[i : i + 3])
        if score > threshold:
            cuts[i] = score
    return cuts
