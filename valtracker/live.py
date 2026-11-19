"""持ち主の今のゲーム（/live）。GLZ から（riot.Riot.live）: VALORANT が開いているか。メニューでは、パーティー、キュー、
マッチングの開始・停止ボタン。エージェント選択では、チームのピック、まだ足りないロール、
持ち主のエージェントのピックとロック。試合では、全員のエージェント・カード・称号・レベル・ランク、各チームの平均ランク、
全員のアクトの戦績、誰と誰が同じパーティーか（番号: 持ち主のパーティーと、PC アプリならほかの人のものも）。

名前はゲームで隠していない（``Incognito`` でない）プレイヤーと、持ち主と同じパーティーのプレイヤー（ゲームの設定も
「パーティー外のプレイヤーに名前を隠す」: 仲間には見える）だけ、レベルは隠していない（``HideAccountLevel`` でない）
ときだけ出す。名前を隠している人も名前以外（カード・称号・戦績・ランク・パーティー）は出す: ゲームが隠すのは名前だけで、
試合のページ（スコアボード）とほかのトラッカーも同じ（持ち主の頼み、2026-11-17）。"""

from __future__ import annotations

from datetime import datetime

from valtracker import i18n, smurf
from valtracker.activity import FEW
from valtracker.content import Content, todays_tier
from valtracker.util import as_dict, as_list, lower_id, to_int

#: valorant-api.com のロールを、チームがふつう埋める順で: まずスモーク、次に情報、センチネル、エントリー。
CONTROLLER = "4ee40330-ecdd-4f2f-98a8-eb1243428373"
INITIATOR = "1b47567f-8f7b-444b-aae3-b0c634622d10"
SENTINEL = "5fc02f99-4091-4486-a531-98459a07e894"
DUELIST = "dbe8757e-9e92-4ed4-b39f-9dfc589691d4"
ROLES = (CONTROLLER, INITIATOR, SENTINEL, DUELIST)
#: 足りないロールに勧めるエージェントの最大数。
SUGGESTED = 4
#: エージェント選択で挙げる、そのマップで一番勝っている持ち主のエージェントの数。
BEST_SHOWN = 3
#: /live とほかのページのヘッダーのライブタブが、ゲームの状態（state の phase）に応じて状態を聞く間隔（秒）
#: : 試合が見つかったら毎秒（エージェント選択前のゲームの 5 秒カウント）、マッチング中とエージェント選択は 2 秒ごと、
#: 何も変わりそうにないときはたまに。1 回見るごとに Riot に聞くのは 1〜2 回（riot.Riot.live）、
#: このサーバーから: 持ち主の PC では何も動かさない。
EVERY = {"": (20, 30), "menus": (5, 8), "queue": (2, 2), "found": (1, 1), "pregame": (2, 4), "ingame": (10, 15)}
#: 席のランクがまだ来ていない（riot.Riot.live の waiting）ときに /live が次に見るまでの秒数: ロード画面で
#: 相手のエージェントと名前を先に出し、ランクと戦績は来たらすぐ足す（試合中の 10 秒を待たない）。
SOON = 2


def icon(agent: dict) -> str:
    """小さい四角（ピック、席、ロール）用のエージェントの絵: あれば valorant-api.com の小さいもの（128 px。
    256 px のもののバイト数の 4 分の 1）。"""
    return str(agent.get("displayIconSmall") or agent.get("displayIcon") or "")


def _seasons(mmr) -> dict:
    """PD の mmr の答えのコンペティティブのアクトごとの情報（アクト ID → 情報）。"""
    skills = (
        as_dict(as_dict(mmr.get("QueueSkills")).get("competitive")).get("SeasonalInfoBySeasonID")
        if isinstance(mmr, dict)
        else {}
    )
    return as_dict(skills)


def _act_info(mmr, act: str) -> dict | None:
    """そのアクトの情報。無ければ None。"""
    info = next((info for season, info in _seasons(mmr).items() if lower_id(season) == act), None) if act else None
    return info if isinstance(info, dict) else None


def _rank(mmr, act: str) -> tuple[int, int]:
    """PD の mmr の答えからの今のアクトでの (tier, RR)。無ければ (0, 0)。"""
    if not isinstance(mmr, dict):
        return 0, 0
    for season, info in _seasons(mmr).items():
        if act and lower_id(season) == act and isinstance(info, dict) and to_int(info.get("CompetitiveTier")) > 2:
            return to_int(info.get("CompetitiveTier")), to_int(info.get("RankedRating"))
    latest = as_dict(mmr.get("LatestCompetitiveUpdate"))
    if act and lower_id(latest.get("SeasonID")) == act:
        return to_int(latest.get("TierAfterUpdate")), to_int(latest.get("RankedRatingAfterUpdate"))
    return 0, 0


