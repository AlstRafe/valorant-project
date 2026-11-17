"""試合のラウンドとキル（HenrikDev v4 の ``rounds`` と ``kills``）からわかることを、
試合を最初に読んだときに 1 回だけ計算する。残り（1 試合 0.5 MB ほど）は保存前に捨てられる（henrik.py）。

プレイヤーごと: ファーストキル・ファーストデス、KAST ラウンド（キル・アシスト・生存・トレード）、マルチキル、
勝ったクラッチ、プラントとデフューズ、武器ごとのキル。ラウンドごと: 勝者と勝ち方、攻撃側のチーム、プラントしたサイト、
各チームのロードアウト、キルの順（コンパクトに: プレイヤーは試合の ``players`` での位置）、
やられた側が倒れた場所と倒した側の立ち位置（heatmap.py が描く）。

HenrikDev の古い答え（v2）は名前が違う。どちらも読み、無いものは省く。"""

from __future__ import annotations

from valtracker.util import as_dict, as_list, to_int

#: 場所の座標の上限（ゲームの単位、センチメートル）。マップは端から端まで 1 万ほど: これより遠いものは壊れた値。
SPOT_LIMIT = 1_000_000
#: 味方がこの時間内に倒した相手を倒せば、デスはトレードされたことになる。
TRADE_MS = 5000
#: ラウンドの終わり方 -> ページで使う短い名前。
ENDINGS = {
    "elimination": "elim",
    "eliminated": "elim",
    "bomb defused": "defuse",
    "bomb detonated": "detonate",
    "round timer expired": "time",
    "surrendered": "surrender",
}
#: 攻守が替わるモードの前半のラウンド数（Red チームが先に攻める）。オーバータイムは毎ラウンド替わる。
HALF = {"competitive": 12, "unrated": 12, "premier": 12, "custom": 12, "swiftplay": 4, "spikerush": 3}
#: 最初のラウンド前のプレイヤー 1 人分の数値。mk: 2・3・4・5 キル（エース）のラウンド数。
EMPTY = {"fk": 0, "fd": 0, "kast": 0, "rounds": 0, "clutches": 0, "clutch_best": 0, "plants": 0, "defuses": 0}
#: これより長いキルの一覧は残さない（デスマッチ: キルが何百もあり、出すラウンドも無い）。
MAX_KILLS = 400


def _puuid(who) -> str:
    if isinstance(who, dict):
        return str(who.get("puuid") or who.get("player_puuid") or who.get("assistant_puuid") or "").lower()
    return str(who or "").lower()


def _kill(raw: dict, place: dict) -> list | None:
    """[ラウンド, ラウンド開始からの ms, 倒した側, やられた側, 武器のキー, [アシスト], 武器名,
    やられた側が倒れた場所, 倒した側の立ち位置]（プレイヤーは位置、-1 は誰でもない。場所は _spot の形）。"""
    victim = place.get(_puuid(raw.get("victim") or raw.get("victim_puuid")))
    if victim is None:
        return None
    killer_id = _puuid(raw.get("killer") or raw.get("killer_puuid"))
    killer = place.get(killer_id, -1)
    weapon = raw.get("weapon") if isinstance(raw.get("weapon"), dict) else {}
    key = str(weapon.get("id") or raw.get("damage_weapon_id") or "").lower()
    name = str(weapon.get("name") or raw.get("damage_weapon_name") or "")
    helpers = []
    for helper in as_list(raw.get("assistants")):
        found = place.get(_puuid(helper))
        if found is not None and found != killer:
            helpers.append(found)
    moment = raw.get("time_in_round_in_ms", raw.get("kill_time_in_round"))
    victim_at = _spot(raw.get("location") if "location" in raw else raw.get("victim_death_location"))
    killer_at = None
    for item in as_list(raw.get("player_locations") or raw.get("player_locations_on_kill")):
        if isinstance(item, dict) and killer_id and _puuid(item.get("player") or item.get("player_puuid")) == killer_id:
            killer_at = _spot(item.get("location"))
            break
    return [
        to_int(raw.get("round", raw.get("round_number"))),
        to_int(moment),
        killer,
        victim,
        key or name,
        helpers,
        name,
        victim_at,
        killer_at,
    ]


