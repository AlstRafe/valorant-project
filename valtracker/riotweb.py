"""Riot's own public pages, asked without a key or a sign-in: the news on playvalorant.com (the list its news page
reads), each region's server notices (the files the game's status page reads), and the esports matches to come
(valorantesports.com's schedule page, which carries them in its HTML for the browser to start from).

These are asked first; HenrikDev (henrik.py), which only passes them on, is the fallback when Riot does not
answer. HenrikDev's copy can fall behind: in 2026-11 its Japanese news still ended at patch 12.04 while Riot's
had 13.09. The answers have the shapes henrik.py's ``news`` and ``status`` give, so the pages read either.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from valtracker import net
from valtracker.cache import TTLCache
from valtracker.henrik import _https, _region, _slim_status

SITE = "https://playvalorant.com"
NEWS = (
    "https://content.publishing.riotgames.com/publishing-content/v2.0/public/channel/valorant_website/list/"
    "valorant_news_page_list"
)
STATUS = "https://valorant.secure.dyn.riotcdn.net/channels/public/x/status/{region}.json"
#: The languages of Riot's news site (ours, as world.NEWS_LANGS names them) -> the list's locale.
LOCALES = {"ja-jp": "ja_JP", "ko-kr": "ko_KR", "en-gb": "en_GB", "zh-tw": "zh_TW"}
#: How many of the latest articles are asked for (world.NEWS_SHOWN are shown).
NEWS_LIMIT = 60
ESPORTS = "https://valorantesports.com/{locale}/schedule"
#: Our languages -> the esports site's (its leagues and stages are named in them); en-US when one does not answer.
ESPORTS_LOCALES = {"ja": "ja-JP", "ko": "ko-KR", "en-GB": "en-GB", "zh-TW": "zh-TW"}
#: Where a match of the schedule starts in the page: the answer of the page's own query, as JavaScript.
EVENT = re.compile(r'\{"__typename":"EventMatch"')
#: How long an answer is kept (seconds), and how long a failure is (not asked again meanwhile: each try can
#: take net.TIMEOUT, and the page then asks HenrikDev).
KEEP = {"news": 1800, "status": 300, "esports": 900}
FAILED_KEEP = 120


class RiotWebError(Exception):
    def __init__(self, detail: str = ""):
        super().__init__(detail)
        self.detail = detail


class RiotWeb:
    def __init__(self, fetch: net.Fetch = net.fetch):
        self.fetch = fetch
        self.cache = TTLCache(64)

    def news(self, country: str) -> list:
        """The latest articles in a language (``ja-jp``...): ``[{"title", "at", "category", "url", "banner"}]``,
        the latest first. The latest patch notes are always among them, even when older than the rest."""
        locale = LOCALES.get(country)
        if not locale:
            raise RiotWebError(f"no news in {country}")
        listed = self._get(("news", locale), _news_url(locale, NEWS_LIMIT), KEEP["news"], _slim_news)
        if not any(item["category"] == "patch_notes" for item in listed):
            try:
                patch = self._get(("patch", locale), _news_url(locale, 1, "patch_notes"), KEEP["news"], _slim_news)
            except RiotWebError:
                patch = []
            listed = sorted(listed + patch[:1], key=lambda item: item["at"], reverse=True)
        return listed

    def status(self, region: str) -> dict:
        """A region's notices: ``{"maintenances": [...], "incidents": [...]}`` (henrik._slim_status)."""
        region = _region(region)
        return self._get(("status", region), STATUS.format(region=region), KEEP["status"], _slim_status)

    def esports(self, lang: str) -> list:
        """The esports matches to come (about the next 40, of every league), in the shape of henrik._slim_esports,
        by time. Riot's site has no results without its key: those (and the matches being played) are HenrikDev's."""
        locale = ESPORTS_LOCALES.get(lang, "en-US")
        try:
            return self._get(("esports", locale), ESPORTS.format(locale=locale), KEEP["esports"], _slim_schedule)
        except RiotWebError:
            if locale == "en-US":
                raise
            return self._get(("esports", "en-US"), ESPORTS.format(locale="en-US"), KEEP["esports"], _slim_schedule)

    def _get(self, key, url: str, seconds: float, shape):
        """``shape`` turns the answer (a JSON object, or for a page its text) into what is kept."""
        kept = self.cache.get(key)
        if kept is not None:
            return kept
        failed = self.cache.get(("failed", key))
        if failed:
            raise RiotWebError(failed)
        page = shape is _slim_schedule
        status, body = self.fetch(url, {"Accept": "text/html" if page else "application/json"})
        try:
            if status != 200:
                raise RiotWebError(f"HTTP {status}" if status else f"no answer within {net.TIMEOUT:g} s")
            try:
                data = body.decode("utf-8") if page else json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise RiotWebError(f"an answer that is not JSON ({len(body):,} bytes)") from None
            if not isinstance(data, str if page else dict):
                raise RiotWebError("an answer of another shape")
            found = shape(data)
            if isinstance(found, list) and not found:
                raise RiotWebError("an empty list")
        except RiotWebError as error:
            self.cache.put(("failed", key), error.detail or "failed", FAILED_KEEP)
            raise
        self.cache.put(key, found, seconds)
        return found


