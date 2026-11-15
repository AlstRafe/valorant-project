"""HenrikDev v4 の試合から計算する数値: 試合でのプレイヤーの成績、スコアボード、まとめ。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from valtracker.util import to_int

JST = timezone(timedelta(hours=9))


def rounds_played(match: dict) -> int:
    teams = [team for team in match.get("teams") or [] if isinstance(team, dict)]
    if teams:
        rounds = teams[0].get("rounds") or {}
        return to_int(rounds.get("won")) + to_int(rounds.get("lost"))
    return len(match.get("rounds") or [])


def started(match: dict) -> datetime | None:
    """試合の開始時刻（日本時間）。"""
    raw = str((match.get("metadata") or {}).get("started_at") or "")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(JST)


def advanced(match: dict, player: dict) -> dict:
    """試合のラウンドとキルからのプレイヤーの数値（``digest``、rounds.py）。無ければ {}。"""
    digest = match.get("digest") or {}
    each = digest.get("players") or []
    players = [other for other in match.get("players") or [] if isinstance(other, dict)]
    for number, other in enumerate(players):
        if other is player or (player.get("puuid") and other.get("puuid") == player.get("puuid")):
            return each[number] if number < len(each) and isinstance(each[number], dict) else {}
    return {}


def line(player: dict, rounds: int, adv: dict | None = None) -> dict:
    """1 人分の数値: K / D / A、ACS（ラウンドあたりスコア）、ADR（ラウンドあたりダメージ）、HS%、+/-、
    DDΔ（ラウンドあたり与ダメージ − 被ダメージ）、エコレーティング（1000 クレジットあたりのダメージ）。 ``adv``（詳細）
    なら KAST、ファーストキル・デス、3 キル以上のラウンド、クラッチも。"""
    stats = player.get("stats") or {}
    kills, deaths, assists = to_int(stats.get("kills")), to_int(stats.get("deaths")), to_int(stats.get("assists"))
    shots = to_int(stats.get("headshots")) + to_int(stats.get("bodyshots")) + to_int(stats.get("legshots"))
    damage = to_int((stats.get("damage") or {}).get("dealt"))
    taken = to_int((stats.get("damage") or {}).get("received"))
    spent = to_int(((player.get("economy") or {}).get("spent") or {}).get("overall"))
    adv = adv or {}
    played = to_int(adv.get("rounds"))
    return {
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "diff": kills - deaths,
        "kd": round(kills / deaths, 2) if deaths else float(kills),
        "acs": round(to_int(stats.get("score")) / rounds) if rounds else 0,
        "adr": round(damage / rounds) if rounds else 0,
        "hs": round(100 * to_int(stats.get("headshots")) / shots) if shots else 0,
        "shots": shots,
        "headshots": to_int(stats.get("headshots")),
        "bodyshots": to_int(stats.get("bodyshots")),
        "legshots": to_int(stats.get("legshots")),
        "damage": damage,
        "taken": taken,
        "dd": round((damage - taken) / rounds) if rounds else 0,
        "econ": round(damage * 1000 / spent) if spent and rounds else None,
        "score": to_int(stats.get("score")),
        "kast": round(100 * to_int(adv.get("kast")) / played) if played else None,
        "fk": to_int(adv.get("fk")) if adv else None,
        "fd": to_int(adv.get("fd")) if adv else None,
        "mk": sum((adv.get("mk") or [0, 0, 0, 0])[1:]) if adv else None,
        "clutches": to_int(adv.get("clutches")) if adv else None,
    }


def lobby(match: dict, player: dict, rounds: int) -> dict | None:
    """試合でのほかのプレイヤーの平均（プレイヤー自身の物差し）: ACS、ADR、HS%、K/D、KAST、
    ラウンドあたりのファーストキル・デス。ラウンドかほかの人がいなければ None。"""
    others = [other for other in match.get("players") or [] if isinstance(other, dict) and other is not player]
    others = [other for other in others if other.get("puuid") != player.get("puuid")]
    if not rounds or not others:
        return None
    lines = [line(other, rounds, advanced(match, other)) for other in others]
    found = {key: sum(item[key] for item in lines) / len(lines) for key in ("acs", "adr", "kills", "deaths")}
    shots = sum(item["shots"] for item in lines)
    found["hs"] = 100 * sum(item["headshots"] for item in lines) / shots if shots else 0
    kast = [item["kast"] for item in lines if item["kast"] is not None]
    found["kast"] = sum(kast) / len(kast) if kast else None
    if all(item["fk"] is not None for item in lines):
        found["fk"] = sum(item["fk"] for item in lines) / len(lines) / rounds
        found["fd"] = sum(item["fd"] for item in lines) / len(lines) / rounds
    return found


def mates(match: dict, player: dict) -> list[dict]:
    """プレイヤーと一緒に並んだ人（同じパーティー）: ``[{"puuid", "name", "tag"}]``。"""
    party = player.get("party_id")
    if not party:
        return []
    return [
        {
            "puuid": str(other.get("puuid") or ""),
            "name": str(other.get("name") or ""),
            "tag": str(other.get("tag") or ""),
        }
        for other in match.get("players") or []
        if isinstance(other, dict) and other.get("party_id") == party and other.get("puuid") != player.get("puuid")
    ]


def team_result(match: dict, team_id: str) -> dict:
    """``{"won": True/False/None（引き分けかチーム無し）, "us": 取ったラウンド, "them": 取られたラウンド, "rounds":
    モードにラウンドがあるか}``。チームデスマッチとエスカレーションにはラウンドが無く、勝敗だけ。"""
    for team in match.get("teams") or []:
        if isinstance(team, dict) and str(team.get("team_id")) == str(team_id):
            rounds = team.get("rounds") or {}
            us, them = to_int(rounds.get("won")), to_int(rounds.get("lost"))
            won = team.get("won")
            if not us and not them:
                return {"won": won if isinstance(won, bool) else None, "us": 0, "them": 0, "rounds": False}
            return {"won": None if us == them else bool(won), "us": us, "them": them, "rounds": True}
    return {"won": None, "us": 0, "them": 0, "rounds": False}


def placing(match: dict, player: dict) -> dict:
    """全員の中での順位: ``{"place": 1.., "of": 人数, "mvp": "match" / "team" / ""}``。チームの無い試合（デスマッチ）
    はキル数、ほかはコンバットスコアで順位。 MVP（ゲームのバッジ）はラウンドのある試合だけ。"""
    players = [other for other in match.get("players") or [] if isinstance(other, dict)]
    teams = bool(team_result(match, str(player.get("team_id") or ""))["rounds"])
    measure = "score" if teams else "kills"

    def value(other: dict) -> int:
        return to_int((other.get("stats") or {}).get(measure))

    mine = value(player)
    place = 1 + sum(1 for other in players if value(other) > mine)
    mvp = ""
    if teams and players and mine > 0:
        if place == 1:
            mvp = "match"
        elif all(value(other) <= mine for other in players if other.get("team_id") == player.get("team_id")):
            mvp = "team"
    return {"place": place, "of": len(players), "mvp": mvp}


def find_player(match: dict, puuid: str = "", name: str = "", tag: str = "") -> dict | None:
    for player in match.get("players") or []:
        if not isinstance(player, dict):
            continue
        if puuid and player.get("puuid") == puuid:
            return player
        if (
            not puuid
            and str(player.get("name", "")).lower() == name.lower()
            and str(player.get("tag", "")).lower() == tag.lower()
        ):
            return player
    return None


#: ロビーと並べる数値: （キー, 大きい方が良いか）。
COMPARED = (("acs", True), ("adr", True), ("kd", True), ("hs", True), ("kast", True), ("fk", True), ("fd", False))


def _rate(won: int, played: int) -> int | None:
    return round(100 * won / played) if played else None


def _per_agent(played: list[dict]) -> list[dict]:
    agents: dict[str, dict] = {}
    for row in played:
        key = row["agent_id"] or row["agent_name"]
        seen = agents.setdefault(
            key,
            {
                "id": row["agent_id"],
                "name": row["agent_name"],
                "icon": row.get("agent_icon"),
                "role": (row.get("role") or {}).get("name") or "",
                "rows": [],
            },
        )
        seen["rows"].append(row)
    table = []
    for seen in agents.values():
        rows = seen.pop("rows")
        table.append(seen | _totals(rows))
    return sorted(table, key=lambda agent: (-agent["games"], -agent["wins"]))


def _totals(rows: list[dict]) -> dict:
    """行のうちいくつかでの試合数、勝敗、勝率、K/D、ACS、ADR、DDΔ、HS%、KAST、プレイ時間。"""
    wins = sum(1 for row in rows if row["result"]["won"] is True)
    losses = sum(1 for row in rows if row["result"]["won"] is False)
    kills = sum(row["line"]["kills"] for row in rows)
    deaths = sum(row["line"]["deaths"] for row in rows)
    shots = sum(row["line"]["shots"] for row in rows)
    with_rounds = [row for row in rows if row.get("rounds")]
    rounds = sum(row["rounds"] for row in with_rounds)
    detailed = [row for row in with_rounds if row.get("adv") and row["adv"].get("rounds")]
    kast_rounds = sum(row["adv"]["rounds"] for row in detailed)
    return {
        "games": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, wins + losses) or 0,
        "kd": round(kills / deaths, 2) if deaths else float(kills),
        "acs": round(sum(row["line"]["acs"] for row in with_rounds) / len(with_rounds)) if with_rounds else None,
        "adr": round(sum(row["line"]["adr"] for row in with_rounds) / len(with_rounds)) if with_rounds else None,
        "dd": round(sum(row["line"].get("damage", 0) - row["line"].get("taken", 0) for row in with_rounds) / rounds)
        if rounds
        else None,
        "hs": round(100 * sum(row["line"]["headshots"] for row in rows) / shots) if shots else 0,
        "kast": round(100 * sum(row["adv"]["kast"] for row in detailed) / kast_rounds) if kast_rounds else None,
        "minutes": round(sum(row.get("length") or 0 for row in rows) / 60000),
    }


def _per_role(played: list[dict]) -> list[dict]:
    """使ったエージェントのロール（デュエリスト、イニシエーターなど）ごとのプレイヤーの数値。多く使った順。"""
    roles: dict[str, dict] = {}
    for row in played:
        role = row.get("role") or {}
        if not role.get("name"):
            continue
        seen = roles.setdefault(role["name"], {"name": role["name"], "icon": role.get("icon"), "rows": []})
        seen["rows"].append(row)
    table = []
    for seen in roles.values():
        rows = seen.pop("rows")
        agents = sorted({row["agent_name"] for row in rows if row.get("agent_name")})
        table.append(seen | _totals(rows) | {"agents": agents})
    return sorted(table, key=lambda role: (-role["games"], -role["wins"], role["name"]))


def _per_map(played: list[dict]) -> list[dict]:
    maps: dict[str, list] = {}
    for row in played:
        name = (row.get("map") or {}).get("name") or ""
        if name and row.get("rounds"):
            maps.setdefault(name, []).append(row)
    table = []
    for name, rows in maps.items():
        sides = _sides(rows)
        image = (rows[0].get("map") or {}).get("image")
        table.append({"name": name, "image": image, **_totals(rows), **sides, "agent": _best_agent(rows)})
    return sorted(table, key=lambda item: (-item["games"], -item["wins"], item["name"]))


def _best_agent(rows: list[dict]) -> dict | None:
    """そのマップでプレイヤーが一番得意なエージェント: 2 回以上使ったもの（無ければ一番使ったもの）
    のうち勝率が一番高いもの、次に試合数と ACS。``{"name", "icon", "games", "win_rate", "acs"}``。"""
    agents: dict[str, list] = {}
    for row in rows:
        if row.get("agent_name"):
            agents.setdefault(row["agent_id"] or row["agent_name"], []).append(row)
    if not agents:
        return None
    picked = {key: found for key, found in agents.items() if len(found) > 1} or agents
    ranked = []
    for found in picked.values():
        totals = _totals(found)
        ranked.append((totals["win_rate"], totals["games"], totals["acs"] or 0, found[0]))
    win_rate, games, acs, row = max(ranked, key=lambda item: item[:3])
    return {"name": row["agent_name"], "icon": row.get("agent_icon"), "games": games, "win_rate": win_rate, "acs": acs}


def _sides(rows: list[dict]) -> dict:
    """攻撃側と防衛側で取ったラウンド: ``{"atk": [取った, プレイした], "def": [...], "atk_rate", "def_rate"}``。"""
    found = {"atk": [0, 0], "def": [0, 0]}
    for row in rows:
        for side in ("atk", "def"):
            won, played = (row.get("side") or {}).get(side) or (0, 0)
            found[side][0] += won
            found[side][1] += played
    return found | {"atk_rate": _rate(*found["atk"]), "def_rate": _rate(*found["def"])}


def _versus_lobby(rows: list[dict]) -> list[dict]:
    """プレイヤーの平均とロビー（同じ試合のほかの人）の平均を並べる。"""
    rows = [row for row in rows if row.get("lobby")]
    if not rows:
        return []
    compared = []
    for key, higher in COMPARED:
        pairs = []
        for row in rows:
            mine, theirs = row["line"].get(key), row["lobby"].get(key)
            if key in ("fk", "fd") and mine is not None:
                mine = mine / row["rounds"]
            if mine is not None and theirs is not None:
                pairs.append((mine, theirs))
        if not pairs:
            continue
        mine = sum(pair[0] for pair in pairs) / len(pairs)
        theirs = sum(pair[1] for pair in pairs) / len(pairs)
        relative = (mine - theirs) / theirs if theirs else 0.0
        compared.append(
            {
                "key": key,
                "mine": mine,
                "lobby": theirs,
                "better": (relative > 0.05) if higher else (relative < -0.05),
                "worse": (relative < -0.05) if higher else (relative > 0.05),
                "score": relative if higher else -relative,  # ロビーよりどれだけ良いか（悪ければ 0 未満）
            }
        )
    return compared


def _mates(rows: list[dict]) -> list[dict]:
    """一番よく一緒に並んだ人と、その試合の結果。"""
    found: dict[str, dict] = {}
    for row in rows:
        for mate in row.get("mates") or []:
            seen = found.setdefault(mate["puuid"] or mate["name"], dict(mate, games=0, wins=0, losses=0))
            seen["games"] += 1
            seen["wins"] += 1 if row["result"]["won"] is True else 0
            seen["losses"] += 1 if row["result"]["won"] is False else 0
    listed = sorted(found.values(), key=lambda mate: (-mate["games"], -mate["wins"], mate["name"].lower()))
    return [dict(mate, win_rate=_rate(mate["wins"], mate["wins"] + mate["losses"])) for mate in listed[:6]]


def summary(rows: list[dict]) -> dict:
    """出している試合（通常の試合があればそれ）全体で: 勝敗、勝率、平均 K/D/A、ACS、ADR、HS%、KAST、DDΔ、
    ファーストキル・デス、マルチキル、クラッチ、プレイ時間、命中部位、エージェント別、マップ別（と攻守）、
    武器ごとのキル、ロビーとの比較、パーティー。"""
    played = [row for row in rows if row.get("line")]
    played = [row for row in played if row.get("standard", True)] or played
    if not played:
        return {}
    count = len(played)
    totals = _totals(played)
    kills = sum(row["line"]["kills"] for row in played)
    deaths = sum(row["line"]["deaths"] for row in played)
    with_rounds = [row for row in played if row.get("rounds")]
    detailed = [row for row in with_rounds if row.get("adv")]
    rounds = sum(row["rounds"] for row in with_rounds)
    heads = sum(row["line"]["headshots"] for row in played)
    bodies = sum(row["line"]["bodyshots"] for row in played)
    legs = sum(row["line"]["legshots"] for row in played)
    shots = heads + bodies + legs
    weapons: dict[str, int] = {}
    for row in played:
        for key, number in (row.get("weapons") or {}).items():
            weapons[key] = weapons.get(key, 0) + number
    weapon_kills = sum(weapons.values())
    multi = [sum((row["adv"].get("mk") or [0, 0, 0, 0])[index] for row in detailed) for index in range(4)]
    agents = _per_agent(played)
    assists = sum(row["line"]["assists"] for row in played)
    detailed_rounds = sum(row["adv"].get("rounds") or 0 for row in detailed)
    extra = [row["extra"] for row in with_rounds if row.get("extra")]
    clutch = {against: [0, 0] for against in range(1, 6)}
    far: dict[str, list] = {}
    for found in extra:
        for against, (won, tried) in (found.get("clutch") or {}).items():
            clutch[int(against)][0] += won
            clutch[int(against)][1] += tried
        for key, (metres, measured, longest) in (found.get("far") or {}).items():
            seen = far.setdefault(key, [0.0, 0, 0.0])
            seen[0] += metres
            seen[1] += measured
            seen[2] = max(seen[2], longest)
    tried = sum(item[1] for item in clutch.values())
    econ = [row["line"]["econ"] for row in with_rounds if row["line"].get("econ") is not None]
    best = max(played, key=lambda row: (row["line"]["kills"], row["line"]["acs"]))
    return {
        "games": count,
        "wins": totals["wins"],
        "losses": totals["losses"],
        "win_rate": totals["win_rate"],
        "kills": round(kills / count, 1),
        "deaths": round(deaths / count, 1),
        "assists": round(assists / count, 1),
        "kd": totals["kd"],
        "kills_total": kills,
        "deaths_total": deaths,
        "assists_total": assists,
        "kad": round((kills + assists) / deaths, 2) if deaths else float(kills + assists),
        "kpr": round(kills / rounds, 2) if rounds else None,
        # ACS、ADR、DDΔ はラウンドあたり: ラウンドの無いモードには無い
        "acs": totals["acs"],
        "adr": totals["adr"],
        "dd": totals["dd"],
        "hs": totals["hs"],
        "kast": totals["kast"],
        "fk": sum(row["adv"]["fk"] for row in detailed) if detailed else None,
        "fd": sum(row["adv"]["fd"] for row in detailed) if detailed else None,
        # プレイヤーがファーストキルを取った（取られた）ラウンドの割合
        "fk_rate": round(100 * sum(row["adv"]["fk"] for row in detailed) / detailed_rounds)
        if detailed_rounds
        else None,
        "fd_rate": round(100 * sum(row["adv"]["fd"] for row in detailed) / detailed_rounds)
        if detailed_rounds
        else None,
        "multi": multi if detailed else None,  # 2・3・4・5 キルのラウンド
        "clutches": sum(row["adv"]["clutches"] for row in detailed) if detailed else None,
        "clutch_best": max((row["adv"]["clutch_best"] for row in detailed), default=0),
        # 相手の人数ごとのクラッチ: 勝ちと挑戦（キルを保存している試合）
        "clutch_table": [
            {"against": against, "won": won, "tried": attempts, "rate": _rate(won, attempts)}
            for against, (won, attempts) in clutch.items()
            if attempts
        ],
        "clutch_tried": tried,
        "clutch_rate": _rate(sum(item[0] for item in clutch.values()), tried),
        "flawless": sum(found.get("flawless") or 0 for found in extra) if extra else None,
        "aces": multi[3] if detailed else None,
        "econ": round(sum(econ) / len(econ)) if econ else None,
        "best": {
            "kills": best["line"]["kills"],
            "deaths": best["line"]["deaths"],
            "assists": best["line"]["assists"],
            "id": best.get("id"),
            "region": best.get("region"),
            "map": (best.get("map") or {}).get("name") or "",
            "agent": best.get("agent_name") or "",
            "when": best.get("when") or "",
        },
        "plants": sum(row["adv"]["plants"] for row in detailed) if detailed else None,
        "defuses": sum(row["adv"]["defuses"] for row in detailed) if detailed else None,
        "minutes": round(sum(row.get("length") or 0 for row in played) / 60000),
        "timed": sum(1 for row in played if row.get("length")),  # 保存した成績（archive.py）には長さが無い
        "detailed": len(detailed),  # ラウンドとキルがある試合: ファーストキルなど
        "aim": {
            "head": round(100 * heads / shots),
            "body": round(100 * bodies / shots),
            "leg": 100 - round(100 * heads / shots) - round(100 * bodies / shots),
            "hits": {"head": heads, "body": bodies, "leg": legs},
        }
        if shots
        else None,
        "decided": totals["wins"] + totals["losses"],  # 0: 勝ち負けと言えるものが無い（デスマッチ）
        "left_out": len([row for row in rows if row.get("line")]) - count,
        "form": [
            "win" if row["result"]["won"] is True else "loss" if row["result"]["won"] is False else "draw"
            for row in played
        ],
        "agents": agents[:3],
        "agent_table": agents,
        "roles": _per_role(played),
        "maps": _per_map(played),
        "sides": _sides(with_rounds),
        "weapons": [
            {
                "key": key,
                "kills": number,
                "share": round(100 * number / weapon_kills),
                # キルの距離（メートル）。両方の場所がわかるものだけ
                "far": round(far[key][0] / far[key][1]) if far.get(key) and far[key][1] else None,
                "longest": round(far[key][2]) if far.get(key) and far[key][1] else None,
            }
            for key, number in sorted(weapons.items(), key=lambda item: -item[1])[:6]
        ],
        "lobby": _versus_lobby(with_rounds),
        "mates": _mates(rows),
    }