def _spot(where) -> list | None:
    """ゲームの単位での場所 [x, y]。不明なら None（HenrikDev はそのとき 0, 0 を出す）。"""
    if not isinstance(where, dict):
        return None
    x, y = where.get("x"), where.get("y")
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int | float) or not isinstance(y, int | float):
        return None
    # nan・inf（round() で落ちる）や、どのマップにもあり得ない遠さ（距離の計算で桁あふれ）は不明とする
    if not (abs(x) <= SPOT_LIMIT and abs(y) <= SPOT_LIMIT):
        return None
    return None if x == 0 and y == 0 else [round(x), round(y)]


def _event(raw: dict, *names: str) -> dict:
    for name in names:
        if isinstance(raw.get(name), dict):
            return raw[name]
    return {}


def _round(raw: dict, place: dict, team: list) -> dict:
    plant = _event(raw, "plant", "plant_events")
    defuse = _event(raw, "defuse", "defuse_events")
    if raw.get("bomb_planted") is False:
        plant = {}
    if raw.get("bomb_defused") is False:
        defuse = {}
    planter = place.get(_puuid(plant.get("player") or plant.get("planted_by")), -1) if plant else -1
    defuser = place.get(_puuid(defuse.get("player") or defuse.get("defused_by")), -1) if defuse else -1
    loadout: dict[str, int] = {}
    shots = []
    for line in as_list(raw.get("stats") or raw.get("player_stats")):
        if not isinstance(line, dict):
            continue
        who = place.get(_puuid(line.get("player") or line.get("player_puuid")))
        counted = line.get("stats") if isinstance(line.get("stats"), dict) else line
        head = to_int(counted.get("headshots"))
        hits = head + to_int(counted.get("bodyshots")) + to_int(counted.get("legshots"))
        if who is not None and hits:
            shots.append([who, head, hits])
        economy = line.get("economy") if isinstance(line.get("economy"), dict) else {}
        if who is not None and team[who]:
            loadout[team[who]] = loadout.get(team[who], 0) + to_int(economy.get("loadout_value"))
    ceremony = str(raw.get("ceremony") or raw.get("round_ceremony") or "").removeprefix("Ceremony").lower()
    return {
        "w": str(raw.get("winning_team") or ""),
        "e": ENDINGS.get(str(raw.get("result") or raw.get("end_type") or "").strip().lower(), ""),
        "c": "" if ceremony in ("", "default") else ceremony,
        "s": str(plant.get("site") or plant.get("plant_site") or ""),
        "p": planter,
        "d": defuser,
        "a": "",
        "l": loadout,
        # ラウンドの命中: [プレイヤー, 頭, 合計]（誰かに当てたプレイヤー）
        "h": shots,
    }


def _sides(rounds: list, team: list, queue: str) -> None:
    """各ラウンドの攻撃側: Red チームが先、前半はモードによる。プラントはいつも攻撃側なので、
    プラントが違うと示せば推測を反転する。前半がわからなければプラントだけで決める。"""
    teams = sorted({name for name in team if name})
    half = HALF.get(queue)
    if half and len(teams) == 2:
        first = "Red" if "Red" in teams else teams[0]
        other = teams[1] if first == teams[0] else teams[0]
        for number, item in enumerate(rounds):
            if number < 2 * half:
                item["a"] = first if number < half else other
            else:
                item["a"] = first if (number - 2 * half) % 2 == 0 else other
        planted = [(item["a"], team[item["p"]]) for item in rounds if item["p"] >= 0]
        if sum(guess != planter for guess, planter in planted) > sum(guess == planter for guess, planter in planted):
            for item in rounds:
                item["a"] = other if item["a"] == first else first
        return
    for item in rounds:
        item["a"] = team[item["p"]] if item["p"] >= 0 else ""


