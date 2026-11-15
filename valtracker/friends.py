"""この PC の Riot Client が出す持ち主のフレンド（PC アプリだけ。サイトには聞けるクライアントが無い）
: 誰が VALORANT のどこにいるか（メニュー、マッチング中、エージェント選択、試合とそのスコア）、何人のパーティーか、
ランクとレベル。Riot のほかのゲームにいる人、離席中の人、最後にいついたか。

どれもクライアント自身のフレンドリストに出るもので、
この PC のクライアントのチャットから読む（localclient.LocalClient.friends: ローカルの読み込み 2 回）。
ゲームにも Riot のサーバーにも何も聞かず、この PC から何も出ていかない。

プレゼンスの private の部分は 2 つの形があった: すべての項目が一番上（2024 年より前）、または matchPresenceData・
partyPresenceData・playerPresenceData の中。どちらも読む（``_field``）。"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from valtracker.localclient import decode_private
from valtracker.util import to_int

#: private の中で項目が入りうる部分（一番上の次に見る）。
PARTS = ("matchPresenceData", "partyPresenceData", "playerPresenceData", "premierPresenceData")
#: プレゼンスの ``product`` での Riot のほかのゲームの名前。
PRODUCTS = {
    "league_of_legends": "League of Legends",
    "bacon": "Legends of Runeterra",
    "lion": "2XKO",
    "riot_client": "Riot Client",
    "keystone": "Riot Client",
}
#: フレンドの居場所の言い方（ヘッダーのライブタブの文 live.STATE_WORDS と、いくつか追加）。
WORDS = {
    "ingame": "対戦中",
    "pregame": "エージェント選択中",
    "found": "試合が見つかりました",
    "queue": "マッチング中",
    "custom": "カスタムゲームの準備中",
    "range": "射撃場",
    "menus": "メニュー",
    "away": "離席中",
}
#: VALORANT にいるフレンドの順: 試合中、試合に入るところ、メニュー、離席。
ORDER = ("ingame", "pregame", "found", "queue", "custom", "range", "menus", "away")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _field(data: dict, key: str):
    """private の項目: 一番上、無ければ部分の中。"""
    if data.get(key) not in (None, ""):
        return data[key]
    for part in PARTS:
        inner = data.get(part)
        if isinstance(inner, dict) and inner.get(key) not in (None, ""):
            return inner[key]
    return None


def _id(value) -> str:
    text = str(value or "").lower()
    return text if _UUID.match(text) else ""


def place(data: dict, away: bool) -> str:
    """VALORANT のプレゼンスが示すプレイヤーの居場所: WORDS のキーのどれか。"""
    loop = str(_field(data, "sessionLoopState") or "").upper()
    party = str(_field(data, "partyState") or "").upper()
    flow = str(_field(data, "provisioningFlow") or "").lower()
    if loop == "INGAME":
        return "range" if flow == "shootingrange" else "ingame"
    if loop == "PREGAME":
        return "pregame"
    if party == "MATCHMADE_GAME_STARTING":
        return "found"
    if party == "MATCHMAKING":
        return "queue"
    if party == "CUSTOM_GAME_SETUP":
        return "custom"
    if away or _field(data, "isIdle") is True:
        return "away"
    return "menus"


def _playing(item: dict, look: dict, my_party: str) -> dict:
    """VALORANT のプレゼンスからわかるフレンドの情報。"""
    data = decode_private(item.get("private"))
    where = place(data, str(item.get("state") or "") == "away")
    queue = str(_field(data, "queueId") or "").lower()
    address = str(_field(data, "matchMap") or _field(data, "partyOwnerMatchMap") or "").rsplit("/", 1)[-1].lower()
    known_map = look["maps"].get(address) or {}
    tier = to_int(_field(data, "competitiveTier"))
    known = look["tiers"].get(tier) or {}
    rank_name = str(known.get("tierName") or "")
    card = _id(_field(data, "playerCardId"))
    party = _id(_field(data, "partyId"))
    size, room = to_int(_field(data, "partySize")), to_int(_field(data, "maxPartySize"))
    playing = where in ("ingame", "pregame")
    ours, theirs = (
        to_int(_field(data, "partyOwnerMatchScoreAllyTeam")),
        to_int(_field(data, "partyOwnerMatchScoreEnemyTeam")),
    )
    return {
        "where": where,
        "words": WORDS[where],
        # 試合のキュー、または待っているキュー（メニューは最後に選んだものを持つので出さない）
        "queue": look["queue_name"](queue) if queue and where in ("ingame", "pregame", "found", "queue") else "",
        "map": str(known_map.get("displayName") or "") if playing else "",
        "score": f"{ours}–{theirs}" if where == "ingame" and (ours or theirs) else "",
        "rank": {"name": rank_name.title() if rank_name.isupper() else rank_name, "icon": known.get("smallIcon")}
        if tier > 2 and rank_name
        else None,
        "level": to_int(_field(data, "accountLevel")) or None,
        "card": f"https://media.valorant-api.com/playercards/{card}/smallart.png" if card else "",
        "party": size if size > 1 else 0,
        "room": room,
        # フレンドなら誰でも入れて、空きがあるパーティー
        "open": str(_field(data, "partyAccessibility") or "").upper() == "OPEN" and 0 < size < (room or 5),
        "mine": bool(party) and party == my_party,
        "party_id": party,
    }


def _since(value, now: float, when) -> dict | None:
    """``last_online_ts``（ミリ秒）をページの時刻表記に（``when``）。不明なら None。"""
    moment = to_int(value) / 1000
    if moment <= 0 or moment > now + 86400:
        return None
    return when(datetime.fromtimestamp(moment, UTC))


def rows(found: dict, look: dict, now: float, when) -> dict:
    """``found``（LocalClient.friends）
    -> ``{"playing": VALORANT にいるフレンド, "online": Riot のほかの場所にいるフレンド, "offline": 残り, "total"}``。
    ``look``: ページの言語での ``{"tiers", "maps", "queue_name"}``。``when(datetime)``: ページの時刻表記。"""
    me = str(found.get("me") or "").lower()
    presences: dict[str, list[dict]] = {}
    for item in found.get("presences") or []:
        presences.setdefault(str(item.get("puuid") or "").lower(), []).append(item)
    mine = next((item for item in presences.get(me, []) if str(item.get("product") or "").lower() == "valorant"), None)
    my_party = _id(_field(decode_private(mine.get("private")), "partyId")) if mine else ""
    playing, online, offline, seen = [], [], [], set()
    for friend in found.get("friends") or []:
        puuid = str(friend.get("puuid") or "").lower()
        name, tag = str(friend.get("game_name") or ""), str(friend.get("game_tag") or "")
        if not puuid or puuid == me or puuid in seen or not name:
            continue
        seen.add(puuid)
        row = {"name": name, "tag": tag, "note": str(friend.get("note") or "")[:80]}
        theirs = presences.get(puuid) or []
        game = next((item for item in theirs if str(item.get("product") or "").lower() == "valorant"), None)
        if game is not None:
            playing.append(row | _playing(game, look, my_party))
        elif theirs:
            product = str(theirs[0].get("product") or "").lower()
            row["product"] = PRODUCTS.get(product) or product.replace("_", " ").title()
            row["away"] = str(theirs[0].get("state") or "") in ("away", "mobile")
            online.append(row)
        else:
            row["last"] = _since(friend.get("last_online_ts"), now, when)
            row["at"] = to_int(friend.get("last_online_ts"))
            offline.append(row)
    # 持ち主自身のパーティーが先、次に居場所、同じパーティーはまとめて、最後に名前
    playing.sort(
        key=lambda row: (not row["mine"], ORDER.index(row["where"]), row["party_id"] or "~", row["name"].casefold())
    )
    online.sort(key=lambda row: (row["away"], row["name"].casefold()))
    offline.sort(key=lambda row: (-row["at"], row["name"].casefold()))
    return {
        "playing": playing,
        "online": online,
        "offline": offline,
        "total": len(playing) + len(online) + len(offline),
    }
