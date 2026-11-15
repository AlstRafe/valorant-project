"""プレイヤーのキルとデスの場所を、マップのミニマップに描く。

HenrikDev のキルには、やられた側が倒れた場所と、その瞬間に生きていた全員の立ち位置が入っている。
rounds.py はやられた側と倒した側のものをゲームの単位で残す。 valorant-api.com は各マップのミニマップ（``displayIcon``）
、ゲームの単位からの変換、コールアウト（場所の名前、訪問者の言語）を出す:

横 = ゲーム y * xMultiplier + xScalarToAdd、縦 = ゲーム x * yMultiplier + yScalarToAdd（0〜1）

キルはプレイヤーが立っていた場所、デスは倒れた場所に描く。それぞれ一番近いコールアウトの名前を付けるので、
点だけではわからない「どこで一番やられるか」をページに書ける。"""

from __future__ import annotations

#: キルとデスがこの数以上あるマップだけ描く。
ENOUGH = 5
#: マップの下に並べる場所の数（多い順）。
PLACES_SHOWN = 6
#: プレイヤーページに出すマップの数（キルとデスが多い順）。
MAPS_SHOWN = 6
NUMBERS = ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")


def plan(item) -> dict | None:
    """マップのミニマップ、数値、コールアウト（valorant-api.com のマップ）。それらが無いマップ（射撃場、スカーミッシュ、
    チームデスマッチのマップ）は None。"""
    if not isinstance(item, dict) or not item.get("displayIcon"):
        return None
    try:
        numbers = tuple(float(item[key]) for key in NUMBERS)
    except (KeyError, TypeError, ValueError):
        return None
    if not numbers[0] or not numbers[1]:
        return None
    callouts = []
    for callout in item.get("callouts") or []:
        where = callout.get("location") if isinstance(callout, dict) else None
        try:
            x, y = float(where["x"]), float(where["y"])
        except (KeyError, TypeError, ValueError):
            continue
        parts = (str(callout.get("superRegionName") or ""), str(callout.get("regionName") or ""))
        name = " ".join(part for part in parts if part)
        if name:
            callouts.append((name, x, y))
    return {"image": str(item["displayIcon"]), "numbers": numbers, "callouts": callouts}


def onto(drawn: dict, spot) -> tuple[float, float] | None:
    """ミニマップ上の位置（[ゲーム x, ゲーム y]）を横・縦のパーセントで。絵の外なら None。"""
    if not spot:
        return None
    x_times, y_times, x_plus, y_plus = drawn["numbers"]
    across = (spot[1] * x_times + x_plus) * 100
    down = (spot[0] * y_times + y_plus) * 100
    if not (-1 <= across <= 101 and -1 <= down <= 101):
        return None
    return round(across, 1), round(down, 1)


def callout(drawn: dict, spot) -> str:
    """その位置に一番近いコールアウトの名前（コールアウトの無いマップは ""）。"""
    nearest = min(drawn["callouts"], key=lambda item: (item[1] - spot[0]) ** 2 + (item[2] - spot[1]) ** 2, default=None)
    return nearest[0] if nearest else ""


def points(match: dict, mine: int) -> list[dict]:
    """試合の ``players`` の ``mine`` 番目のプレイヤーのキルとデス（ダイジェスト、 rounds.py から）
    : それぞれ ``{"kind": "k" か "d", "at": 位置, "other": 相手の位置, "side": "atk"/"def"/"", "round", "first":
    ラウンド最初のキル}``。味方を倒したキルは省き、味方にやられたデスは残す。"""
    digest = match.get("digest") or {}
    kills = digest.get("kills") or []
    rounds = digest.get("rounds") or []
    team = [str(player.get("team_id") or "") for player in match.get("players") or [] if isinstance(player, dict)]
    if not 0 <= mine < len(team):
        return []
    found, opened = [], set()
    for kill in kills:
        first = kill[0] not in opened
        opened.add(kill[0])
        if len(kill) < 8 or not 0 <= kill[3] < len(team):
            continue  # 場所を持つ前に保存したもの（rounds.py）、または誰のものでもない
        attack = rounds[kill[0]].get("a") if 0 <= kill[0] < len(rounds) else ""
        side = ("atk" if attack == team[mine] else "def") if attack and team[mine] else ""
        victim_at, killer_at = kill[6], kill[7]
        if kill[2] == mine and kill[3] != mine and team[kill[3]] != team[mine] and killer_at:
            found.append(
                {"kind": "k", "at": killer_at, "other": victim_at, "side": side, "round": kill[0] + 1, "first": first}
            )
        elif kill[3] == mine and victim_at:
            found.append(
                {"kind": "d", "at": victim_at, "other": killer_at, "side": side, "round": kill[0] + 1, "first": first}
            )
    return found


def drawing(drawn: dict, spots: list[dict], lines: bool = False, most: int | None = None) -> dict | None:
    """ページが描くもの: ミニマップ、キル・デスごとの点（``lines`` なら相手の位置への線も）、多い場所とそこでのキル・
    デス・ファーストデス。少なすぎれば None。 ``most``: 描く点の最大数（``spots`` の先頭＝新しいものから）。
    数えるのは全部。"""
    dots, places = [], {}
    for spot in spots:
        at = onto(drawn, spot["at"])
        if at is None:
            continue
        other = onto(drawn, spot["other"]) if lines else None
        name = callout(drawn, spot["at"])
        dots.append(
            {
                "x": at[0],
                "y": at[1],
                "kind": spot["kind"],
                "side": spot["side"],
                "first": spot["first"],
                "round": spot["round"],
                "place": name,
                "line": other,
            }
        )
        if name:
            counted = places.setdefault(name, {"name": name, "kills": 0, "deaths": 0, "first_deaths": 0})
            counted["kills" if spot["kind"] == "k" else "deaths"] += 1
            counted["first_deaths"] += 1 if spot["kind"] == "d" and spot["first"] else 0
    if len(dots) < ENOUGH:
        return None
    busiest = sorted(places.values(), key=lambda place: (-(place["kills"] + place["deaths"]), place["name"]))
    for place in busiest:
        place["diff"] = place["kills"] - place["deaths"]
    worst = max(busiest, key=lambda place: (place["deaths"] - place["kills"], place["deaths"]), default=None)
    return {
        "image": drawn["image"],
        "dots": dots[:most] if most else dots,
        "kills": sum(dot["kind"] == "k" for dot in dots),
        "deaths": sum(dot["kind"] == "d" for dot in dots),
        "drawn": min(len(dots), most or len(dots)),
        "sides": bool({dot["side"] for dot in dots} - {""}),
        "places": busiest[:PLACES_SHOWN],
        # プレイヤーが撃ち合いに一番負ける場所: 角度を変えるか、先にユーティリティを使う価値がある
        "worst": worst if worst and worst["deaths"] - worst["kills"] >= 2 else None,
    }
