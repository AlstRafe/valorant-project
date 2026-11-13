"""VALORANT's crosshair codes (Settings -> Crosshair -> Import profile code): read into their settings and drawn as
SVG, here on the server, so a code can be seen and checked before it is pasted into the game.

A code is ``0;P;c;5;h;0;0l;4;...``: a version, then ``P`` (primary), ``A`` (aim down sights) and ``S`` (sniper
scope) sections of key;value pairs. A key that is not given keeps the game's default; one we do not know is kept
and listed as it is. Lines are in the game's pixels at 1080p.

The drawing is the crosshair at rest (standing still, not firing). What makes it move in the game is read too and
told in the page's table: each ring's movement error (``0m``, ``1m``) and firing error (``0f``, ``1f``) with their
multipliers (``0s``, ``0e``, ``1s``, ``1e``), the fade while firing (``f``), the firing error's offset taken from
the crosshair's (``m``) and whether a watched player's crosshair is shown (``s``).
"""

from __future__ import annotations

import re

#: The game's colour choices (``c``); 8 is the custom colour given by ``u`` (RRGGBBAA).
COLOURS = ("#ffffff", "#00ff00", "#7fff00", "#dfff00", "#ffff00", "#00ffff", "#ff00ff", "#ff0000")
#: Their names, translated in locale.py.
COLOUR_NAMES = ("白", "緑", "黄緑", "緑黄", "黄", "シアン", "ピンク", "赤", "カスタム")
#: A code has only these characters, and is at most this long.
CODE = re.compile(r"^[0-9A-Za-z;.\-]{1,600}$")
#: The game's defaults (a crosshair given as "0" is the default one).
PRIMARY = {
    "c": 0,
    "u": "",
    "h": 1,
    "t": 1,
    "o": 0.5,
    "d": 0,
    "z": 2,
    "a": 1.0,
    "f": 1,
    "s": 1,
    "m": 0,
    "0b": 1,
    "0t": 2,
    "0l": 6,
    "0v": 6,
    "0g": 0,
    "0o": 3,
    "0a": 0.8,
    "0m": 0,
    "0f": 1,
    "0s": 1,
    "0e": 1,
    "1b": 1,
    "1t": 2,
    "1l": 2,
    "1v": 2,
    "1g": 0,
    "1o": 10,
    "1a": 0.35,
    "1m": 1,
    "1f": 1,
    "1s": 1,
    "1e": 1,
}
SNIPER = {"c": 7, "u": "", "d": 1, "s": 1.0, "o": 0.75}
SECTIONS = {"P": "primary", "A": "ads", "S": "sniper"}
#: How far the drawing reaches from the centre (pixels): every line fits, however long.
REACH = 50


def _value(text: str):
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text else number


def parse(code: str) -> dict | None:
    """``{"primary": settings, "ads": settings or None (the primary's), "sniper": settings, "unknown": [keys]}``,
    or None for what is not a crosshair code."""
    code = re.sub(r"\s+", "", str(code or ""))
    if not CODE.match(code):
        return None
    parts = code.split(";")
    if not parts or not re.fullmatch(r"\d+", parts[0]):
        return None
    found: dict[str, dict] = {"primary": {}, "ads": {}, "sniper": {}, "general": {}}
    section = "general"
    rest = parts[1:]
    index = 0
    while index < len(rest):
        key = rest[index]
        if key in SECTIONS:
            section = SECTIONS[key]
            index += 1
            continue
        if index + 1 >= len(rest):
            return None  # a key without its value
        found[section][key] = _value(rest[index + 1])
        index += 2
    unknown = sorted(
        {
            f"{section[0].upper()}:{key}"
            for section in ("primary", "ads")
            for key in found[section]
            if key not in PRIMARY
        }
        | {f"S:{key}" for key in found["sniper"] if key not in SNIPER}
    )
    # the ADS profile is the primary unless it was set on its own ("p;0" in the general part, or an A section)
    own_ads = bool(found["ads"]) or str(found["general"].get("p", 1)) == "0"
    return {
        "primary": PRIMARY | found["primary"],
        "ads": (PRIMARY | found["ads"]) if own_ads else None,
        "sniper": SNIPER | found["sniper"],
        "unknown": unknown,
    }


