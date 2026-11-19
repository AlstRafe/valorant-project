"""ゲーム自身の試合記録（Riot の PD: ``match-history`` と ``match-details``）を HenrikDev v4 の形にする。
サイトのほかの部分（henrik.slim_match、rounds.py、stats.py、heatmap.py）が HenrikDev のものと同じように読めるように。

PD は試合の全プレイヤーの名前を出す（ゲームで名前を隠している人も）。このサイトは隠した名前を出さないが、
PD は誰が隠しているかを言わないので、ここでは名前を全部消す。
例外は記録を読んでいるプレイヤー本人（訪問者がその名前で検索した）。"""

from __future__ import annotations

from datetime import UTC, datetime

from valtracker.util import LAST_MOMENT, as_dict, as_list, lower_id, to_int

#: PD のキュー ID -> 試合の種類（HenrikDev の ``mode_type``）。ほかはラウンド制（"Standard"）。
MODE_TYPES = {
    "deathmatch": "Deathmatch",
    "hurm": "TeamDeathmatch",
    "ggteam": "Escalation",
    "snowball": "Snowball",
    "skirmish_1v1": "Skirmish",
    "skirmish_2v2": "Skirmish",
}
#: ゲームサーバー名の都市（PD の ``gamePodId``: "aresriot.aws-...-1.ap-gp-tokyo-1"）
#: -> HenrikDev の名前（訳は views.CLUSTERS）。ほかはそのまま書く。
CITIES = {
    "tokyo": "Tokyo",
    "hongkong": "Hong Kong",
    "singapore": "Singapore",
    "sydney": "Sydney",
    "mumbai": "Mumbai",
    "seoul": "Seoul",
    "frankfurt": "Frankfurt",
    "london": "London",
    "paris": "Paris",
    "stockholm": "Stockholm",
    "madrid": "Madrid",
    "warsaw": "Warsaw",
    "istanbul": "Istanbul",
    "bahrain": "Bahrain",
    "telaviv": "Tel Aviv",
    "ashburn": "N. Virginia",
    "virginia": "N. Virginia",
    "oregon": "Oregon",
    "norcal": "N. California",
    "california": "N. California",
    "texas": "Texas",
    "dallas": "Texas",
    "chicago": "Illinois",
    "illinois": "Illinois",
    "atlanta": "Georgia",
    "georgia": "Georgia",
    "mexicocity": "Mexico City",
    "mexico": "Mexico City",
    "miami": "Miami",
    "santiago": "Santiago",
    "saopaulo": "São Paulo",
}


def city(pod) -> str:
    """ゲームサーバー名の都市（無ければ ""）。"""
    last = str(pod or "").rsplit(".", 1)[-1].lower()
    parts = [part for part in last.split("-") if part and not part.isdigit()]
    if len(parts) >= 3 and parts[1] == "gp":
        parts = parts[2:]  # "ap-gp-tokyo" -> "tokyo"
    name = "".join(parts)
    return CITIES.get(name, name.title() if name else "")


def history(answer) -> dict:
    """PD の match-history -> ``{"total", "matches": [{"id", "at"（unix 秒）, "queue"}]}``、新しい順。"""
    answer = as_dict(answer)
    found = [
        {
            "id": lower_id(item["MatchID"]),
            "at": to_int(item.get("GameStartTime")) // 1000,
            "queue": lower_id(item.get("QueueID")),
        }
        for item in as_list(answer.get("History"))
        if isinstance(item, dict) and item.get("MatchID")
    ]
    return {"total": to_int(answer.get("Total"), len(found)), "matches": found}


def rr_updates(answer) -> dict:
    """PD の competitiveupdates -> henrik._slim_rr の形: ``{試合 ID: {"change", "tier", "rr", "elo", "at"}}``（RR
    が動かない配置試合は前の tier が無いので省く）。"""
    answer = as_dict(answer)
    found = {}
    for item in as_list(answer.get("Matches")):
        if not isinstance(item, dict) or not item.get("MatchID"):
            continue
        tier = to_int(item.get("TierAfterUpdate"))
        if tier < 3 or not to_int(item.get("TierBeforeUpdate")):
            continue
        rr = to_int(item.get("RankedRatingAfterUpdate"))
        found[lower_id(item["MatchID"])] = {
            "change": to_int(item.get("RankedRatingEarned")),
            "tier": tier,
            "rr": rr,
            "elo": (tier - 3) * 100 + rr,
            "at": to_int(item.get("MatchStartTime")) // 1000,
        }
    return found


