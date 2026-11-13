"""The owner's friends as the Riot Client on this PC lists them (the PC app only; the site has no client to ask):
who is in VALORANT and where (the menus, matchmaking, agent select, a match and its score), in a party of how many,
with their rank and level; who is in another of Riot's games; who is away; who was last here when.

All of it is what the client's own friends list shows, read from the client's chat on this PC
(localclient.LocalClient.friends: two local reads). Nothing is asked of the game or of Riot's servers, and nothing
leaves this PC.

A presence's private part has had two shapes: every field at the top (before 2024), or in matchPresenceData,
partyPresenceData and playerPresenceData. Both are read (``_field``).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from valtracker.localclient import _private

#: The parts of a presence's private part a field can be in (after the top).
PARTS = ("matchPresenceData", "partyPresenceData", "playerPresenceData", "premierPresenceData")
#: Riot's other games, as a presence's ``product`` names them.
PRODUCTS = {
    "league_of_legends": "League of Legends",
    "bacon": "Legends of Runeterra",
    "lion": "2XKO",
    "riot_client": "Riot Client",
    "keystone": "Riot Client",
}
#: How a friend's place is said (the words of the header's ライブ tab, live.STATE_WORDS, and a few more).
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
#: The order of the friends in VALORANT: in a match first, then getting into one, then the menus, then away.
ORDER = ("ingame", "pregame", "found", "queue", "custom", "range", "menus", "away")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _field(data: dict, key: str):
    """A field of a presence's private part: at the top, else in one of its parts."""
    if data.get(key) not in (None, ""):
        return data[key]
    for part in PARTS:
        inner = data.get(part)
        if isinstance(inner, dict) and inner.get(key) not in (None, ""):
            return inner[key]
    return None


def _number(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _id(value) -> str:
    text = str(value or "").lower()
    return text if _UUID.match(text) else ""


def place(data: dict, away: bool) -> str:
    """Where a VALORANT presence says the player is: one of WORDS' keys."""
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
    """What a VALORANT presence tells of a friend."""
    data = _private(item.get("private"))
    where = place(data, str(item.get("state") or "") == "away")
    queue = str(_field(data, "queueId") or "").lower()
    address = str(_field(data, "matchMap") or _field(data, "partyOwnerMatchMap") or "").rsplit("/", 1)[-1].lower()
    known_map = look["maps"].get(address) or {}
    tier = _number(_field(data, "competitiveTier"))
    known = look["tiers"].get(tier) or {}
    rank_name = str(known.get("tierName") or "")
    card = _id(_field(data, "playerCardId"))
    party = _id(_field(data, "partyId"))
    size, room = _number(_field(data, "partySize")), _number(_field(data, "maxPartySize"))
    playing = where in ("ingame", "pregame")
    ours, theirs = (
        _number(_field(data, "partyOwnerMatchScoreAllyTeam")),
        _number(_field(data, "partyOwnerMatchScoreEnemyTeam")),
    )
    return {
        "where": where,
        "words": WORDS[where],
        # the queue of the match, or the one being waited for (the menus keep the last one chosen: not said)
        "queue": look["queue_name"](queue) if queue and where in ("ingame", "pregame", "found", "queue") else "",
        "map": str(known_map.get("displayName") or "") if playing else "",
        "score": f"{ours}–{theirs}" if where == "ingame" and (ours or theirs) else "",
        "rank": {"name": rank_name.title() if rank_name.isupper() else rank_name, "icon": known.get("smallIcon")}
        if tier > 2 and rank_name
        else None,
        "level": _number(_field(data, "accountLevel")) or None,
        "card": f"https://media.valorant-api.com/playercards/{card}/smallart.png" if card else "",
        "party": size if size > 1 else 0,
        "room": room,
        # a party anyone of its friends may join, with a place left
        "open": str(_field(data, "partyAccessibility") or "").upper() == "OPEN" and 0 < size < (room or 5),
        "mine": bool(party) and party == my_party,
        "party_id": party,
    }


def _since(value, now: float, when) -> dict | None:
    """``last_online_ts`` (milliseconds) as the page says a moment (``when``), None when not known."""
    moment = _number(value) / 1000
    if moment <= 0 or moment > now + 86400:
        return None
    return when(datetime.fromtimestamp(moment, UTC))


def rows(found: dict, look: dict, now: float, when) -> dict:
    """``found`` (LocalClient.friends) -> ``{"playing": friends in VALORANT, "online": friends elsewhere at Riot,
    "offline": the rest, "total"}``. ``look``: ``{"tiers", "maps", "queue_name"}`` in the page's language;
    ``when(datetime)``: how the page says a moment."""
    me = str(found.get("me") or "").lower()
    presences: dict[str, list[dict]] = {}
    for item in found.get("presences") or []:
        presences.setdefault(str(item.get("puuid") or "").lower(), []).append(item)
    mine = next((item for item in presences.get(me, []) if str(item.get("product") or "").lower() == "valorant"), None)
    my_party = _id(_field(_private(mine.get("private")), "partyId")) if mine else ""
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
            row["at"] = _number(friend.get("last_online_ts"))
            offline.append(row)
    # the owner's own party first, then by where they are, then parties together, then by name
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
