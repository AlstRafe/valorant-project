"""1000 点満点の試合スコアと、プレイヤーの数値の「上位 X%」。どちらも、
このサイトが見た試合での同じランク帯のプレイヤーの成績と比べる（activity.py の物差し）。ゴールドはゴールドと、
レディアントは上位ランクと比べることになる。

試合のスコア: ACS・KAST・DDΔ（ラウンドあたりの与ダメージ − 被ダメージ）がその成績の中のどこにいるか（0 は全員より下、
1 は全員より上）と勝敗を 30 / 25 / 25 / 20 で重み付けする:

score = 1000 × (0.30 × ACS + 0.25 × KAST + 0.25 × DDΔ + 0.20 × 勝敗) / わかっているものの重みの合計

帯の真ん中で勝率 5 割なら約 500。評価: 800 から S、650 から A、500 から B、350 から C、それ未満は D。
重みと評価はページに書いてあるので数字を確かめられる。物差しの成績が activity.LINES_ENOUGH 件未満ならスコアは出さない。"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

#: 各要素の重み。
WEIGHTS = {"acs": 0.30, "kast": 0.25, "dd": 0.25, "won": 0.20}
#: （下限, 評価）、良い順。
GRADES = ((800, "S"), (650, "A"), (500, "B"), (350, "C"), (0, "D"))


def standing(values: list, value) -> float | None:
    """並べた ``values`` の中で ``value`` がどこにいるか: それより下の割合、同じ値は半分として数える（0〜1）。"""
    if value is None or not values:
        return None
    low, high = bisect_left(values, value), bisect_right(values, value)
    return (low + (high - low) / 2) / len(values)


def match_score(line: dict, won: float | None, stick: dict) -> int | None:
    """プレイヤーの成績（stats.line）、結果（1、引き分け 0.5、0。デスマッチのように無ければ None）、
    物差し（``{stat: 並べた値}``）からの試合スコア（0〜1000）。物差しが無い、またはわかっている重みが半分未満なら None。"""
    if not stick:
        return None
    total, weight = 0.0, 0.0
    for key in ("acs", "kast", "dd"):
        found = standing(stick.get(key) or [], line.get(key))
        if found is not None:
            total += WEIGHTS[key] * found
            weight += WEIGHTS[key]
    if won is not None:
        total += WEIGHTS["won"] * won
        weight += WEIGHTS["won"]
    if weight < 0.5:
        return None
    return round(1000 * total / weight)


def grade(score: int | None) -> str:
    if score is None:
        return ""
    return next(letter for low, letter in GRADES if score >= low)


def top(standings: list) -> int | None:
    """複数の試合での順位（平均）からの「上位 X%」: 一番良くて 1、一番悪くて 100。"""
    known = [value for value in standings if value is not None]
    if not known:
        return None
    return max(1, min(100, round(100 * (1 - sum(known) / len(known)))))
