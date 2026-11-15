"""あちこちのモジュールで使う小さな変換。API の答えは型があてにならないので、壊れた値は既定値にする。"""

from __future__ import annotations


def to_int(value, default: int = 0) -> int:
    """整数に。"12"・12.7・"12.7" も読む（小数は切り捨て）。読めないもの（None・""・"abc"・nan）は ``default``。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def to_float(value, default: float = 0.0) -> float:
    """小数に。読めないものは ``default``。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def lower_id(value) -> str:
    """uuid などの ID を比べられる形に（小文字の文字列、無ければ ""）。"""
    return str(value or "").lower()