def best_ever(mmr, old_acts=frozenset()) -> int:
    """プレイヤーが今まで遊んだ全アクトでの最高ランク（PD の mmr の答え: アクトごとの tier 別の勝利と、
    終わったときの tier）を今の番号で（``old_acts``: アセンダント以前のアクト）。0: 無し。"""
    best = 0
    for season, info in _seasons(mmr).items():
        if not isinstance(info, dict):
            continue
        won_at = as_dict(info.get("WinsByTier"))
        tiers = [to_int(tier) for tier, count in won_at.items() if to_int(count) > 0]
        tiers.append(to_int(info.get("CompetitiveTier")))
        best = max(best, todays_tier(max(tiers), lower_id(season) in old_acts))
    return best


def act_best(mmr, act: str) -> int:
    """プレイヤーのそのアクトでの最高ランク（勝った tier と、今の tier）。0: 無し。"""
    info = _act_info(mmr, act)
    if info is None:
        return 0
    won_at = as_dict(info.get("WinsByTier"))
    tiers = [to_int(tier) for tier, count in won_at.items() if to_int(count) > 0]
    return max([*tiers, to_int(info.get("CompetitiveTier"))])


def _smurf(tier: int, level: int | None, mmr, act: str) -> dict | None:
    """レベル、ランク戦のアクト、今のアクト（``act``）の戦績からのサブ垢の推測（smurf.guess）。"""
    skills = _seasons(mmr)
    acts = (
        sum(1 for info in skills.values() if isinstance(info, dict) and to_int(info.get("NumberOfGames")) > 0)
        if skills
        else None
    )
    record = _record(mmr, act) if act else None
    return smurf.guess(
        tier if tier > 2 else 0,
        level,
        acts,
        record["games"] if record else 0,
        record["win_rate"] if record else None,
    )


def _record(mmr, act: str) -> dict | None:
    """PD の mmr の答えからのアクトのコンペティティブ戦績（ゲームのキャリアページと同じ）
    : ``{"wins", "games", "win_rate"}``。試合が無ければ None。"""
    info = _act_info(mmr, act)
    if info is None:
        return None
    games = to_int(info.get("NumberOfGames"))
    if games <= 0:
        return None
    wins = to_int(info.get("NumberOfWinsWithPlacements") or info.get("NumberOfWins"))
    return {"wins": min(wins, games), "games": games, "win_rate": round(100 * min(wins, games) / games)}


def _rank_of(tier: int, look: dict) -> dict | None:
    known = look["tiers"].get(tier) or {}
    name = str(known.get("tierName") or "")
    return {"name": name.title() if name.isupper() else name, "icon": known.get("smallIcon")} if tier > 2 else None


def average(rows: list[dict], look: dict) -> dict | None:
    """チームの平均ランク（ランクのある人で）をランクの名前で: ロビーの強さ。"""
    tiers = [row["tier"] for row in rows if row["tier"] > 2]
    return _rank_of(round(sum(tiers) / len(tiers)), look) if len(tiers) >= 2 else None


