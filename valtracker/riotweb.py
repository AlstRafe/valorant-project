"""Riot 自身の公開ページ（キーもサインインも不要）: playvalorant.com のニュース（ニュースページが読む一覧）、
各リージョンのサーバーのお知らせ（ゲームのステータスページが読むファイル）、
eスポーツの予定（valorantesports.com の予定ページ。ブラウザー用に HTML に入っている）。

これを先に聞き、Riot が答えないときだけ、それを中継しているだけの HenrikDev（henrik.py）に聞く。
HenrikDev の写しは遅れることがある: 2026-11 には、
Riot は 13.09 まであるのに HenrikDev の日本語ニュースは 12.04 で止まっていた。
答えは henrik.py の ``news`` と ``status`` と同じ形なので、ページはどちらでも読める。"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from valtracker import net
from valtracker.cache import TTLCache
from valtracker.henrik import https_only, known_region, slim_status
from valtracker.util import as_list, to_int

SITE = "https://playvalorant.com"
NEWS = (
    "https://content.publishing.riotgames.com/publishing-content/v2.0/public/channel/valorant_website/list/"
    "valorant_news_page_list"
)
STATUS = "https://valorant.secure.dyn.riotcdn.net/channels/public/x/status/{region}.json"
#: Riot ニュースサイトの言語（world.NEWS_LANGS でのこちらの言語）-> 一覧のロケール。
LOCALES = {"ja-jp": "ja_JP", "ko-kr": "ko_KR", "en-gb": "en_GB", "zh-tw": "zh_TW"}
#: 頼む最新記事の数（出すのは world.NEWS_SHOWN）。
NEWS_LIMIT = 60
ESPORTS = "https://valorantesports.com/{locale}/schedule"
#: こちらの言語 -> eスポーツサイトの言語（リーグとステージの名前がその言語になる）。答えなければ en-US。
ESPORTS_LOCALES = {"ja": "ja-JP", "ko": "ko-KR", "en-GB": "en-GB", "zh-TW": "zh-TW"}
#: ページ内で予定の試合が始まる場所: ページ自身のクエリの答え（JavaScript として）。
EVENT = re.compile(r'\{"__typename":"EventMatch"')
#: 答えを持つ秒数と、失敗を覚えておく秒数（その間は聞き直さない。 1 回で net.TIMEOUT かかることがあり、
#: そのときページは HenrikDev に聞く）。
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
        """その言語（``ja-jp`` など）の最新記事: ``[{"title", "at", "category", "url", "banner"}]``、新しい順。
        最新のパッチノートはほかより古くてもいつも入れる。"""
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
        """リージョンのお知らせ: ``{"maintenances": [...], "incidents": [...]}``（henrik.slim_status）。"""
        region = known_region(region)
        return self._get(("status", region), STATUS.format(region=region), KEEP["status"], slim_status)

    def esports(self, lang: str) -> list:
        """eスポーツの予定（全リーグの次の 40 試合ほど）を henrik._slim_esports の形で、時刻順に。
        Riot のサイトはキー無しでは結果を出さないので、結果（と進行中の試合）は HenrikDev。"""
        locale = ESPORTS_LOCALES.get(lang, "en-US")
        try:
            return self._get(("esports", locale), ESPORTS.format(locale=locale), KEEP["esports"], _slim_schedule)
        except RiotWebError:
            if locale == "en-US":
                raise
            return self._get(("esports", "en-US"), ESPORTS.format(locale="en-US"), KEEP["esports"], _slim_schedule)

    def _get(self, key, url: str, seconds: float, shape):
        """``shape`` で答え（JSON オブジェクト、ページならその本文）を残す形にする。"""
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
    for item in as_list(data.get("data")):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        url = str(payload.get("url") or "")
        if url.startswith("/") and not url.startswith("//"):
            url = SITE + url
        tags = {str(tag.get("machineName") or "") for tag in as_list(item.get("tags")) if isinstance(tag, dict)}
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        if media.get("type") != "image":
            media = item.get("imageMedia") if isinstance(item.get("imageMedia"), dict) else {}
        articles.append(
            {
                "title": str(item["title"])[:200],
                "at": str(item.get("publishedAt") or ""),
                "category": "patch_notes" if "patch_notes" in tags else str(category.get("machineName") or "").lower(),
                "url": https_only(url),
                "banner": https_only(media.get("url")),
            }
        )
    return sorted(articles, key=lambda item: item["at"], reverse=True)


def _picture(value) -> str:
    """eスポーツサイトの絵。http:// で来るものもある（同じアドレスが https でも答える）。"""
    value = str(value or "")
    if value.startswith("http://static.lolesports.com/"):
        value = "https://" + value.removeprefix("http://")
    return https_only(value)


def _slim_schedule(page: str) -> list:
    """予定ページの試合: ページ内の ``{"__typename":"EventMatch",...}`` オブジェクトを 1 つずつ（JSON。
    ただし JavaScript の ``undefined`` が値になっていることがある）。"""
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
        teams = [team for team in as_list(item.get("matchTeams")) if isinstance(team, dict) and team.get("name")]
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
                    "wins": to_int(result.get("gameWins")),
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
                "best_of": to_int(strategy.get("count")) if strategy.get("type") == "bestOf" else 0,
                "vod": "",
                "teams": each,
            }
        )
    return sorted(games, key=lambda item: item["at"])
