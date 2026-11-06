"""Players and matches, from the HenrikDev API (https://docs.henrikdev.xyz): an unofficial API that answers
for any Riot ID, with a free key the owner makes on its dashboard (settings.py keeps it).

Riot's own API needs a production review before it answers VALORANT match questions, so this is the way in.
Every answer is kept a while (TTLCache) and the calls share one budget (Budget), because the key's
allowance is shared by every visitor.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlencode

from valtracker import net, rounds
from valtracker.cache import Budget, TTLCache

BASE = "https://api.henrikdev.xyz"
PLATFORM = "pc"
#: How long each kind of answer is kept (seconds). A finished match never changes.
KEEP = {
    "account": 6 * 3600,
    "mmr": 300,
    "rr": 180,
    "matches": 180,
    "match": 7 * 24 * 3600,
    "leaderboard": 600,
    "status": 300,
    "featured": 1800,
    "news": 1800,
    "esports": 300,
}
#: Players on one page of a leaderboard.
BOARD_PAGE = 50
#: A Riot ID: the name (3-16 characters, spaces allowed) and the tag (3-5 letters or digits).
NAME = re.compile(r"^[^#/\\?<>\x00-\x1f]{1,16}$")
TAG = re.compile(r"^[^\W_]{2,5}$")
#: Full-width ０-９, Ａ-Ｚ and ａ-ｚ -> half-width.
HALF_WIDTH = {
    code: code - 0xFEE0
    for start, end in ((0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A))
    for code in range(start, end + 1)
}
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
#: What the pages use of a match. Every round and kill (about 0.5 MB a match) is boiled down to ``digest``
#: (rounds.py) and dropped before it is kept, so the memory of the small VM holds hundreds of matches.
METADATA = (
    "match_id",
    "map",
    "game_length_in_ms",
    "started_at",
    "is_completed",
    "queue",
    "season",
    "region",
    "cluster",
)
PLAYER = ("puuid", "name", "tag", "team_id", "party_id", "agent", "stats", "tier", "account_level", "economy")
TEAM = ("team_id", "rounds", "won")


class HenrikError(Exception):
    """Why there is no answer: ``kind`` is one of nokey (no API key set), badkey (the key was refused),
    notfound, busy (too many calls just now, ours or theirs), down (no answer, or one we cannot read)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind = kind
        #: What came back, for the owner's eyes (``HTTP 500``, ``no answer``...).
        self.detail = detail