def digest(match: dict) -> dict:
    """ページが使うラウンドとキルの情報（モジュールの説明を参照）。試合に無ければ {}。"""
    players = [player for player in as_list(match.get("players")) if isinstance(player, dict)]
    ids = [_puuid(player) for player in players]
    place = {puuid: number for number, puuid in enumerate(ids) if puuid}
    team = [str(player.get("team_id") or "") for player in players]
    metadata = match.get("metadata") if isinstance(match.get("metadata"), dict) else {}
    queue = (
        str(as_dict(metadata.get("queue")).get("id") or "").lower() if isinstance(metadata.get("queue"), dict) else ""
    )
    raw_rounds = [item for item in as_list(match.get("rounds")) if isinstance(item, dict)]
    rounds = [_round(item, place, team) for item in raw_rounds]
    rounds = rounds if any(item["w"] for item in rounds) else []
    kills = [
        found
        for item in as_list(match.get("kills"))
        if isinstance(item, dict)
        for found in [_kill(item, place)]
        if found
    ]
    kills.sort(key=lambda kill: (kill[0], kill[1]))
    if not rounds and not kills:
        return {}
    _sides(rounds, team, queue)
    weapons = {kill[4]: kill[6] for kill in kills if kill[4] and kill[6]}
    each = [dict(EMPTY, mk=[0, 0, 0, 0], weapons={}) for _ in players]
    for kill in kills:
        killer, victim = kill[2], kill[3]
        if killer >= 0 and team[killer] != team[victim] and kill[4]:
            counted = each[killer]["weapons"]
            counted[kill[4]] = counted.get(kill[4], 0) + 1
    by_round: dict[int, list] = {}
    for kill in kills:
        by_round.setdefault(kill[0], []).append(kill)
    for number, item in enumerate(rounds):
        happened = by_round.get(number, [])
        _round_players(item, happened, team, each)
        if item["p"] >= 0:
            each[item["p"]]["plants"] += 1
        if item["d"] >= 0:
            each[item["d"]]["defuses"] += 1
    sides: dict[str, dict] = {}
    for item in rounds:
        if not item["a"]:
            continue
        for name in {name for name in team if name}:
            side = sides.setdefault(name, {"atk": [0, 0], "def": [0, 0]})["atk" if item["a"] == name else "def"]
            side[1] += 1
            side[0] += 1 if item["w"] == name else 0
    # [ラウンド, ms, 倒した側, やられた側, 武器, [アシスト], やられた側が倒れた場所, 倒した側の立ち位置]
    compact = [kill[:6] + kill[7:9] for kill in kills] if rounds and len(kills) <= MAX_KILLS else []
    return {"rounds": rounds, "kills": compact, "weapons": weapons, "players": each, "sides": sides}


def _round_players(item: dict, happened: list, team: list, each: list) -> None:
    """1 ラウンドのファーストキル・ファーストデス、KAST、マルチキル、クラッチを各プレイヤーに足す。"""
    enemies = [kill for kill in happened if kill[2] >= 0 and team[kill[2]] != team[kill[3]]]
    if enemies:
        each[enemies[0][2]]["fk"] += 1
        each[enemies[0][3]]["fd"] += 1
    killed: dict[int, int] = {}
    for kill in enemies:
        killed[kill[2]] = killed.get(kill[2], 0) + 1
    for player, count in killed.items():
        if count >= 2:
            each[player]["mk"][min(count, 5) - 2] += 1
    dead = {kill[3]: kill for kill in happened}
    helped = {helper for kill in enemies for helper in kill[5]}
    for player in range(len(each)):
        if not team[player]:
            continue
        each[player]["rounds"] += 1
        death = dead.get(player)
        traded = bool(death) and any(
            other[3] == death[2]
            and other[2] >= 0
            and team[other[2]] == team[player]
            and 0 <= other[1] - death[1] <= TRADE_MS
            for other in enemies
        )
        if player in killed or player in helped or death is None or traded:
            each[player]["kast"] += 1
    # クラッチ: チーム最後の 1 人で、相手が 1 人以上、
    # そしてチームがそのラウンドを取った（倒れる前に設置したスパイクでも）
    alive = {name: {player for player in range(len(team)) if team[player] == name} for name in set(team) if name}
    last: dict[str, tuple[int, int]] = {}
    for kill in happened:
        for members in alive.values():
            members.discard(kill[3])
        for name, members in alive.items():
            others = sum(len(rest) for other, rest in alive.items() if other != name)
            if len(members) == 1 and others and name not in last:
                last[name] = (next(iter(members)), others)
    for name, (player, against) in last.items():
        if item["w"] == name:
            each[player]["clutches"] += 1
            each[player]["clutch_best"] = max(each[player]["clutch_best"], against)