def _player(item: dict, look: dict, me: str, rank_key: str) -> dict:
    identity = as_dict(item.get("PlayerIdentity"))
    agent = look["agents"].get(lower_id(item.get("CharacterID"))) or {}
    subject = lower_id(item.get("Subject"))
    # 同じパーティーの仲間（Riot のパーティーのメンバー、riot.Riot.live）にはゲームも名前を見せる
    mate = subject in look["mates"] and subject != me
    # 隠すのは名前だけ（riot.Riot は隠している人の名前をそもそも聞かない）
    hidden = bool(identity.get("Incognito")) and not mate
    name = look["names"].get(subject) if not hidden else None
    badge = as_dict(item.get("SeasonalBadgeInfo"))
    tier, rr = _rank(look["ranks"].get(subject), look["act"])
    if tier <= 2:
        tier, rr = to_int(item.get(rank_key) if rank_key in item else badge.get("Rank")), None
    rank = _rank_of(tier, look)
    level = None if identity.get("HideAccountLevel") else to_int(identity.get("AccountLevel")) or None
    card = lower_id(identity.get("PlayerCardID"))
    title = look["titles"].get(lower_id(identity.get("PlayerTitleID"))) or {}
    return {
        "me": subject == me,
        "agent": agent.get("displayName") or "",
        "agent_icon": icon(agent),
        "agent_id": lower_id(item.get("CharacterID")),
        "role": lower_id(as_dict(agent.get("role")).get("uuid")),
        "state": str(item.get("CharacterSelectionState") or ""),
        "name": name[0] if name else "",
        "tag": name[1] if name else "",
        "mate": mate,
        "card": f"https://media.valorant-api.com/playercards/{card}/smallart.png" if len(card) == 36 else "",
        "title": str(title.get("titleText") or ""),
        "level": level,
        "rank": {**rank, "rr": rr} if rank else None,
        "tier": tier if tier > 2 else 0,
        # アクトの戦績と 3 つのランク（今、アクト最高、通算最高）は全員: 名前を隠している人にも（2026-11-15、11-17）
        "record": _record(look["ranks"].get(subject), look["act"]),
        "peak": _rank_of(best_ever(look["ranks"].get(subject), look.get("old_acts") or frozenset()), look),
        "act_peak": _rank_of(act_best(look["ranks"].get(subject), look["act"]), look),
        "smurf": _smurf(tier, level, look["ranks"].get(subject), look["act"]),
        "team": str(item.get("TeamID") or ""),
        "owner": bool(item.get("IsOwner")),
        "subject": subject,
        #: 同じパーティーの番号（1 から。_number_parties）。0: 1 人か、わからない
        "party": 0,
    }


def _number_parties(rows: list[dict], parties: dict) -> list[int]:
    """試合の席（``rows``、ページの順）に、同じパーティーの人どうしで同じ番号（``party``: 1 から）を付ける。
    ``parties``: {puuid: パーティーの ID}（riot.Riot._parties）。番号は持ち主のパーティーが 1、あとは席の順。
    2 人以上いるパーティーだけ（1 人ならソロと同じ）。答え: 番号ごとの人数（番号の順）。"""
    members: dict[str, list[dict]] = {}
    for row in rows:
        party = parties.get(row["subject"]) if row["subject"] else None
        if party:
            members.setdefault(party, []).append(row)
    groups = [group for group in members.values() if len(group) >= 2]
    groups.sort(key=lambda group: not any(row["me"] for row in group))  # 持ち主のパーティーを先に（安定ソート）
    for number, group in enumerate(groups, 1):
        for row in group:
            row["party"] = number
    return [len(group) for group in groups]


def _team_parties(rows: list[dict]) -> list[int]:
    """チームの席にいるパーティーの人数（大きい順。2 人以上）: 見出しに「パーティー 3人」のように出す。"""
    sizes: dict[int, int] = {}
    for row in rows:
        if row["party"]:
            sizes[row["party"]] = sizes.get(row["party"], 0) + 1
    return sorted((size for size in sizes.values() if size >= 2), reverse=True)


def _seconds_since(value, now: float) -> int | None:
    try:
        when = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if when.year < 2000:  # GLZ の "0001-01-01": 並んでいない
        return None
    return max(0, int(now - when.timestamp()))


def _party(party: dict, look: dict, me: str, queue_name, now: float) -> dict:
    members = [
        _player(item, look, me, "CompetitiveTier") for item in as_list(party.get("Members")) if isinstance(item, dict)
    ]
    making = as_dict(party.get("MatchmakingData"))
    state = str(party.get("State") or "")
    current = lower_id(making.get("QueueID"))
    eligible = [lower_id(queue) for queue in as_list(party.get("EligibleQueues")) if isinstance(queue, str) and queue]
    if current and current not in eligible:
        eligible.insert(0, current)
    return {
        "members": members,
        "state": "matchmaking" if state == "MATCHMAKING" else "idle" if state in ("DEFAULT", "") else "starting",
        "queue": current,
        "queue_name": queue_name(current) if current else "",
        "queues": [{"id": queue, "name": queue_name(queue)} for queue in eligible],
        "waited": _seconds_since(party.get("QueueEntryTime"), now) if state == "MATCHMAKING" else None,
        "leader": any(row["me"] and row["owner"] for row in members) or len(members) <= 1,
        "restricted": to_int(party.get("RestrictedSeconds")),
    }