def split_riot_id(text: str) -> tuple[str, str] | None:
    """``Name#TAG`` (a full-width ＃ too, spaces around it allowed) -> (name, tag), or None. A tag typed with the
    Japanese keyboard's full-width letters (ＪＰ１) is the same tag: Riot's tags are half-width."""
    name, mark, tag = text.strip().replace("＃", "#").rpartition("#")
    name, tag = name.strip(), tag.strip().translate(HALF_WIDTH)
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
        """``{"current": {"tier": {"id", "name"}, "rr", "last_change", "games_needed_for_rating",
        "leaderboard_placement", ...}, "peak": ..., "seasons": every act played, the latest first (_slim_act)}``."""
        path = f"/valorant/v3/mmr/{_region(region)}/{PLATFORM}/{_part(name)}/{_part(tag)}"
        return self._get(("mmr", region, name.lower(), tag.lower()), path, KEEP["mmr"], _slim_mmr)

    def rr_changes(self, region: str, name: str, tag: str) -> dict:
        """The recent competitive matches' RR: ``{match id: {"change": +18, "tier": 21, "rr": 42, "elo": 1842,
        "at": unix seconds}}`` (elo: the rank and RR as one number, 100 a rank)."""
        path = f"/valorant/v1/mmr-history/{_region(region)}/{_part(name)}/{_part(tag)}"
        return self._get(("rr", region, name.lower(), tag.lower()), path, KEEP["rr"], _slim_rr)

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
        if shows_rr(mode):
            keys.append(("rr", region, name.lower(), tag.lower()))
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

    def leaderboard(self, region: str, start: int = 0) -> dict:
        """One page of a region's ranked leaderboard: ``{"players": [{"rank", "name", "tag", "puuid", "card",
        "tier", "rr", "wins", "hidden"}], "thresholds": [{"tier", "rr", "from"}], "total", "updated"}``."""
        start = max(0, int(start))
        query = urlencode({"size": BOARD_PAGE, "start_index": start})
        path = f"/valorant/v3/leaderboard/{_region(region)}/{PLATFORM}?{query}"
        return self._get(("leaderboard", _region(region), start), path, KEEP["leaderboard"], _slim_board)

    def matches_by_puuid(self, region: str, puuid: str, size: int = 5) -> list:
        """A player's latest matches by their puuid, only what activity.py keeps of them (the start, server,
        queue, length, and each player's puuid and rank): for the background collector, which has no names."""
        query = urlencode({"size": size})
        path = f"/valorant/v4/by-puuid/matches/{_region(region)}/{PLATFORM}/{_part(puuid)}?{query}"
        return self._get(("by-puuid", puuid), path, KEEP["matches"], _slim_collected)

    def has_board(self, region: str, start: int = 0) -> bool:
        """Whether that page of the leaderboard is kept: it needs no call."""
        return self.cache.get(("leaderboard", _region(region), max(0, int(start)))) is not None

    def status(self, region: str) -> dict:
        """Riot's notices for a region: ``{"maintenances": [...], "incidents": [...]}`` (_slim_status)."""
        path = f"/valorant/v1/status/{_region(region)}"
        return self._get(("status", _region(region)), path, KEEP["status"], _slim_status)

    def featured(self) -> list:
        """The store's featured bundles (the same for everyone): ``[{"uuid", "price", "until", "items"}]``."""
        return self._get(("featured",), "/valorant/v2/store-featured", KEEP["featured"], _slim_featured)

    def news(self, country: str) -> list:
        """Riot's VALORANT news in a language (``ja-jp``, ``en-gb``...): ``[{"title", "at", "category", "url",
        "banner"}]``, the latest first."""
        if self.cache.get(("no-news", country)):
            raise HenrikError("notfound")  # asked a while ago, and there were none: not asked again for a while
        try:
            return self._get(("news", country), f"/valorant/v1/website/{_part(country)}", KEEP["news"], _slim_news)
        except HenrikError as error:
            if error.kind in ("down", "notfound"):
                self.cache.put(("no-news", country), True, KEEP["news"])
            raise

    def esports(self) -> tuple[list, float | None]:
        """The esports schedule (every league): ``[{"at", "state", "league": {"name", "region", "icon"},
        "tournament", "best_of", "vod", "teams": [{"name", "code", "icon", "won", "wins"}]}]``, by time, and
        ``None``, or the time (``time.time()``) of the last schedule that came when a new one does not: that one is
        shown (for up to a day) rather than nothing. After a failure nothing is asked for a while (each try costs
        the shared allowance, up to 2 calls)."""
        fresh = self.cache.get(("esports",))
        if fresh is not None:
            return fresh, None
        last = self.cache.get(("esports-last",))
        waiting = self.cache.get(("esports-wait",))
        if waiting:
            if last:
                return last["games"], last["at"]
            raise HenrikError(*waiting)
        try:
            games = self._esports()
        except HenrikError as error:
            if error.kind in ("nokey", "badkey"):
                raise
            self.cache.put(("esports-wait",), (error.kind, error.detail), ESPORTS_WAIT.get(error.kind, 120))
            if last:
                return last["games"], last["at"]
            raise
        self.cache.put(("esports-last",), {"games": games, "at": time.time()}, ESPORTS_LAST)
        return games, None

    def _esports(self) -> list:
        """A new schedule: the whole one, or when it does not come, the main leagues' (one call). The whole one is
        big: a timeout, an error, or HTTP 429 from HenrikDev, which counts each request it makes to Riot for it
        against the key (docs.henrikdev.xyz/general/rate-limiting); on the live site it was refused every time."""
        path = "/valorant/v1/esports/schedule"
        if not self.cache.get(("no-esports",)):
            try:
                return self._get(("esports",), path, KEEP["esports"], _slim_esports)
            except HenrikError as error:
                if not (error.kind == "down" or _refused(error)):
                    raise
                first = error
                self.cache.put(("no-esports",), True, KEEP["news"])
        else:
            first = HenrikError("down", "the whole schedule did not come a while ago")
        failed = self.cache.get(("no-esports", "leagues"))
        if failed:  # failed a while ago: not asked again for a while (each visit would cost the budget)
            raise HenrikError("down", f"{first.detail or first.kind}; leagues: {failed}")
        try:
            games = self._get(("esports", "leagues"), f"{path}?league={ESPORTS_LEAGUES}", KEEP["esports"], _slim_esports)
        except HenrikError as error:
            if error.kind in ("nokey", "badkey"):
                raise
            detail = f"{first.detail or first.kind}; leagues: {error.detail or error.kind}"
            if error.kind == "busy":  # the allowance is used up for now: another time
                raise HenrikError("busy", detail) from None
            self.cache.put(("no-esports", "leagues"), error.detail or error.kind, KEEP["news"])
            raise HenrikError("down", detail) from None
        if not games:
            raise HenrikError("down", f"{first.detail or first.kind}; leagues: no matches")
        return games

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
            raise HenrikError("notfound", "HTTP 404")
        if status == 429:
            raise HenrikError("busy", "HTTP 429")
        if status != 200:
            raise HenrikError("down", f"HTTP {status}" if status else "no answer within 10 s")
        try:
            data = json.loads(body.decode("utf-8")).get("data")
        except (ValueError, AttributeError, UnicodeDecodeError):
            raise HenrikError("down", f"an answer that is not JSON ({len(body):,} bytes)") from None
        if data is None:
            raise HenrikError("notfound")
        if shape is not None:
            data = shape(data)
        self.cache.put(cache_key, data, seconds)
        return data


