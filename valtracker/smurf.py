"""A guess at whether an account is someone's second account (a "smurf": サブ垢) or their main one, from what a
tracker can see. It is a guess, and the page says so with its reasons: no single sign proves anything.

Signs of a second account, each with its weight:
- a low account level for the rank (a new account already high: levels come with time played, ranks with skill)
- few acts of ranked play behind the rank
- winning much more than half of the matches (a rank still climbing to where the player belongs)
- numbers far above the players of the same rank (the VAL score, measured against the same rank band)

A main account is the other way round: many levels and acts, a win rate near half. With too little to go on
(no rank, no level) there is no guess.
"""

from __future__ import annotations

#: The reasons, as the page words them (translated in locale.py).
NEW_ACCOUNT = "レベル {level} でこのランク"
LOW_LEVEL = "ランクの割にレベルが低い（{level}）"
FEW_ACTS = "ランク戦は {count} アクトだけ"
WINNING = "最近の勝率 {rate}%"
ABOVE_RANK = "同じランク帯の中で VAL スコア {score}"
FITS = "レベル {level}・ランクに見合った成績"
#: Every reason (tests/test_i18n.py).
WORDS = (NEW_ACCOUNT, LOW_LEVEL, FEW_ACTS, WINNING, ABOVE_RANK, FITS)
#: Points from which the guess says "likely a second account", and "maybe".
LIKELY, MAYBE = 3, 2


def guess(
    tier: int,
    level: int | None,
    acts: int | None = None,
    games: int = 0,
    win_rate: float | None = None,
    score: int | None = None,
) -> dict | None:
    """``{"kind": "likely" | "maybe" | "main", "why": [(Japanese words, values)]}`` or None (nothing to go on).
    ``tier``: the rank now; ``level``: the account level (None when hidden); ``acts``: acts with ranked matches;
    ``games``/``win_rate``: recent matches; ``score``: the average VAL score (the same rank band's yardstick)."""
    if tier <= 2 or not level:
        return None
    points, why = 0, []
    if tier >= 15 and level < 50:
        points += 2
        why.append((NEW_ACCOUNT, {"level": level}))
    elif tier >= 9 and level < 30:
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
        why = [(FITS, {"level": level})] if not why else why
    return {"kind": kind, "why": why}
