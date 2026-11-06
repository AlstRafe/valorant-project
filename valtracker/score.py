"""A match's score out of 1000, and "top X%" for a player's numbers: each measured against the lines of the players
of the same rank band in the matches this site has seen (activity.py's yardstick), so a Gold player is measured
against Gold players and a Radiant against the top ranks.

The score of a match: where the player's ACS, KAST and DDΔ (damage dealt less taken, per round) stand among those
lines (0 = below every one, 1 = above every one), and whether the team won, weighed 30 / 25 / 25 / 20:

    score = 1000 × (0.30 × ACS + 0.25 × KAST + 0.25 × DDΔ + 0.20 × win) / the weights of what is known

so a player in the middle of their band who wins half their games scores about 500. The grade: S from 800, A from
650, B from 500, C from 350, else D. The weights and the grades are on the page, so the number can be checked; a
score is not shown when the yardstick has fewer than activity.LINES_ENOUGH lines.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

#: How much each part counts.
WEIGHTS = {"acs": 0.30, "kast": 0.25, "dd": 0.25, "won": 0.20}
#: (from, grade), best first.
GRADES = ((800, "S"), (650, "A"), (500, "B"), (350, "C"), (0, "D"))


def standing(values: list, value) -> float | None:
    """Where ``value`` stands among the sorted ``values``: the share below it, ties counted half (0..1)."""
    if value is None or not values:
        return None
    low, high = bisect_left(values, value), bisect_right(values, value)
    return (low + (high - low) / 2) / len(values)


def match_score(line: dict, won: float | None, stick: dict) -> int | None:
    """A match's score (0-1000) from the player's line (stats.line), the result (1, 0.5 for a draw, 0; None when
    there is none, as in Deathmatch) and the yardstick (``{stat: sorted values}``). None without a yardstick or
    with less than half the weights known."""
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
    """ "Top X%" from a number's standings in several matches (their average): 1 at best, 100 at worst."""
    known = [value for value in standings if value is not None]
    if not known:
        return None
    return max(1, min(100, round(100 * (1 - sum(known) / len(known)))))
