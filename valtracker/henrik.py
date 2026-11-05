"""Players and matches, from the HenrikDev API (https://docs.henrikdev.xyz): an unofficial API that answers
for any Riot ID, with a free key the owner makes on its dashboard (settings.py keeps it).

Riot's own API needs a production review before it answers VALORANT match questions, so this is the way in.
Every answer is kept a while (TTLCache) and the calls share one budget (Budget), because the key's
allowance is shared by every visitor.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlencode

from valtracker import net
from valtracker.cache import Budget, TTLCache

BASE = "https://api.henrikdev.xyz"
PLATFORM = "pc"
#: How long each kind of answer is kept (seconds). A finished match never changes.
KEEP = {"account": 6 * 3600, "mmr": 300, "matches": 180, "match": 7 * 24 * 3600}
#: A Riot ID: the name (3-16 characters, spaces allowed) and the tag (3-5 letters or digits).
NAME = re.compile(r"^[^#/\\?<>\x00-\x1f]{1,16}$")
TAG = re.compile(r"^[^\W_]{2,5}$")
MATCH_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REGIONS = ("ap", "br", "eu", "kr", "latam", "na")
#: How many matches a player's page lists: as many as the game's own career page shows.
RECENT = 15
#: The most matches one answer gives, so the list is asked for in two parts, side by side.
PAGE = 10
#: The modes a player's matches can be narrowed to: HenrikDev's name for it -> the queue id in its matches.
MODES = {
    "competitive": "competitive",
    "unrated": "unrated",
    "swiftplay": "swiftplay",
    "spikerush": "spikerush",
    "deathmatch": "deathmatch",
    "teamdeathmatch": "hurm",
    "escalation": "ggteam",
    "premier": "premier",
}
#: What the pages use of a match. The rest (every round and kill, about 0.5 MB a match) is dropped before
#: it is kept, so the memory of the small VM holds hundreds of matches instead of a few dozen.
METADATA = ("match_id", "map", "game_length_in_ms", "started_at", "is_completed", "queue", "season", "region")
PLAYER = ("puuid", "name", "tag", "team_id", "party_id", "agent", "stats", "tier", "account_level")
TEAM = ("team_id", "rounds", "won")


class HenrikError(Exception):
    """Why there is no answer: ``kind`` is one of nokey (no API key set), badkey (the key was refused),
    notfound, busy (too many calls just now, ours or theirs), down (no answer, or one we cannot read)."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def split_riot_id(text: str) -> tuple[str, str] | None:
    """``Name#TAG`` (a full-width ＃ too, spaces around it allowed) -> (name, tag), or None."""
    name, mark, tag = text.strip().replace("＃", "#").rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not mark or not NAME.match(name) or not TAG.match(tag):
        return None
    return name, tag


