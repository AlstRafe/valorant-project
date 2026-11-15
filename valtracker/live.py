"""持ち主の今のゲーム（/live）。GLZ から（riot.Riot.live）: VALORANT が開いているか。メニューでは、パーティー、キュー、
マッチングの開始・停止ボタン。エージェント選択では、チームのピック、まだ足りないロール、
持ち主のエージェントのピックとロック。試合では、全員のエージェント・カード・称号・レベル・ランク、各チームの平均ランク、
名前を出しているプレイヤーのアクトの戦績。

名前はゲームで隠していない（``Incognito`` でない）プレイヤーと、持ち主と同じパーティーのプレイヤー（ゲームの設定も
「パーティー外のプレイヤーに名前を隠す」: 仲間には見える）だけ、レベルは隠していない（``HideAccountLevel`` でない）
ときだけ出す: このページはゲーム自身の画面以上のことを言わない。"""

from __future__ import annotations

from datetime import datetime

from valtracker import i18n, smurf
from valtracker.activity import FEW
from valtracker.content import Content, todays_tier
from valtracker.util import lower_id, to_int

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


def icon(agent: dict) -> str:
    """小さい四角（ピック、席、ロール）用のエージェントの絵: あれば valorant-api.com の小さいもの（128 px。
    256 px のもののバイト数の 4 分の 1）。"""
    return str(agent.get("displayIconSmall") or agent.get("displayIcon") or "")


def _seasons(mmr) -> dict:
    """PD の mmr の答えのコンペティティブのアクトごとの情報（アクト ID → 情報）。"""
    skills = (
        ((mmr.get("QueueSkills") or {}).get("competitive") or {}).get("SeasonalInfoBySeasonID") or {}
        if isinstance(mmr, dict)
        else {}
    )
    return skills if isinstance(skills, dict) else {}


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
    latest = mmr.get("LatestCompetitiveUpdate") if isinstance(mmr.get("LatestCompetitiveUpdate"), dict) else {}
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
        won_at = info.get("WinsByTier") if isinstance(info.get("WinsByTier"), dict) else {}
        tiers = [to_int(tier) for tier, count in won_at.items() if to_int(count) > 0]
        tiers.append(to_int(info.get("CompetitiveTier")))
        best = max(best, todays_tier(max(tiers), lower_id(season) in old_acts))
    return best


def act_best(mmr, act: str) -> int:
    """プレイヤーのそのアクトでの最高ランク（勝った tier と、今の tier）。0: 無し。"""
    info = _act_info(mmr, act)
    if info is None:
        return 0
    won_at = info.get("WinsByTier") if isinstance(info.get("WinsByTier"), dict) else {}
    tiers = [to_int(tier) for tier, count in won_at.items() if to_int(count) > 0]
    return max([*tiers, to_int(info.get("CompetitiveTier"))])


