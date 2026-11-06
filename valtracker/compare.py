"""Two players side by side (/compare?a=Name%23TAG&b=Name%23TAG): their ranks and what their recent matches of
one mode say, the better of each line marked. Each player is read as on their own page (views._player), so a
player just looked at costs nothing more."""

from __future__ import annotations

from flask import Flask, render_template, request, url_for

from valtracker.henrik import MODES as HENRIK_MODES
from valtracker.henrik import RECENT, split_riot_id
from valtracker.views import MODE_FILTER, QUEUES, _from_link, _player, _t

#: The modes worth comparing: those with teams that win or lose (not Deathmatch).
MODES = tuple(mode for mode in MODE_FILTER if mode and mode != "deathmatch")
#: The lines from the matches: (key in stats.summary, its name, whether more is better (None: neither), unit).
LINES = (
    ("games", "試合数", None, ""),
    ("win_rate", "勝率", True, "%"),
    ("kd", "K/D", True, ""),
    ("kills", "平均キル", True, ""),
    ("deaths", "平均デス", False, ""),
    ("acs", "ACS", True, ""),
    ("adr", "ADR", True, ""),
    ("hs", "HS率", True, "%"),
    ("kast", "KAST", True, "%"),
    ("dd", "DDΔ", True, ""),
)


def _side(asked: str, mode: str, span: str = "") -> dict:
    """One player: ``{"asked", "found": the player page's values or None, "problem"}``; ``span`` "all": over
    their lifetime (views._lifetime)."""
    asked = _from_link(" ".join(asked.split()))[:40]
    parts = split_riot_id(asked)
    if not parts:
        problem = _t("「名前#タグ」の形で入れてください（例: Name#JP1）。") if asked else ""
        return {"asked": asked, "found": None, "problem": problem, "status": 400 if asked else 200}
    values, status = _player(parts[0], parts[1], mode, span=span)
    if not values.get("account"):
        return {"asked": asked, "found": None, "problem": values.get("problem") or "", "status": status}
    return {"asked": f"{values['name']}#{values['tag']}", "found": values, "problem": "", "status": 200}


def _number(value):
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _best(numbers: list, more: bool | None) -> int | None:
    """Which side (0 or 1) is better, or None for a tie, a missing number or a line with no better."""
    if more is None or any(number is None for number in numbers) or numbers[0] == numbers[1]:
        return None
    return 0 if (numbers[0] > numbers[1]) == more else 1


def _rows(found: list[dict]) -> list[dict]:
    tiers = [_number((values.get("rank") or {}).get("tier")) for values in found]
    peaks = [_number((values.get("peak") or {}).get("tier")) for values in found]
    rows = [
        {
            "name": _t("現在のランク"),
            "kind": "rank",
            "values": [v.get("rank") for v in found],
            "best": _best(tiers, True),
        },
        {
            "name": _t("最高ランク"),
            "kind": "rank",
            "values": [v.get("peak") for v in found],
            "best": _best(peaks, True),
        },
        {
            "name": _t("アカウントレベル"),
            "kind": "number",
            "values": [_number((v.get("account") or {}).get("account_level")) for v in found],
            "unit": "",
            "best": None,
        },
    ]
    summaries = [values.get("summary") or {} for values in found]
    for key, name, more, unit in LINES:
        numbers = [_number(summary.get(key)) for summary in summaries]
        if all(number is None for number in numbers):
            continue
        rows.append(
            {
                "name": _t(name),
                "kind": "number",
                "values": numbers,
                "unit": unit,
                "signed": key == "dd",
                "best": _best(numbers, more),
            }
        )
    rows.append(
        {
            "name": _t("よく使うエージェント"),
            "kind": "agents",
            "values": [summary.get("agents") or [] for summary in summaries],
            "best": None,
        }
    )
    return rows


def page() -> tuple[dict, int]:
    mode = request.args.get("mode", "competitive")
    mode = mode if mode in MODES else "competitive"
    span = "all" if request.args.get("span") == "all" else ""
    sides = [_side(request.args.get(key, ""), mode, span) for key in ("a", "b")]
    found = [side["found"] for side in sides]
    both = all(found)
    status = max(side["status"] for side in sides) if not both else 200
    wins = [0, 0]
    rows = _rows(found) if both else []
    for row in rows:
        if row["best"] is not None:
            wins[row["best"]] += 1
    return {
        "sides": sides,
        "rows": rows,
        "wins": wins,
        "recent": RECENT,
        "mode": mode,
        "modes": [{"key": key, "name": _t(QUEUES[HENRIK_MODES[key]])} for key in MODES],
        "span": span,
        "spans": [{"key": "", "name": _t("直近 15 試合")}, {"key": "all", "name": _t("通算")}],
        "swap_url": url_for(
            "compare",
            a=sides[1]["asked"],
            b=sides[0]["asked"],
            mode=mode if mode != "competitive" else None,
            span=span or None,
        )
        if both
        else "",
    }, status


def register(app: Flask) -> None:
    @app.get("/compare")
    def compare():
        values, status = page()
        return render_template("compare.html", **values), status