def to_v4(details, region: str, maps: dict, puuid: str = "", name: str = "", tag: str = "", keep=frozenset()) -> dict:
    """PD の match-details -> HenrikDev v4 の試合（henrik.slim_match が読むもの）。
    ``maps``: valorant-api.com のマップをアドレス名で（"ascent"、content.Content.map_index）。名前（``name``、``tag``）
    を残すのは ``puuid`` だけ。``keep``: ほかに PD の名前を残すプレイヤー（持ち主のページだけ: party_of）。
    終わった試合でなければ {}。"""
    if not isinstance(details, dict) or not isinstance(details.get("matchInfo"), dict):
        return {}
    info = details["matchInfo"]
    match_id = lower_id(info.get("matchId"))
    if not match_id:
        return {}
    queue = lower_id(info.get("queueID")) or "custom"
    address = str(info.get("mapId") or "").rsplit("/", 1)[-1].lower()
    known = maps.get(address) or {}
    start = to_int(info.get("gameStartMillis"))
    if not 0 < start <= LAST_MOMENT * 1000:  # 壊れた時刻（日付にすると OverflowError）は無いことにする
        start = 0
    players_raw = [item for item in as_list(details.get("players")) if isinstance(item, dict) and item.get("subject")]
    players_raw = [item for item in players_raw if not item.get("isObserver")]
    rounds_raw = [item for item in as_list(details.get("roundResults")) if isinstance(item, dict)]
    shots, dealt, taken, spent = _from_rounds(rounds_raw)
    players = []
    for item in players_raw:
        subject = lower_id(item["subject"])
        line = as_dict(item.get("stats"))
        head, body, leg = shots.get(subject, (0, 0, 0))
        mine = subject == lower_id(puuid)
        kept = subject in keep and not mine
        players.append(
            {
                "puuid": subject,
                "name": name if mine else str(item.get("gameName") or "") if kept else "",
                "tag": tag if mine else str(item.get("tagLine") or "") if kept else "",
                "team_id": str(item.get("teamId") or ""),
                "party_id": lower_id(item.get("partyId")),
                "agent": {"id": lower_id(item.get("characterId")), "name": ""},
                "stats": {
                    "score": to_int(line.get("score")),
                    "kills": to_int(line.get("kills")),
                    "deaths": to_int(line.get("deaths")),
                    "assists": to_int(line.get("assists")),
                    "headshots": head,
                    "bodyshots": body,
                    "legshots": leg,
                    "damage": {"dealt": dealt.get(subject, 0), "received": taken.get(subject, 0)},
                },
                "tier": {"id": to_int(item.get("competitiveTier")), "name": ""},
                "account_level": to_int(item.get("accountLevel")),
                "economy": {"spent": {"overall": spent.get(subject, 0)}},
            }
        )
    teams = []
    for item in as_list(details.get("teams")):
        if not isinstance(item, dict) or not item.get("teamId"):
            continue
        won, played = to_int(item.get("roundsWon")), to_int(item.get("roundsPlayed"))
        teams.append(
            {
                "team_id": str(item["teamId"]),
                "rounds": {"won": won, "lost": max(0, played - won)},
                "won": bool(item.get("won")),
            }
        )
    if MODE_TYPES.get(queue) in ("Deathmatch", "Escalation", "Snowball"):
        teams = []  # 1 人ずつのチーム、またはラウンドでないラウンド: HenrikDev も出さない
    return {
        "metadata": {
            "match_id": match_id,
            "map": {"id": lower_id(known.get("uuid")), "name": str(known.get("displayName") or address.title())},
            "game_length_in_ms": to_int(info.get("gameLengthMillis")),
            "started_at": datetime.fromtimestamp(start / 1000, UTC).isoformat().replace("+00:00", "Z") if start else "",
            "is_completed": bool(info.get("isCompleted", True)),
            "queue": {"id": queue, "name": "", "mode_type": MODE_TYPES.get(queue, "Standard")},
            "season": {"id": lower_id(info.get("seasonId")), "short": ""},
            "region": region,
            "cluster": city(info.get("gamePodId")),
        },
        "players": players,
        "teams": teams,
        "rounds": [_round(item) for item in rounds_raw],
        "kills": _kills(details, rounds_raw),
    }


