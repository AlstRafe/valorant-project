"""プレイヤーのシーズン（/p/<name>/<tag>/seasons）: アクトごとの戦績と数値、前のアクトからの変化。
HenrikDev が持っているそのモードの全試合から（henrik.stored）。tracker.gg のシーズン成績のようなもの。

Riot には過去のアクトを公開で取れる口が無い。HenrikDev は自分が読んだ試合しか持たない（ドキュメントによれば履歴の一部で
、増えていく）ので、数えた試合数をページに書く。ページはリンクからだけ開くので、プレイヤーページの呼び出しは増えない。"""

from __future__ import annotations

import itertools
import re

from flask import Flask, abort, current_app, render_template, request

from valtracker.henrik import MODES, STORED_MODES, HenrikError, split_riot_id
from valtracker.views import QUEUES, agent_info, failure_info, peak_info, rank_info, tr

#: アクトごとに出すエージェント数（多く使った順）。
AGENTS_SHOWN = 3
#: 前のアクトと比べる数値と、その変化の小数の桁数。
CHANGES = {"win_rate": 0, "kd": 2, "hs": 1, "adr": 0, "acs": 0}


def act_name(short: str) -> str:
    """HenrikDev のアクトの短い名前（"e9a3"）-> "E9 A3"。"""
    return re.sub(r"(\d)a(\d)", r"\1 A\2", short).upper()


def numbers(matches: list[dict]) -> dict:
    """保存済み試合（henrik._slim_stored）の戦績と平均。"""
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
        "kda": round((total["kills"] + total["assists"]) / deaths, 2)
        if deaths
        else float(total["kills"] + total["assists"]),
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
    """試合のあるアクト（新しい順）と、その数値、前のアクトからの変化。"""
    by_act: dict[str, list] = {}
    for match in matches:  # 新しい順: アクトも同じ順
        if match["season"]:
            by_act.setdefault(match["season"], []).append(match)
    acts = [{"key": key, "name": act_name(key)} | numbers(listed) for key, listed in by_act.items()]
    for act, before in itertools.pairwise(acts):
        act["change"] = {
            key: round(act[key] - before[key], places)
            for key, places in CHANGES.items()
            if act[key] is not None and before[key] is not None
        }
    return acts


def _named(act: dict, mode: str) -> dict:
    """アクトのエージェントと最高ランクに、訪問者の言語の名前と絵を付ける。"""
    act["agents"] = [agent | {"agent": agent_info({"id": agent["id"]})} for agent in act["agents"]]
    act["peak_rank"] = rank_info({"id": act["peak"]}) if mode == "competitive" and act["peak"] > 2 else None
    return act


def register(app: Flask) -> None:
    @app.get("/p/<name>/<tag>/seasons")
    def seasons_page(name: str, tag: str):
        if not split_riot_id(f"{name}#{tag}"):
            abort(404)
        mode = request.args.get("mode", "competitive")
        mode = mode if mode in STORED_MODES else "competitive"
        modes = [{"key": key, "name": tr(QUEUES[MODES[key]]), "current": key == mode} for key in STORED_MODES]
        values = {"name": name, "tag": tag, "modes": modes, "acts": [], "total": None, "problem": ""}
        values["places"] = CHANGES
        henrik = current_app.config["VAL_HENRIK"]
        try:
            account = henrik.account(name, tag)
        except HenrikError as error:
            status, values["problem"], _ = failure_info(error.kind)
            return render_template("seasons.html", **values), status
        values["name"], values["tag"] = str(account.get("name") or name), str(account.get("tag") or tag)
        try:
            stored = henrik.stored(str(account.get("region") or "ap"), values["name"], values["tag"], mode)
        except HenrikError as error:
            if error.kind != "notfound":
                status, values["problem"], _ = failure_info(error.kind)
                return render_template("seasons.html", **values), status
            stored = []  # プレイヤーはいるが、HenrikDev がこのモードの試合を 1 つも持っていない
        values["acts"] = [_named(act, mode) for act in seasons(stored)]
        counted = [match for match in stored if match["season"]]
        values["total"] = _named(numbers(counted), mode) if counted else None
        if values["total"] and mode == "competitive":
            # 通算カード: 今までの最高ランク（プレイヤーページと同じ）。保存済み試合の中の最高ではない
            try:
                ever = peak_info(henrik.mmr(str(account.get("region") or "ap"), values["name"], values["tag"]))
            except HenrikError:
                ever = None
            if ever and ever["tier"] >= values["total"]["peak"]:
                values["total"]["peak_rank"] = ever
        return render_template("seasons.html", **values)
