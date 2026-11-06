"""A player's seasons (/p/<name>/<tag>/seasons): each act's record and numbers, with the change from the act
before, from every match of a mode that HenrikDev keeps of them (henrik.stored), like tracker.gg's season stats.

Riot has no public source of a player's past acts. HenrikDev keeps only the matches it has read (its documentation:
a growing part of the history, not all of it), so the page says how many matches it counted. The page is asked for
only from its link, so the player page costs no more calls.
"""

from __future__ import annotations

import re

from flask import Flask, abort, current_app, render_template, request

from valtracker.henrik import MODES, STORED_MODES, HenrikError, split_riot_id
from valtracker.views import QUEUES, _agent, _failure, _rank, _t

#: The agents shown for each act, the most played first.
AGENTS_SHOWN = 3
#: The numbers compared with the act before, and how many decimals their change has.
CHANGES = {"win_rate": 0, "kd": 2, "hs": 1, "adr": 0, "acs": 0}


def act_name(short: str) -> str:
    """HenrikDev's short name of an act ("e9a3") -> "E9 A3"."""
    return re.sub(r"(\d)a(\d)", r"\1 A\2", short).upper()


def numbers(matches: list[dict]) -> dict:
    """The record and averages of some stored matches (henrik._slim_stored)."""
    games = len(matches)
    wins = sum(match["won"] is True for match in matches)
    losses = sum(match["won"] is False for match in matches)
    total = {key: sum(match[key] for match in matches) for key in ("rounds", "kills", "deaths", "assists", "score")}
    total |= {key: sum(match[key] for match in matches) for key in ("head", "body", "leg", "made", "received")}
    shots = total["head"] + total["body"] + total["leg"]
    rounds, deaths = total["rounds"], total["deaths"]
    played: dict[str, list] = {}
    for match in matches:
        if match["agent"]:
            played.setdefault(match["agent"], []).append(match)
    agents = sorted(played.items(), key=lambda item: (-len(item[1]), item[0]))[:AGENTS_SHOWN]
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100 * wins / games) if games else 0,
        "kd": round(total["kills"] / deaths, 2) if deaths else float(total["kills"]),
        "kda": round((total["kills"] + total["assists"]) / deaths, 2) if deaths else float(total["kills"]),
        "kills": round(total["kills"] / games, 1) if games else 0,
        "hs": round(100 * total["head"] / shots, 1) if shots else None,
        "adr": round(total["made"] / rounds) if rounds else None,
        "acs": round(total["score"] / rounds) if rounds else None,
        "dd": round((total["made"] - total["received"]) / rounds) if rounds else None,
        "peak": max((match["tier"] for match in matches), default=0),
        "agents": [
            {
                "id": agent,
                "games": len(listed),
                "win_rate": round(100 * sum(match["won"] is True for match in listed) / len(listed)),
            }
            for agent, listed in agents
        ],
    }


def seasons(matches: list[dict]) -> list[dict]:
    """Each act with matches, the latest first, with its numbers and their change from the act before it."""
    by_act: dict[str, list] = {}
    for match in matches:  # newest first: so are the acts
        if match["season"]:
            by_act.setdefault(match["season"], []).append(match)
    acts = [{"key": key, "name": act_name(key)} | numbers(listed) for key, listed in by_act.items()]
    for act, before in zip(acts, acts[1:], strict=False):
        act["change"] = {
            key: round(act[key] - before[key], places)
            for key, places in CHANGES.items()
            if act[key] is not None and before[key] is not None
        }
    return acts


def _named(act: dict, mode: str) -> dict:
    """An act's agents and peak rank with their names and pictures in the visitor's language."""
    act["agents"] = [agent | {"agent": _agent({"id": agent["id"]})} for agent in act["agents"]]
    act["peak_rank"] = _rank({"id": act["peak"]}) if mode == "competitive" and act["peak"] > 2 else None
    return act


def register(app: Flask) -> None:
    @app.get("/p/<name>/<tag>/seasons")
    def seasons_page(name: str, tag: str):
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        mode = request.args.get("mode", "competitive")
        mode = mode if mode in STORED_MODES else "competitive"
        modes = [{"key": key, "name": _t(QUEUES[MODES[key]]), "current": key == mode} for key in STORED_MODES]
        values = {"name": name, "tag": tag, "mode": mode, "modes": modes, "acts": [], "total": None, "problem": ""}
        values["places"] = CHANGES
        henrik = current_app.config["VAL_HENRIK"]
        try:
            account = henrik.account(name, tag)
        except HenrikError as error:
            status, values["problem"], _ = _failure(error.kind)
            return render_template("seasons.html", **values), status
        values["name"], values["tag"] = str(account.get("name") or name), str(account.get("tag") or tag)
        try:
            stored = henrik.stored(str(account.get("region") or "ap"), values["name"], values["tag"], mode)
        except HenrikError as error:
            if error.kind != "notfound":
                status, values["problem"], _ = _failure(error.kind)
                return render_template("seasons.html", **values), status
            stored = []  # the player is there, but HenrikDev keeps none of their matches of this mode
        values["acts"] = [_named(act, mode) for act in seasons(stored)]
        counted = [match for match in stored if match["season"]]
        values["total"] = _named(numbers(counted), mode) if counted else None
        return render_template("seasons.html", **values)