#: The leagues asked for (in one call) when the whole esports schedule does not come, as HenrikDev's documentation
#: names them (comma-separated, docs.henrikdev.xyz/valorant/api-reference/esports).
ESPORTS_LEAGUES = "vct-international,vct-americas,vct-emea,vct-pacific,vct-china"
#: How long the last schedule that came is shown when a new one does not (seconds), and how long nothing is asked
#: after a failure: a short while when the allowance was used up, longer when HenrikDev did not answer.
ESPORTS_LAST = 24 * 3600
ESPORTS_WAIT = {"busy": 20, "down": 120}


def _refused(error: HenrikError) -> bool:
    """HenrikDev itself said the key has asked too much (HTTP 429), not this site's own budget."""
    return error.kind == "busy" and error.detail == "HTTP 429"


def shows_rr(mode: str) -> bool:
    """Whether a list of this mode has competitive matches, whose RR changes are worth a call."""
    return mode in ("", "competitive") or mode not in MODES


def _slim_mmr(data) -> dict:
    slim = _pick(data, ("current", "peak"))
    seasons = [_slim_act(season) for season in (data or {}).get("seasonal") or [] if isinstance(season, dict)]
    slim["seasons"] = sorted(
        (act for act in seasons if act["games"]), key=lambda act: _act_order(act["short"]), reverse=True
    )
    return slim


#: The act rank badge (the triangle, "onigiri") shows this many wins: the best ones.
BADGE_WINS = 9


