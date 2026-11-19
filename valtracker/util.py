"""あちこちのモジュールで使う小さな変換。API の答えは型があてにならないので、壊れた値は既定値にする。"""

from __future__ import annotations

import base64
import json
import math
import re

#: Riot の ID（puuid・試合・パーティー・アイテムの uuid、小文字）。比べるのは ``fullmatch`` で。
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: 時刻として読む unix 秒の上限（西暦 3000 年）: これより先の数は壊れた値（日付にすると OverflowError）。
LAST_MOMENT = 32_503_680_000


def to_int(value, default: int = 0) -> int:
    """整数に。"12"・12.7・"12.7" も読む（小数は切り捨て）。読めないもの（None・""・"abc"・nan）は ``default``。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def to_float(value, default: float = 0.0) -> float:
    """小数に。読めないもの（nan・inf も: そのあとの int() や round() で落ちる）は ``default``。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def as_list(value) -> list:
    """API の答えの一覧。null や型の違うもの（文字列、数、辞書）は空の一覧に（for で落ちないように）。"""
    return value if isinstance(value, list) else []


def as_dict(value) -> dict:
    """API の答えの辞書。null や型の違うものは空の辞書に（.get で落ちないように）。"""
    return value if isinstance(value, dict) else {}


def lower_id(value) -> str:
    """uuid などの ID を比べられる形に（小文字の文字列、無ければ ""）。"""
    return str(value or "").lower()


def token_claims(token: str) -> dict:
    """JWT（アクセストークン）の中身。署名は確かめない（確かめるのは Riot、こちらは puuid や期限を読むだけ）。"""
    try:
        middle = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except (IndexError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
