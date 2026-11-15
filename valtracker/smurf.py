"""トラッカーから見える情報で、サブ垢（スマーフ）か本垢かを推測する。あくまで推測なので、
ページにも理由と一緒にそう書く（どのサインも単独では何も証明しない）。

サブ垢のサイン（それぞれ点数つき）:
- ランクの割にアカウントレベルが低い（レベルはプレイ時間、ランクは腕前で上がる）
- そのランクまでのランク戦のアクトが少ない
- 勝率が 5 割を大きく超える（本来のランクへ上がっている途中）
- 同じランク帯の中で成績が飛び抜けている（VAL スコア）

本垢はその逆（レベルもアクトも多く、勝率は 5 割前後）。レベルを隠していれば残りのサインで推す。ランクが無いか、
レベルもアクト数も最近の試合も無いときは推測しない。"""

from __future__ import annotations

#: ページに出す理由（訳は locale.py）。
NEW_ACCOUNT = "レベル {level} でこのランク"
LOW_LEVEL = "ランクの割にレベルが低い（{level}）"
FEW_ACTS = "ランク戦は {count} アクトだけ"
WINNING = "最近の勝率 {rate}%"
ABOVE_RANK = "同じランク帯の中で VAL スコア {score}"
FITS = "レベル {level}・ランクに見合った成績"
FITS_ACTS = "ランク戦 {count} アクト・ランクに見合った成績"
#: 理由の全部（tests/test_i18n.py が使う）。
WORDS = (NEW_ACCOUNT, LOW_LEVEL, FEW_ACTS, WINNING, ABOVE_RANK, FITS, FITS_ACTS)
#: 「サブ垢の可能性: 高」「あり」になる点数。
LIKELY, MAYBE = 3, 2


def guess(
    tier: int,
    level: int | None,
    acts: int | None = None,
    games: int = 0,
    win_rate: float | None = None,
    score: int | None = None,
) -> dict | None:
    """``{"kind": "likely" | "maybe" | "main", "why": [(日本語の文, 値)]}``、材料が無ければ None。
    ``tier``: 今のランク、``level``: アカウントレベル（隠していれば None）、``acts``: ランク戦をしたアクトの数、
    ``games``/``win_rate``: 最近の試合、``score``: VAL スコアの平均（同じランク帯が基準）。"""
    if tier <= 2 or (not level and acts is None and games < 10):
        return None
    points, why = 0, []
    if not level:
        pass
    elif (tier >= 15 and level < 50) or (tier >= 9 and level < 30):
        points += 2
        why.append((NEW_ACCOUNT, {"level": level}))
    elif tier >= 15 and level < 100:
        points += 1
        why.append((LOW_LEVEL, {"level": level}))
    if acts is not None and acts <= 2 and tier >= 12:
        points += 1
        why.append((FEW_ACTS, {"count": acts}))
    if win_rate is not None and games >= 10:
        if win_rate >= 75:
            points += 2
            why.append((WINNING, {"rate": round(win_rate)}))
        elif win_rate >= 65:
            points += 1
            why.append((WINNING, {"rate": round(win_rate)}))
    if score is not None and score >= 750:
        points += 1
        why.append((ABOVE_RANK, {"score": score}))
    if points >= LIKELY:
        kind = "likely"
    elif points >= MAYBE:
        kind = "maybe"
    else:
        kind = "main"
        if not why:
            why = [(FITS, {"level": level})] if level else [(FITS_ACTS, {"count": acts if acts is not None else "–"})]
    return {"kind": kind, "why": why}