def _from_rounds(rounds_raw: list) -> tuple[dict, dict, dict, dict]:
    """各プレイヤーのラウンド通算の命中（頭・体・脚）、与ダメージ・被ダメージ、使ったクレジット。"""
    shots: dict[str, tuple[int, int, int]] = {}
    dealt: dict[str, int] = {}
    taken: dict[str, int] = {}
    spent: dict[str, int] = {}
    for item in rounds_raw:
        for line in as_list(item.get("playerStats")):
            if not isinstance(line, dict):
                continue
            subject = lower_id(line.get("subject"))
            economy = as_dict(line.get("economy"))
            spent[subject] = spent.get(subject, 0) + to_int(economy.get("spent"))
            for hit in as_list(line.get("damage")):
                if not isinstance(hit, dict):
                    continue
                head, body, leg = shots.get(subject, (0, 0, 0))
                shots[subject] = (
                    head + to_int(hit.get("headshots")),
                    body + to_int(hit.get("bodyshots")),
                    leg + to_int(hit.get("legshots")),
                )
                dealt[subject] = dealt.get(subject, 0) + to_int(hit.get("damage"))
                receiver = lower_id(hit.get("receiver"))
                taken[receiver] = taken.get(receiver, 0) + to_int(hit.get("damage"))
    return shots, dealt, taken, spent


def _round(item: dict) -> dict:
    planter, defuser = lower_id(item.get("bombPlanter")), lower_id(item.get("bombDefuser"))
    return {
        "winning_team": str(item.get("winningTeam") or ""),
        "result": str(item.get("roundResult") or ""),
        "ceremony": str(item.get("roundCeremony") or ""),
        "plant": {"site": str(item.get("plantSite") or ""), "player": {"puuid": planter}} if planter else None,
        "defuse": {"player": {"puuid": defuser}} if defuser else None,
        "stats": [
            {
                "player": {"puuid": lower_id(line.get("subject"))},
                "stats": _shots(line),
                "economy": {
                    "loadout_value": to_int(line["economy"].get("loadoutValue"))
                    if isinstance(line.get("economy"), dict)
                    else 0
                },
            }
            for line in as_list(item.get("playerStats"))
            if isinstance(line, dict)
        ],
    }


def _shots(line: dict) -> dict:
    """1 ラウンドの命中を部位ごとに（PD は当てた相手ごとに数える）。"""
    hit = [item for item in as_list(line.get("damage")) if isinstance(item, dict)]
    return {name: sum(to_int(item.get(name)) for item in hit) for name in ("headshots", "bodyshots", "legshots")}


def _kills(details: dict, rounds_raw: list) -> list:
    """全キル（試合の一覧、無ければラウンドごとのもの）を v4 の形で。"""
    raw = [item for item in as_list(details.get("kills")) if isinstance(item, dict)]
    if not raw:
        for number, item in enumerate(rounds_raw):
            for line in as_list(item.get("playerStats")):
                for kill in as_list(as_dict(line).get("kills")):
                    if isinstance(kill, dict):
                        raw.append(dict(kill, round=kill.get("round", number)))
    found = []
    for kill in raw:
        damage = as_dict(kill.get("finishingDamage"))
        item = str(damage.get("damageItem") or "")
        found.append(
            {
                "round": to_int(kill.get("round")),
                "time_in_round_in_ms": to_int(kill.get("roundTime")),
                "killer": {"puuid": lower_id(kill.get("killer"))},
                "victim": {"puuid": lower_id(kill.get("victim"))},
                "assistants": [{"puuid": lower_id(helper)} for helper in as_list(kill.get("assistants"))],
                # 武器の uuid。アビリティはスロット（"Ultimate"）で、valorant-api.com の武器一覧には無い
                "weapon": {"id": item.lower(), "name": "" if damage.get("damageType") == "Weapon" else item},
                "location": kill.get("victimLocation"),
                "player_locations": [
                    {"player": {"puuid": lower_id(spot.get("subject"))}, "location": spot.get("location")}
                    for spot in as_list(kill.get("playerLocations"))
                    if isinstance(spot, dict)
                ],
            }
        )
    return found


def party_of(details, puuid: str) -> frozenset[str]:
    """PD の match-details で ``puuid`` と同じパーティー（Riot の ``partyId``）にいたプレイヤー（本人と観戦者を除く）
    : ゲームはパーティーの仲間には名前を隠さない。"""
    found = details.get("players") if isinstance(details, dict) else None
    players = [item for item in found or [] if isinstance(item, dict)]
    me = lower_id(puuid)
    party = next((lower_id(item.get("partyId")) for item in players if lower_id(item.get("subject")) == me), "")
    if not party or not me:
        return frozenset()
    return frozenset(
        lower_id(item.get("subject"))
        for item in players
        if lower_id(item.get("partyId")) == party
        and lower_id(item.get("subject")) not in ("", me)
        and not item.get("isObserver")
    )