#: ゲームの単位はセンチメートル: キルの距離（メートル）は 2 人の (x, y) の平面距離 / 100。
METRE = 100


def extras(match: dict, me: int) -> dict:
    """ダイジェストの合計以外に、1 人のプレイヤーのラウンドからわかること。
    コンパクトなラウンドとキルから読む（これを数える前に保存した試合にもある）
    : ``{"flawless": 誰も失わずに取ったラウンド, "clutch": {相手の人数: [勝ち, 挑戦]}（チーム最後の 1 人になった時点、相手 1〜5）, "far": {武器のキー: [メートル, キル, 最長]}（両方の場所がわかるキル）
    }``。ラウンドとキルが無ければ {}。"""
    digest = match.get("digest") if isinstance(match.get("digest"), dict) else {}
    played, kills = as_list(digest.get("rounds")), as_list(digest.get("kills"))
    players = [player for player in as_list(match.get("players")) if isinstance(player, dict)]
    if not played or not kills or not 0 <= me < len(players):
        return {}
    team = [str(player.get("team_id") or "") for player in players]
    mine = team[me]
    if not mine:
        return {}
    by_round: dict[int, list] = {}
    for kill in kills:
        if isinstance(kill, list) and len(kill) >= 4 and 0 <= to_int(kill[3], -1) < len(team):
            by_round.setdefault(to_int(kill[0]), []).append(kill)
    flawless, clutch, far = 0, {}, {}
    for number, item in enumerate(played):
        happened = by_round.get(number, [])
        won = isinstance(item, dict) and item.get("w") == mine
        if won and not any(team[kill[3]] == mine for kill in happened):
            flawless += 1
        alive = {name: {player for player in range(len(team)) if team[player] == name} for name in set(team) if name}
        for kill in happened:
            for members in alive.values():
                members.discard(kill[3])
            if alive.get(mine) == {me}:
                against = sum(len(rest) for other, rest in alive.items() if other != mine)
                if against:
                    seen = clutch.setdefault(min(against, 5), [0, 0])
                    seen[0] += 1 if won else 0
                    seen[1] += 1
                break
            if not alive.get(mine):
                break
    for kill in kills:
        if not isinstance(kill, list) or len(kill) < 8 or kill[2] != me or not kill[4]:
            continue
        if not 0 <= to_int(kill[3], -1) < len(team) or team[kill[3]] == mine:
            continue
        victim_at, killer_at = kill[6], kill[7]
        if not (isinstance(victim_at, list) and isinstance(killer_at, list) and len(victim_at) == len(killer_at) == 2):
            continue
        metres = ((victim_at[0] - killer_at[0]) ** 2 + (victim_at[1] - killer_at[1]) ** 2) ** 0.5 / METRE
        seen = far.setdefault(str(kill[4]), [0.0, 0, 0.0])
        seen[0] += metres
        seen[1] += 1
        seen[2] = max(seen[2], metres)
    return {"flawless": flawless, "clutch": clutch, "far": far}