def _smurf(tier: int, level: int | None, mmr, act: str) -> dict | None:
    """レベル、ランク戦のアクト、名前を出しているプレイヤー（``act``）
    なら今のアクトの戦績からのサブ垢の推測（smurf.guess）: 名前を隠している人は戦績無しで推測する。"""
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
    identity = item.get("PlayerIdentity") if isinstance(item.get("PlayerIdentity"), dict) else {}
    agent = look["agents"].get(lower_id(item.get("CharacterID"))) or {}
    subject = lower_id(item.get("Subject"))
    # 同じパーティーの仲間（Riot のパーティーのメンバー、riot.Riot.live）にはゲームも名前を見せる
    mate = subject in look["mates"] and subject != me
    hidden = bool(identity.get("Incognito")) and not mate
    name = look["names"].get(subject) if not hidden else None
    badge = item.get("SeasonalBadgeInfo") if isinstance(item.get("SeasonalBadgeInfo"), dict) else {}
    tier, rr = _rank(look["ranks"].get(subject), look["act"])
    if tier <= 2:
        tier, rr = to_int(item.get(rank_key) if rank_key in item else badge.get("Rank")), None
    rank = _rank_of(tier, look)
    level = None if identity.get("HideAccountLevel") else to_int(identity.get("AccountLevel")) or None
    card = lower_id(identity.get("PlayerCardID"))
    title = look["titles"].get(lower_id(identity.get("PlayerTitleID"))) or {}
    shown = bool(name) or subject == me
    return {
        "me": subject == me,
        "agent": agent.get("displayName") or "",
        "agent_icon": icon(agent),
        "agent_id": lower_id(item.get("CharacterID")),
        "role": lower_id((agent.get("role") or {}).get("uuid")),
        "state": str(item.get("CharacterSelectionState") or ""),
        "name": name[0] if name else "",
        "tag": name[1] if name else "",
        "mate": mate,
        # 隠しているプレイヤーはカードと称号も出さない: 誰かわかってしまうことがある
        "card": f"https://media.valorant-api.com/playercards/{card}/smallart.png"
        if card and not hidden and len(card) == 36
        else "",
        "title": "" if hidden else str(title.get("titleText") or ""),
        "level": level,
        "rank": {**rank, "rr": rr} if rank else None,
        "tier": tier if tier > 2 else 0,
        # アクトの戦績は持ち主と名前が出ているプレイヤー（パーティーの仲間も）だけ: このサイトのその人のページと同じ公開度。
        # 3 つのランク（今、アクト最高、通算最高）は全員: 持ち主が名前を隠している人にも出すよう頼んだ（2026-11-15）。
        # ランクでは誰かわからない
        "record": _record(look["ranks"].get(subject), look["act"]) if shown else None,
        "peak": _rank_of(best_ever(look["ranks"].get(subject), look.get("old_acts") or frozenset()), look),
        "act_peak": _rank_of(act_best(look["ranks"].get(subject), look["act"]), look),
        "smurf": _smurf(tier, level, look["ranks"].get(subject), look["act"] if shown else ""),
        "team": str(item.get("TeamID") or ""),
        "owner": bool(item.get("IsOwner")),
    }


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
        _player(item, look, me, "CompetitiveTier") for item in party.get("Members") or [] if isinstance(item, dict)
    ]
    making = party.get("MatchmakingData") if isinstance(party.get("MatchmakingData"), dict) else {}
    state = str(party.get("State") or "")
    current = lower_id(making.get("QueueID"))
    eligible = [lower_id(queue) for queue in party.get("EligibleQueues") or [] if isinstance(queue, str) and queue]
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
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
    agents = content.agent_index(lang)
    act = content.act(lang)
    look = {
        "agents": agents,
        "tiers": content.tiers(lang),
        "titles": content.index("playertitles", lang),
        "names": found.get("names") or {},
        "ranks": found.get("ranks") or {},
        "mates": {lower_id(subject) for subject in found.get("mates") or []},
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
        "hidden_names": False,
        "every": EVERY[where(found)][0],
        # エージェントの絵はマッチング中にブラウザーが取る: エージェント選択ですぐ出る
        "warm": icons(content, lang) if where(found) in ("queue", "found") else [],
    }
    if phase == "menus" and isinstance(found.get("party"), dict):
        values["party"] = _party(found["party"], look, me, queue_name, float(found.get("at") or 0))
    if phase == "pregame":
        team = match.get("AllyTeam") if isinstance(match.get("AllyTeam"), dict) else {}
        rows = [
            _player(item, look, me, "CompetitiveTier") for item in team.get("Players") or [] if isinstance(item, dict)
        ]
        values["players"] = rows
        values["average"] = average(rows, look)
        values["queue"] = queue_name(str(match.get("QueueID") or ""))
        values["left"] = to_int(match.get("PhaseTimeRemainingNS")) // 1_000_000_000 or None
        values["enemy_locked"] = to_int(match.get("EnemyTeamLockCount"))
        values["enemy_size"] = to_int(match.get("EnemyTeamSize")) or 5
        picked = {row["role"] for row in rows if row["state"] in ("selected", "locked") and not row["me"]}
        taken = {lower_id(item.get("CharacterID")) for item in team.get("Players") or [] if isinstance(item, dict)}
        roles = {lower_id((agent.get("role") or {}).get("uuid")): agent.get("role") for agent in agents.values()}
        for role in ROLES:
            if role in picked or role not in roles:
                continue
            choices = [
                {"name": agent.get("displayName") or "", "icon": icon(agent)}
                for uuid, agent in sorted(agents.items(), key=lambda pair: str(pair[1].get("displayName") or ""))
                if lower_id((agent.get("role") or {}).get("uuid")) == role
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
                        "role": ((agent.get("role") or {}).get("displayName")) or "",
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
            for item in match.get("Players") or []
            if isinstance(item, dict) and not item.get("IsCoach")
        ]
        mine = next((row["team"] for row in rows if row["me"]), "")
        groups: dict[str, list] = {}
        for row in rows:
            groups.setdefault(row["team"], []).append(row)
        values["teams"] = [
            {"ours": team == mine, "players": players, "average": average(players, look)}
            for team, players in sorted(groups.items(), key=lambda pair: pair[0] != mine)
        ]
        making = match.get("MatchmakingData") if isinstance(match.get("MatchmakingData"), dict) else {}
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
    party = found.get("party") if isinstance(found.get("party"), dict) else {}
    if phase == "menus":
        party_state = str(party.get("State") or "").upper()
        return "queue" if party_state == "MATCHMAKING" else "found" if "STARTING" in party_state else "menus"
    return phase if phase in EVERY else ""


def state(found: dict, content: Content, lang: str) -> dict:
    """全ページのヘッダーのライブタブ用の、持ち主のゲームの状態の短い文（/live/state）
    : ``{"phase": where(found), "words", "map", "match": 試合の ID（エージェント選択と試合）, "running", "every": タブが次に聞くまでの秒数, "icons": エージェント選択の前に用意しておくエージェントの絵（マッチング中と試合が見つかったときだけ）
    }``。ページの言語（``lang``）で。"""
    phase = where(found)
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
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