def page(
    found: dict, content: Content, lang: str, me: str, owned_agents: set | None, queue_name, agent_stats=None
) -> dict:
    """live.html に出す ``found``（riot.Riot.live）。 ``owned_agents``: 勧める候補用の持ち主のエージェント（uuid）、
    ``queue_name(id)``: ページの言語でのキューの名前、 ``agent_stats(map uuid, queue id)``: このサイトが見た試合での、
    そのマップでの各エージェントの成績（activity.Activity.agents）。"""
    phase = found.get("phase") or ""
    match = as_dict(found.get("match"))
    agents = content.agent_index(lang)
    act = content.act(lang)
    look = {
        "agents": agents,
        "tiers": content.tiers(lang),
        "titles": content.index("playertitles", lang),
        "names": found.get("names") or {},
        "ranks": found.get("ranks") or {},
        "mates": {lower_id(subject) for subject in as_list(found.get("mates"))},
        "act": (act or {}).get("uuid") or "",
        "old_acts": content.old_acts(lang),
    }
    index = content.map_index(lang)
    address = str(match.get("MapID") or "").rsplit("/", 1)[-1].lower()
    known_map = index.get(address) or {}
    values: dict = {
        "phase": phase,
        "map": {"name": known_map.get("displayName") or "", "image": known_map.get("listViewIcon") or ""},
        "queue": "",
        "players": [],
        "teams": [],
        "missing_roles": [],
        "left": None,
        "running": found.get("running"),
        "party": None,
        "picker": None,
        "average": None,
        #: 試合のパーティーの人数（番号の順）。どれかあれば、ページは番号の意味を書く
        "parties": [],
        "hidden_names": False,
        "every": min(EVERY[where(found)][0], SOON) if to_int(found.get("waiting")) > 0 else EVERY[where(found)][0],
        # エージェントの絵はマッチング中にブラウザーが取る: エージェント選択ですぐ出る
        "warm": icons(content, lang) if where(found) in ("queue", "found") else [],
    }
    if phase == "menus" and isinstance(found.get("party"), dict):
        values["party"] = _party(found["party"], look, me, queue_name, float(found.get("at") or 0))
    if phase == "pregame":
        team = as_dict(match.get("AllyTeam"))
        rows = [
            _player(item, look, me, "CompetitiveTier")
            for item in as_list(team.get("Players"))
            if isinstance(item, dict)
        ]
        values["players"] = rows
        values["average"] = average(rows, look)
        values["parties"] = _number_parties(rows, found.get("parties") or {})
        values["queue"] = queue_name(str(match.get("QueueID") or ""))
        values["left"] = to_int(match.get("PhaseTimeRemainingNS")) // 1_000_000_000 or None
        values["enemy_locked"] = to_int(match.get("EnemyTeamLockCount"))
        values["enemy_size"] = to_int(match.get("EnemyTeamSize")) or 5
        picked = {row["role"] for row in rows if row["state"] in ("selected", "locked") and not row["me"]}
        taken = {lower_id(item.get("CharacterID")) for item in as_list(team.get("Players")) if isinstance(item, dict)}
        roles = {lower_id(as_dict(agent.get("role")).get("uuid")): agent.get("role") for agent in agents.values()}
        for role in ROLES:
            if role in picked or role not in roles:
                continue
            choices = [
                {"name": agent.get("displayName") or "", "icon": icon(agent)}
                for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                if lower_id(as_dict(agent.get("role")).get("uuid")) == role
                and uuid not in taken
                and (owned_agents is None or uuid in owned_agents)
            ]
            values["missing_roles"].append(
                {"name": (roles[role] or {}).get("displayName") or "", "agents": choices[:SUGGESTED]}
            )
        mine = next((row for row in rows if row["me"]), None)
        if mine is not None:
            # 持ち主がピックできるエージェント: 持っているもの（読めなければ全部）。ただし味方がロックしたものは除く
            locked = {row["agent_id"] for row in rows if row["state"] == "locked" and not row["me"]}
            seen = {"matches": 0, "agents": []}
            if agent_stats and known_map.get("uuid"):
                seen = agent_stats(lower_id(known_map["uuid"]), lower_id(match.get("QueueID")))
            here = {item["agent"]: item for item in seen["agents"] if item["picks"] >= FEW}
            values["picker"] = {
                "current": mine["agent_id"] if mine["state"] in ("selected", "locked") else "",
                "current_name": mine["agent"],
                # ロックしたエージェントは大きく出す: 全身の絵、ゲームに無ければ四角
                "portrait": str(
                    (agents.get(mine["agent_id"]) or {}).get("fullPortrait")
                    or (agents.get(mine["agent_id"]) or {}).get("displayIcon")
                    or ""
                ),
                "current_role": ((agents.get(mine["agent_id"]) or {}).get("role") or {}).get("displayName") or "",
                "locked": mine["state"] == "locked",
                "agents": [
                    {
                        "id": uuid,
                        "name": agent.get("displayName") or "",
                        "icon": icon(agent),
                        "role": (as_dict(agent.get("role")).get("displayName")) or "",
                        "taken": uuid in locked,
                        "win": here[uuid]["win"] if uuid in here else None,
                    }
                    for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                    if owned_agents is None or uuid in owned_agents
                ],
                "matches": seen["matches"],
            }
            # このマップで一番勝っている持ち主のエージェント（言えるくらいピックしたもの）
            choices = [item for item in values["picker"]["agents"] if item["win"] is not None and not item["taken"]]
            values["picker"]["best"] = sorted(choices, key=lambda item: -item["win"])[:BEST_SHOWN]
    elif phase == "ingame":
        rows = [
            _player(item, look, me, "")
            for item in as_list(match.get("Players"))
            if isinstance(item, dict) and not item.get("IsCoach")
        ]
        mine = next((row["team"] for row in rows if row["me"]), "")
        groups: dict[str, list] = {}
        for row in rows:
            groups.setdefault(row["team"], []).append(row)
        ordered = sorted(groups.items(), key=lambda pair: pair[0] != mine)
        # 番号は味方の席から: 持ち主のパーティーが 1、次に味方、最後に相手
        values["parties"] = _number_parties(
            [row for _, players in ordered for row in players], found.get("parties") or {}
        )
        values["teams"] = [
            {
                "ours": team == mine,
                "players": players,
                "average": average(players, look),
                "parties": _team_parties(players),
            }
            for team, players in ordered
        ]
        making = as_dict(match.get("MatchmakingData"))
        values["queue"] = queue_name(str(making.get("QueueID") or ""))
    # ページの誰かが名前を隠している: そのときだけ、名前が無い理由をページに書く
    shown = values["players"] + [row for team in values["teams"] for row in team["players"]]
    shown += (values["party"] or {}).get("members") or []
    values["hidden_names"] = any(not row["name"] and not row["me"] for row in shown)
    return values