class Henrik:
    def __init__(self, key, fetch: net.Fetch = net.fetch, budget: Budget | None = None):
        #: () -> the API key ("" when none is set): read on every call, so a key saved in the settings works at once.
        self.key = key
        self.fetch = fetch
        self.budget = budget or Budget(25, 60.0)
        self.cache = TTLCache(1024)

    def account(self, name: str, tag: str) -> dict:
        """``{"puuid", "region", "account_level", "name", "tag", "card", "title"}``."""
        path = f"/valorant/v2/account/{_part(name)}/{_part(tag)}"
        return self._get(("account", name.lower(), tag.lower()), path, KEEP["account"])

    def mmr(self, region: str, name: str, tag: str) -> dict:
        """``{"current": {"tier": {"id", "name"}, "rr", "last_change", ...}, "peak": ..., "seasonal": [...]}``."""
        path = f"/valorant/v3/mmr/{_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}"
        slim = lambda data: _pick(data, ("current", "peak"))  # noqa: E731 - the seasons' history is not shown
        return self._get(("mmr", region, name.lower(), tag.lower()), path, KEEP["mmr"], slim)

    def matches(self, region: str, name: str, tag: str, mode: str = "") -> list:
        """The latest RECENT matches (of one of MODES, or of any), newest first, each as ``match`` gives it.
        Without the newest part there is no list (HenrikError); without the older one it is shorter."""
        with ThreadPoolExecutor(2, thread_name_prefix="henrik-matches") as pool:
            parts = [pool.submit(self._matches, region, name, tag, mode, start) for start in (0, PAGE)]
        found = parts[0].result()
        try:
            found = found + parts[1].result()
        except HenrikError:
            pass
        wanted = MODES.get(mode)
        seen, kept = set(), []
        for match in found:
            metadata = match["metadata"]
            match_id = str(metadata.get("match_id") or "")
            if match_id in seen:  # an older part that came back the same as the newest
                continue
            seen.add(match_id)
            if wanted and str((metadata.get("queue") or {}).get("id") or "").lower() != wanted:
                continue
            if match_id and metadata.get("is_completed", True):  # each one is a whole match: opening it costs no call
                self.cache.put(("match", match_id), match, KEEP["match"])
            kept.append(match)
        return kept[:RECENT]

    def _matches(self, region: str, name: str, tag: str, mode: str, start: int) -> list:
        size = min(PAGE, RECENT - start)
        query = {"size": size} | ({"start": start} if start else {}) | ({"mode": mode} if mode in MODES else {})
        path = f"/valorant/v4/matches/{_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}?" + urlencode(query)
        key = _matches_key(region, name, tag, mode, start)
        return self._get(key, path, KEEP["matches"], _slim_matches)

    def has_player(self, name: str, tag: str, mode: str = "") -> bool:
        """Whether the player's account, rank and matches are all kept: their page needs no call."""
        account = self.cache.get(("account", name.lower(), tag.lower()))
        if not isinstance(account, dict):
            return False
        region = str(account.get("region") or "ap")
        name, tag = str(account.get("name") or name), str(account.get("tag") or tag)
        keys = [("mmr", region, name.lower(), tag.lower())]
        keys += [_matches_key(region, name, tag, mode, start) for start in (0, PAGE)]
        return all(self.cache.get(key) is not None for key in keys)

    def match(self, region: str, match_id: str) -> dict:
        """``{"metadata", "players", "teams", "rounds", "kills", ...}``."""
        if not MATCH_ID.match(match_id):
            raise HenrikError("notfound")
        kept = self.cache.get(("match", match_id))
        if kept is not None:
            return kept
        path = f"/valorant/v4/match/{_region(region)}/{match_id}"
        return self._get(("match", match_id), path, KEEP["match"], _slim_match)

    def _get(self, cache_key, path: str, seconds: float, shape=None):
        kept = self.cache.get(cache_key)
        if kept is not None:
            return kept
        key = self.key()
        if not key:
            raise HenrikError("nokey")
        if not self.budget.take():
            raise HenrikError("busy")
        status, body = self.fetch(BASE + path, {"Authorization": key, "Accept": "application/json"})
        if status in (401, 403):
            raise HenrikError("badkey")
        if status == 404:
            raise HenrikError("notfound")
        if status == 429:
            raise HenrikError("busy")
        if status != 200:
            raise HenrikError("down")
        try:
            data = json.loads(body.decode("utf-8")).get("data")
        except (ValueError, AttributeError, UnicodeDecodeError):
            raise HenrikError("down") from None
        if data is None:
            raise HenrikError("notfound")
        if shape is not None:
            data = shape(data)
        self.cache.put(cache_key, data, seconds)
        return data


def _matches_key(region: str, name: str, tag: str, mode: str, start: int) -> tuple:
    return ("matches", region, name.lower(), tag.lower(), mode if mode in MODES else "", start)


def _pick(item, keys) -> dict:
    return {key: item.get(key) for key in keys} if isinstance(item, dict) else {}


def _slim_match(match) -> dict:
    """A match with what the pages use only (METADATA, PLAYER, TEAM)."""
    if not isinstance(match, dict) or not isinstance(match.get("metadata"), dict):
        raise HenrikError("down")
    return {
        "metadata": _pick(match["metadata"], METADATA),
        "players": [_pick(player, PLAYER) for player in match.get("players") or [] if isinstance(player, dict)],
        "teams": [_pick(team, TEAM) for team in match.get("teams") or [] if isinstance(team, dict)],
    }


def _slim_matches(matches) -> list:
    if not isinstance(matches, list):
        raise HenrikError("down")
    return [_slim_match(match) for match in matches if isinstance(match, dict) and match.get("metadata")]


def _part(text: str) -> str:
    return quote(text, safe="")


def _region(region: str) -> str:
    region = str(region or "").lower()
    return region if region in REGIONS else "ap"