def _news_url(locale: str, limit: int, tag: str = "") -> str:
    asked = {"locale": locale, "from": 0, "limit": limit}
    if tag:
        asked["tags"] = tag
    return f"{NEWS}?{urlencode(asked)}"


def _slim_news(data: dict) -> list:
    articles = []
    for item in data.get("data") or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        url = str(payload.get("url") or "")
        if url.startswith("/") and not url.startswith("//"):
            url = SITE + url
        tags = {str(tag.get("machineName") or "") for tag in item.get("tags") or [] if isinstance(tag, dict)}
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        if media.get("type") != "image":
            media = item.get("imageMedia") if isinstance(item.get("imageMedia"), dict) else {}
        articles.append(
            {
                "title": str(item["title"])[:200],
                "at": str(item.get("publishedAt") or ""),
                "category": "patch_notes" if "patch_notes" in tags else str(category.get("machineName") or "").lower(),
                "url": _https(url),
                "banner": _https(media.get("url")),
            }
        )
    return sorted(articles, key=lambda item: item["at"], reverse=True)


def _picture(value) -> str:
    """The esports site's pictures, some of them given as http:// (the same address answers https)."""
    value = str(value or "")
    if value.startswith("http://static.lolesports.com/"):
        value = "https://" + value.removeprefix("http://")
    return _https(value)


def _number(value) -> int:
    return int(value) if isinstance(value, int) or str(value or "").isdigit() else 0


def _slim_schedule(page: str) -> list:
    """The matches in the schedule page: each ``{"__typename":"EventMatch",...}`` object in it (JSON, except that
    JavaScript's ``undefined`` can stand for a value), once each."""
    page = page.replace(":undefined", ":null")
    decoder, found = json.JSONDecoder(), {}
    for spot in EVENT.finditer(page):
        try:
            item, _ = decoder.raw_decode(page, spot.start())
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("id"):
            found[str(item["id"])] = item
    games = []
    for item in found.values():
        teams = [team for team in item.get("matchTeams") or [] if isinstance(team, dict) and team.get("name")]
        if item.get("type") != "match" or len(teams) != 2:
            continue
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        tournament = item.get("tournament") if isinstance(item.get("tournament"), dict) else {}
        match = item.get("match") if isinstance(item.get("match"), dict) else {}
        strategy = match.get("strategy") if isinstance(match.get("strategy"), dict) else {}
        stage = " · ".join(str(part)[:60] for part in (tournament.get("name"), item.get("blockName")) if part)
        each = []
        for team in teams:
            result = team.get("result") if isinstance(team.get("result"), dict) else {}
            each.append(
                {
                    "name": str(team["name"])[:60],
                    "code": str(team.get("code") or "")[:8],
                    "icon": _picture(team.get("image")),
                    "won": result.get("outcome") == "win",
                    "wins": _number(result.get("gameWins")),
                }
            )
        games.append(
            {
                "at": str(item.get("startTime") or ""),
                "state": str(item.get("state") or ""),
                "league": {
                    "name": str(league.get("name") or "")[:60],
                    "id": str(league.get("slug") or league.get("name") or "").lower()[:40],
                    "region": "",
                    "icon": _picture(league.get("image")),
                },
                "tournament": stage,
                "best_of": _number(strategy.get("count")) if strategy.get("type") == "bestOf" else 0,
                "vod": "",
                "teams": each,
            }
        )
    return sorted(games, key=lambda item: item["at"])
