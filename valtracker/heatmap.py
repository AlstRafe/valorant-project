"""Where a player's kills and deaths happened, drawn on the map's own minimap.

HenrikDev's kills carry where the victim died and where every living player stood at that moment; rounds.py keeps
the victim's and the killer's, in the game's units. valorant-api.com gives each map's minimap (``displayIcon``),
how the game's units go onto it, and its callouts (the places' names, in the visitor's language):

    across = game y * xMultiplier + xScalarToAdd,  down = game x * yMultiplier + yScalarToAdd   (0 to 1)

A kill is drawn where the player stood, a death where they died. Each is named after the nearest callout, so the
page can say where the player dies most, which the dots alone do not say.
"""

from __future__ import annotations

#: A map is drawn only with this many kills and deaths on it.
ENOUGH = 5
#: The named places listed under a map, the busiest first.
PLACES_SHOWN = 6
#: The player page's maps, the one with most kills and deaths first.
MAPS_SHOWN = 6
NUMBERS = ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")


def plan(item) -> dict | None:
    """A map's minimap, its numbers and callouts (valorant-api.com's map), or None for a map without them (the
    Range, Skirmish, the Team Deathmatch maps)."""
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
    """A spot ([game x, game y]) on the minimap, in percent across and down; None for one off the picture."""
    if not spot:
        return None
    x_times, y_times, x_plus, y_plus = drawn["numbers"]
    across = (spot[1] * x_times + x_plus) * 100
    down = (spot[0] * y_times + y_plus) * 100
    if not (-1 <= across <= 101 and -1 <= down <= 101):
        return None
    return round(across, 1), round(down, 1)


def callout(drawn: dict, spot) -> str:
    """The name of the callout nearest a spot ("" for a map without callouts)."""
    nearest = min(drawn["callouts"], key=lambda item: (item[1] - spot[0]) ** 2 + (item[2] - spot[1]) ** 2, default=None)
    return nearest[0] if nearest else ""


def points(match: dict, mine: int) -> list[dict]:
    """The kills and deaths of the player at place ``mine`` in a match's ``players``, from its digest (rounds.py):
    each ``{"kind": "k" or "d", "at": spot, "other": the other player's spot, "side": "atk"/"def"/"", "round",
    "first": the round's first kill}``. A kill of a team-mate is left out; a death to one is not."""
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
            continue  # kept before the places were (rounds.py), or nobody's
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


def drawing(drawn: dict, spots: list[dict], lines: bool = False) -> dict | None:
    """What the page draws: the minimap, a dot for each kill and death (with a line to the other player's spot
    when ``lines``), and the busiest named places with their kills, deaths and first deaths. None with too few."""
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
        "dots": dots,
        "kills": sum(dot["kind"] == "k" for dot in dots),
        "deaths": sum(dot["kind"] == "d" for dot in dots),
        "sides": bool({dot["side"] for dot in dots} - {""}),
        "places": busiest[:PLACES_SHOWN],
        # where the player loses the most duels: worth a different angle or a utility first
        "worst": worst if worst and worst["deaths"] - worst["kills"] >= 2 else None,
    }
