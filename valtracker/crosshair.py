"""VALORANT のクロスヘアコード（設定 -> クロスヘア -> プロファイルコードをインポート）: 設定に読み直し、
サーバー側で SVG に描く。ゲームに貼る前にコードを見て確かめられるように。

コードは ``0;P;c;5;h;0;0l;4;...``: バージョンのあとに ``P``（プライマリー）、``A``（ADS）、 ``S``（スナイパースコープ）
の区切りが続き、中は key;value の組。書かれていないキーはゲームの既定値、知らないキーは残してそのまま一覧に出す。
線の長さは 1080p でのゲームのピクセル。

絵は止まっているとき（動かず撃たず）のクロスヘア。
ゲームで動かす設定も読んでページの表に書く: 各リングの移動エラー（``0m``、``1m``）と射撃エラー（``0f``、 ``1f``）
とその倍率（``0s``、``0e``、``1s``、``1e``）、射撃中のフェード（``f``）、
射撃エラーのオフセットをクロスヘアのものにするか（``m``）、観戦中のプレイヤーのクロスヘアを出すか（``s``）。"""

from __future__ import annotations

import re

from valtracker.util import to_int

#: ゲームの色の選択肢（``c``）。8 は ``u``（RRGGBBAA）で指定するカスタム色。
COLOURS = ("#ffffff", "#00ff00", "#7fff00", "#dfff00", "#ffff00", "#00ffff", "#ff00ff", "#ff0000")
#: その名前（訳は locale.py）。
COLOUR_NAMES = ("白", "緑", "黄緑", "緑黄", "黄", "シアン", "ピンク", "赤", "カスタム")
#: コードに使える文字と最大の長さ。
CODE = re.compile(r"[0-9A-Za-z;.\-]{1,600}")
#: ゲームの既定値（"0" のクロスヘアは既定のもの）。
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


def _value(text: str):
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text else number


def parse(code: str) -> dict | None:
    """``{"primary": 設定, "ads": 設定か None（プライマリーと同じ）, "sniper": 設定, "unknown": [キー]}``。
    クロスヘアコードでなければ None。"""
    code = re.sub(r"\s+", "", str(code or ""))
    if not CODE.fullmatch(code):
        return None
    parts = code.split(";")
    if not re.fullmatch(r"\d+", parts[0]):
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
            return None  # 値の無いキー
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
    # ADS のプロファイルは、別に設定していなければプライマリーと同じ（全般の "p;0"、または A の区切り）
    own_ads = bool(found["ads"]) or str(found["general"].get("p", 1)) == "0"
    return {
        "primary": PRIMARY | found["primary"],
        "ads": (PRIMARY | found["ads"]) if own_ads else None,
        "sniper": SNIPER | found["sniper"],
        "unknown": unknown,
    }


def colour(settings: dict) -> str:
    """クロスヘアの色を #rrggbb で。"""
    index = to_int(settings.get("c"))
    custom = str(settings.get("u") or "")
    if index == 8 and re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", custom):
        return "#" + custom[:6].lower()
    return COLOURS[index] if 0 <= index < len(COLOURS) else COLOURS[0]


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
    """中心 (0, 0) のまわりの四角形としてのクロスヘア: ``[{"x", "y", "w", "h", "fill", "opacity"}]``。
    アウトラインを先に（線の下に描く）。"""
    fill = colour(settings)
    outline = _size(settings.get("t"), 0, 6) if to_int(settings.get("h")) else 0
    outline_opacity = _float(settings.get("o"), 0.5)
    lines: list[tuple[dict, float]] = []
    if to_int(settings.get("d")):
        dot = _size(settings.get("z"), 1, 6)
        lines.append(({"x": -dot / 2, "y": -dot / 2, "w": dot, "h": dot}, _float(settings.get("a"), 1.0)))
    for ring in ("0", "1"):
        if not to_int(settings.get(ring + "b")):
            continue
        thick = _size(settings.get(ring + "t"), 0, 10)
        length = _size(settings.get(ring + "l"), 0, 20)
        tall = _size(settings.get(ring + "v"), 0, 20) if to_int(settings.get(ring + "g")) else length
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
    """スコープの中心の点（オンのとき）。"""
    if not to_int(settings.get("d")):
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
    """ゲームの設定画面に出るもの（ページの表用）。"""
    index = to_int(settings.get("c"))
    return {
        "colour": colour(settings),
        "colour_name": COLOUR_NAMES[index] if 0 <= index < len(COLOUR_NAMES) else COLOUR_NAMES[0],
        "outlines": (to_int(settings.get("h")), _size(settings.get("t"), 0, 6), _float(settings.get("o"), 0.5)),
        "dot": (to_int(settings.get("d")), _size(settings.get("z"), 1, 6), _float(settings.get("a"), 1.0)),
        "inner": _ring(settings, "0"),
        "outer": _ring(settings, "1"),
        "fade": bool(to_int(settings.get("f"))),
    }


def _ring(settings: dict, ring: str) -> dict:
    return {
        "on": bool(to_int(settings.get(ring + "b"))),
        "opacity": _float(settings.get(ring + "a"), 1.0),
        "length": _size(settings.get(ring + "l"), 0, 20),
        "vertical": _size(settings.get(ring + "v"), 0, 20) if to_int(settings.get(ring + "g")) else None,
        "thickness": _size(settings.get(ring + "t"), 0, 10),
        "offset": _size(settings.get(ring + "o"), 0, 20),
        # ゲームで線が開くか: 歩いているときと、撃っているとき
        "moving": bool(to_int(settings.get(ring + "m"))),
        "firing": bool(to_int(settings.get(ring + "f"))),
    }


def code(settings: dict) -> str:
    """プライマリーのクロスヘアをまたコードに（既定と違うところだけ）。プリセット用など。"""
    parts = ["0", "P"]
    for key, default in PRIMARY.items():
        value = settings.get(key, default)
        if value != default:
            parts += [key, str(value)]
    return ";".join(parts)


#: 出発点になるいくつか（誰かのクロスヘアではない）: 名前 -> プライマリーの設定。
PRESETS = {
    "ドット": {"c": 5, "h": 1, "t": 1, "o": 1, "d": 1, "z": 3, "0b": 0, "1b": 0},
    "小さい十字": {"c": 1, "h": 0, "0l": 3, "0t": 2, "0o": 2, "0a": 1, "1b": 0},
    "十字と点": {"c": 5, "h": 1, "o": 1, "d": 1, "z": 2, "0l": 4, "0t": 2, "0o": 3, "0a": 1, "1b": 0},
    "ゲームの初期設定": {},
}