def colour(settings: dict) -> str:
    """The crosshair's colour as #rrggbb."""
    index = _int(settings.get("c"))
    custom = str(settings.get("u") or "")
    if index == 8 and re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", custom):
        return "#" + custom[:6].lower()
    return COLOURS[index] if 0 <= index < len(COLOURS) else COLOURS[0]


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _size(value, low: int = 0, high: int = 40) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def shapes(settings: dict) -> list[dict]:
    """The crosshair as rectangles around the centre (0, 0): ``[{"x", "y", "w", "h", "fill", "opacity"}]``,
    outlines first (they are drawn under the lines)."""
    fill = colour(settings)
    outline = _size(settings.get("t"), 0, 6) if _int(settings.get("h")) else 0
    outline_opacity = _float(settings.get("o"), 0.5)
    lines: list[tuple[dict, float]] = []
    if _int(settings.get("d")):
        dot = _size(settings.get("z"), 1, 6)
        lines.append(({"x": -dot / 2, "y": -dot / 2, "w": dot, "h": dot}, _float(settings.get("a"), 1.0)))
    for ring in ("0", "1"):
        if not _int(settings.get(ring + "b")):
            continue
        thick = _size(settings.get(ring + "t"), 0, 10)
        length = _size(settings.get(ring + "l"), 0, 20)
        tall = _size(settings.get(ring + "v"), 0, 20) if _int(settings.get(ring + "g")) else length
        gap = _size(settings.get(ring + "o"), 0, 20)
        opacity = _float(settings.get(ring + "a"), 1.0)
        if not thick or not (length or tall):
            continue
        half = thick / 2
        if length:
            lines.append(({"x": gap, "y": -half, "w": length, "h": thick}, opacity))
            lines.append(({"x": -gap - length, "y": -half, "w": length, "h": thick}, opacity))
        if tall:
            lines.append(({"x": -half, "y": gap, "w": thick, "h": tall}, opacity))
            lines.append(({"x": -half, "y": -gap - tall, "w": thick, "h": tall}, opacity))
    drawn = []
    if outline:
        for box, opacity in lines:
            drawn.append(
                {
                    "x": box["x"] - outline,
                    "y": box["y"] - outline,
                    "w": box["w"] + 2 * outline,
                    "h": box["h"] + 2 * outline,
                    "fill": "#000000",
                    "opacity": round(outline_opacity * (1 if opacity else 0), 2),
                }
            )
    drawn += [dict(box, fill=fill, opacity=round(opacity, 2)) for box, opacity in lines]
    return drawn


def sniper_shapes(settings: dict) -> list[dict]:
    """The scope's centre dot, when it is on."""
    if not _int(settings.get("d")):
        return []
    size = 2 * _size(settings.get("s"), 0, 4) or 1
    return [
        {
            "x": -size / 2,
            "y": -size / 2,
            "w": size,
            "h": size,
            "fill": colour(settings),
            "opacity": _float(settings.get("o"), 0.75),
        }
    ]


def describe(settings: dict) -> dict:
    """What the settings page of the game would show, for the page's table."""
    index = _int(settings.get("c"))
    return {
        "colour": colour(settings),
        "colour_name": COLOUR_NAMES[index] if 0 <= index < len(COLOUR_NAMES) else COLOUR_NAMES[0],
        "outlines": (_int(settings.get("h")), _size(settings.get("t"), 0, 6), _float(settings.get("o"), 0.5)),
        "dot": (_int(settings.get("d")), _size(settings.get("z"), 1, 6), _float(settings.get("a"), 1.0)),
        "inner": _ring(settings, "0"),
        "outer": _ring(settings, "1"),
        "fade": bool(_int(settings.get("f"))),
    }


def _ring(settings: dict, ring: str) -> dict:
    return {
        "on": bool(_int(settings.get(ring + "b"))),
        "opacity": _float(settings.get(ring + "a"), 1.0),
        "length": _size(settings.get(ring + "l"), 0, 20),
        "vertical": _size(settings.get(ring + "v"), 0, 20) if _int(settings.get(ring + "g")) else None,
        "thickness": _size(settings.get(ring + "t"), 0, 10),
        "offset": _size(settings.get(ring + "o"), 0, 20),
        # whether the lines move apart in the game: while walking, and while firing
        "moving": bool(_int(settings.get(ring + "m"))),
        "firing": bool(_int(settings.get(ring + "f"))),
    }


def code(settings: dict) -> str:
    """The primary crosshair as a code again (only what differs from the default), e.g. for a preset."""
    parts = ["0", "P"]
    for key, default in PRIMARY.items():
        value = settings.get(key, default)
        if value != default:
            parts += [key, str(value)]
    return ";".join(parts)


#: A few starting points (not anyone's own crosshair): name -> primary settings.
PRESETS = {
    "ドット": {"c": 5, "h": 1, "t": 1, "o": 1, "d": 1, "z": 3, "0b": 0, "1b": 0},
    "小さい十字": {"c": 1, "h": 0, "0l": 3, "0t": 2, "0o": 2, "0a": 1, "1b": 0},
    "十字と点": {"c": 5, "h": 1, "o": 1, "d": 1, "z": 2, "0l": 4, "0t": 2, "0o": 3, "0a": 1, "1b": 0},
    "ゲームの初期設定": {},
}