def _slim_act(season: dict) -> dict:
    """One act of v3 mmr's ``seasonal``: its record, final rank, and the ranks of its best wins (``act_wins``)."""

    def number(value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def tier(value) -> int:
        return number(value.get("id")) if isinstance(value, dict) else number(value)

    short = str((season.get("season") or {}).get("short") or "") if isinstance(season.get("season"), dict) else ""
    board = season.get("leaderboard_placement")
    wins = sorted((tier(win) for win in season.get("act_wins") or []), reverse=True)
    return {
        "short": short.lower(),
        "games": number(season.get("games")),
        "wins": number(season.get("wins")),
        "end_tier": tier(season.get("end_tier")),
        # before Episode 5 the ranks had no Ascendant, so the same number was another rank
        "old": str(season.get("ranking_schema") or "") == "base" or (_act_order(short)[:1] or (99,))[0] < 5,
        "board": number(board.get("rank")) if isinstance(board, dict) else 0,
        "best": [win for win in wins if win > 2][:BADGE_WINS],
    }


def _act_order(short: str) -> tuple:
    """ "e9a3" -> (9, 3): the latest act is the largest, whatever order the list is in."""
    return tuple(int(number) for number in re.findall(r"\d+", str(short or "")))


def _slim_rr(data) -> dict:
    """v1's list (or v2's ``{"history": [...]}``) -> ``{match id: {"change", "tier", "rr"}}``."""
    items = data.get("history") if isinstance(data, dict) else data
    found = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("match_id"):
            continue
        change = item.get("mmr_change_to_last_game", item.get("last_change"))
        tier = item.get(
            "currenttier", (item.get("tier") or {}).get("id") if isinstance(item.get("tier"), dict) else None
        )
        try:
            found[str(item["match_id"])] = {
                "change": int(change),
                "tier": int(tier or 0),
                "rr": int(item.get("ranking_in_tier", item.get("rr")) or 0),
                "elo": int(item.get("elo") or 0),
                "at": _moment(item.get("date_raw") or item.get("date")),
            }
        except (TypeError, ValueError):
            continue
    return found


def _moment(value) -> int:
    """Unix seconds from a number (v1's date_raw) or an ISO time (v2's date); 0 when it is neither."""
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _number(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slim_board(data) -> dict:
    """v3's leaderboard (or v2's, whose names differ) -> what the page shows."""
    if not isinstance(data, dict):
        raise HenrikError("down")
    players = []
    for item in data.get("players") or []:
        if not isinstance(item, dict):
            continue
        tier = item.get("tier", item.get("competitiveTier"))
        players.append(
            {
                "rank": _number(item.get("leaderboard_rank", item.get("leaderboardRank"))),
                "name": str(item.get("name", item.get("gameName")) or ""),
                "tag": str(item.get("tag", item.get("tagLine")) or ""),
                "puuid": str(item.get("puuid") or ""),
                "card": str(item.get("card", item.get("PlayerCardID")) or "").lower(),
                "tier": _number(tier.get("id") if isinstance(tier, dict) else tier),
                "rr": _number(item.get("rr", item.get("rankedRating"))),
                "wins": _number(item.get("wins", item.get("numberOfWins"))),
                "hidden": bool(item.get("is_anonymized", item.get("IsAnonymized"))),
            }
        )
    thresholds = []
    for item in data.get("thresholds") or []:
        if isinstance(item, dict):
            tier = item.get("tier")
            thresholds.append(
                {
                    "tier": _number(tier.get("id") if isinstance(tier, dict) else tier),
                    "rr": _number(item.get("threshold")),
                    "from": _number(item.get("start_index")) + 1,
                }
            )
    for tier, key in ((27, "radiant_threshold"), (26, "immortal_3_threshold"), (25, "immortal_2_threshold")):
        if not thresholds and data.get(key) is not None:  # v2
            thresholds.append({"tier": tier, "rr": _number(data[key]), "from": 0})
    results = data.get("results") if isinstance(data.get("results"), dict) else {}
    return {
        "players": players,
        "thresholds": sorted(thresholds, key=lambda item: -item["tier"]),
        "total": _number(results.get("total", data.get("total_players"))),
        "updated": str(data.get("updated_at") or data.get("last_update") or ""),
    }


def _https(value) -> str:
    """An https address, or "" (nothing else is linked to or shown)."""
    value = str(value or "")
    return value if value.startswith("https://") else ""


def _slim_news(data) -> list:
    articles = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        articles.append(
            {
                "title": str(item["title"])[:200],
                "at": str(item.get("date") or ""),
                "category": str(item.get("category") or "").lower(),
                "url": _https(item.get("external_link")) or _https(item.get("url")),
                "banner": _https(item.get("banner_url")),
            }
        )
    return sorted(articles, key=lambda item: item["at"], reverse=True)


def _slim_esports(data) -> list:
    games = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        match = item.get("match") if isinstance(item.get("match"), dict) else {}
        teams = [team for team in match.get("teams") or [] if isinstance(team, dict) and team.get("name")]
        if len(teams) != 2:
            continue
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        tournament = item.get("tournament") if isinstance(item.get("tournament"), dict) else {}
        kind = match.get("game_type") if isinstance(match.get("game_type"), dict) else {}
        games.append(
            {
                "at": str(item.get("date") or ""),
                "state": str(item.get("state") or ""),
                "league": {
                    "name": str(league.get("name") or ""),
                    "id": str(league.get("identifier") or league.get("name") or "").lower(),
                    "region": str(league.get("region") or ""),
                    "icon": _https(league.get("icon")),
                },
                "tournament": str(tournament.get("name") or ""),
                "best_of": int(kind.get("count") or 0) if str(kind.get("count") or "").isdigit() else 0,
                "vod": _https(item.get("vod")),
                "teams": [
                    {
                        "name": str(team.get("name"))[:60],
                        "code": str(team.get("code") or "")[:8],
                        "icon": _https(team.get("icon")),
                        "won": bool(team.get("has_won")),
                        "wins": int(team.get("game_wins") or 0) if str(team.get("game_wins") or 0).isdigit() else 0,
                    }
                    for team in teams
                ],
            }
        )
    return sorted(games, key=lambda item: item["at"])


def _slim_status(data) -> dict:
    """Each notice: ``{"kind": maintenance / incident, "severity", "status", "titles": {locale: words},
    "updates": [{"at", "words": {locale: words}}] (latest first), "at"}``."""

    def words(items) -> dict:
        return {
            str(item.get("locale") or "").replace("-", "_").lower(): str(item.get("content") or "")
            for item in items or []
            if isinstance(item, dict) and item.get("content")
        }

    found = {}
    for kind in ("maintenances", "incidents"):
        notices = []
        for item in (data or {}).get(kind) or [] if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            updates = [
                {"at": str(update.get("created_at") or ""), "words": words(update.get("translations"))}
                for update in item.get("updates") or []
                if isinstance(update, dict)
            ]
            notices.append(
                {
                    "kind": kind[:-1] if kind == "incidents" else "maintenance",
                    "severity": str(item.get("incident_severity") or ""),
                    "status": str(item.get("maintenance_status") or ""),
                    "titles": words(item.get("titles")),
                    "updates": sorted(updates, key=lambda update: update["at"], reverse=True)[:3],
                    "at": str(item.get("created_at") or ""),
                    "platforms": [str(name).lower() for name in item.get("platforms") or []],
                }
            )
        found[kind] = notices
    return found


def _slim_featured(data) -> list:
    """v2's store-featured: each bundle's uuid, price, time left and items (uuid, type, name, price)."""
    bundles = data if isinstance(data, list) else []
    found = []
    for bundle in bundles:
        if not isinstance(bundle, dict) or not bundle.get("bundle_uuid"):
            continue
        items = [
            {
                "uuid": str(item.get("uuid") or "").lower(),
                "type": str(item.get("type") or ""),
                "name": str(item.get("name") or ""),
                "image": str(item.get("image") or ""),
                "amount": _number(item.get("amount"), 1),
                "price": _number(item.get("discounted_price", item.get("base_price"))),
                "base": _number(item.get("base_price")),
            }
            for item in bundle.get("items") or []
            if isinstance(item, dict)
        ]
        found.append(
            {
                "uuid": str(bundle["bundle_uuid"]).lower(),
                "price": _number(bundle.get("bundle_price")),
                "until": time.time() + _number(bundle.get("seconds_remaining")),  # kept a while: when it ends
                "whole": bool(bundle.get("whole_sale_only")),
                "items": items,
            }
        )
    return found


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
        # in the order of the match's own list: the digest names players by their place in it
        "players": [_pick(player, PLAYER) for player in match.get("players") or [] if isinstance(player, dict)],
        "teams": [_pick(team, TEAM) for team in match.get("teams") or [] if isinstance(team, dict)],
        "digest": rounds.digest(match),
    }


def _slim_collected(matches) -> list:
    if not isinstance(matches, list):
        raise HenrikError("down")
    return [
        {
            "metadata": _pick(match["metadata"], METADATA),
            "players": [
                _pick(player, ("puuid", "tier", "team_id", "agent"))
                for player in match.get("players") or []
                if isinstance(player, dict)
            ],
            # which team won: the agents' win rates (activity.py)
            "teams": [_pick(team, ("team_id", "won")) for team in match.get("teams") or [] if isinstance(team, dict)],
        }
        for match in matches
        if isinstance(match, dict) and isinstance(match.get("metadata"), dict)
    ]


def _slim_matches(matches) -> list:
    if not isinstance(matches, list):
        raise HenrikError("down")
    return [_slim_match(match) for match in matches if isinstance(match, dict) and match.get("metadata")]


def _part(text: str) -> str:
    return quote(text, safe="")


def _region(region: str) -> str:
    region = str(region or "").lower()
    return region if region in REGIONS else "ap"