#: 持ち主のゲームの状態を短く（ヘッダーのライブタブ、トップページの自分の枠）。
STATE_WORDS = {
    "menus": "メニュー",
    "queue": "マッチング中",
    "found": "試合が見つかりました",
    "pregame": "エージェント選択中",
    "ingame": "対戦中",
    "unknown": "状態を取得できません",
    "closed": "VALORANT 未起動",
}


def where(found: dict) -> str:
    """持ち主のゲームの状態（riot.Riot.live の答え）: "menus"、"queue"（マッチング中）、
    "found"（試合が見つかった: エージェント選択前のゲームの 5 秒カウント）、"pregame"、"ingame"、 ""（閉じているか不明）
    。"""
    phase = found.get("phase") or ""
    party = as_dict(found.get("party"))
    if phase == "menus":
        party_state = str(party.get("State") or "").upper()
        return "queue" if party_state == "MATCHMAKING" else "found" if "STARTING" in party_state else "menus"
    return phase if phase in EVERY else ""


def state(found: dict, content: Content, lang: str) -> dict:
    """全ページのヘッダーのライブタブ用の、持ち主のゲームの状態の短い文（/live/state）
    : ``{"phase": where(found), "words", "map", "match": 試合の ID（エージェント選択と試合）, "running", "every": タブが次に聞くまでの秒数, "icons": エージェント選択の前に用意しておくエージェントの絵（マッチング中と試合が見つかったときだけ）
    }``。ページの言語（``lang``）で。"""
    phase = where(found)
    match = as_dict(found.get("match"))
    place = ""
    if phase in ("pregame", "ingame"):
        address = str(match.get("MapID") or "").rsplit("/", 1)[-1].lower()
        place = (content.map_index(lang).get(address) or {}).get("displayName") or ""
    running = found.get("running")
    words = STATE_WORDS.get(phase) or STATE_WORDS["unknown" if running is None else "closed"]
    words = i18n.text(lang, words)
    return {
        "phase": phase,
        "words": i18n.text(lang, "{state}（{map}）", state=words, map=place) if place else words,
        "map": place,
        "match": lower_id(match.get("ID") or match.get("MatchID")) if phase in ("pregame", "ingame") else "",
        "running": running,
        "every": EVERY[phase][1],
        "icons": icons(content, lang) if phase in ("queue", "found") else [],
    }


def icons(content: Content, lang: str) -> list[str]:
    """パーティーがマッチング中にブラウザーが取っておく全エージェントの小さい絵（アイコン）
    : エージェント選択で四角がすぐ出る（絵はブラウザーに 1 か月残る）。"""
    return sorted({icon(agent) for agent in content.agent_index(lang).values() if icon(agent)})
