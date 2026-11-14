"""The game's names and pictures (agents, maps, ranks, and the owner's collection: weapons and their skins,
buddies, cards, sprays, titles) in the visitor's language, from valorant-api.com: a free community API with no
key, which has every one of our languages (English as en-US).

Each list is saved in instance/content/ and asked for again once a day; when the site cannot be reached the
saved one is used, however old. Its pictures are on media.valorant-api.com, which the pages do not load
from (no outside addresses, CSP): media.py fetches them once and serves them from here.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from valtracker import i18n, net

BASE = "https://valorant-api.com/v1"
MEDIA = "https://media.valorant-api.com/"
#: Where the skins' videos are (Riot's own CDN; valorant-api.com names them): the only outside addresses a page
#: may play from (security.CSP's media-src).
VIDEOS = ("https://valorant.dyn.riotcdn.net/", MEDIA)
#: The marks the game colours its own texts with (an ability's "<whitebold>…</>", "<debuffred>…</>"): they mean
#: nothing on a page.
MARKS = re.compile(r"</?[A-Za-z][A-Za-z0-9_]*>|</>")


#: Episode 5 (22 June 2022) put Ascendant between Diamond and Immortal: an act that began before it numbers
#: Immortal 21-23 and Radiant 24, three less than today.
ASCENDANT_SINCE = datetime(2022, 6, 21, tzinfo=UTC)


def todays_tier(tier: int, old: bool) -> int:
    """A rank's number as today's table has it; ``old``: it is from an act before Episode 5."""
    return tier + 3 if old and tier >= 21 else tier


def plain(text) -> str:
    """A text of the game (an agent's or an ability's description) without the game's own marks."""
    return MARKS.sub("", str(text or ""))


KINDS = {
    "agents": "/agents?isPlayableCharacter=true&language={lang}",
    "maps": "/maps?language={lang}",
    "competitivetiers": "/competitivetiers?language={lang}",
    # the weapons' names and kill-feed pictures for everyone's pages; "weapons" (with every skin) is the owner's
    "arsenal": "/weapons?language={lang}",
    # the owner's collection (collection.py)
    "weapons": "/weapons?language={lang}",
    "buddies": "/buddies?language={lang}",
    "playercards": "/playercards?language={lang}",
    "sprays": "/sprays?language={lang}",
    "playertitles": "/playertitles?language={lang}",
    "flex": "/flex?language={lang}",
    "contenttiers": "/contenttiers?language={lang}",
    "currencies": "/currencies?language={lang}",
    # the skin library (skinlib.py): the names of the skins' series (Prime, Reaver, ...)
    "themes": "/themes?language={lang}",
    # the owner's progress (progress.py): the battle passes' and agents' contracts, the XP of each level
    "contracts": "/contracts?language={lang}",
    # the store's bundles (shop.py): their names and pictures
    "bundles": "/bundles?language={lang}",
    # the episodes and acts, and when each ends
    "seasons": "/seasons?language={lang}",
    # the game's version, which Riot's own servers want to be told (riot.py)
    "version": "/version",
}
#: A saved list is asked for again after this long.
FRESH_SECONDS = 24 * 3600
#: After a failed try, the saved list (or none) is used this long before the next try.
RETRY_SECONDS = 600
#: A list whose kept keys (SLIM) changed: a saved one of an older shape is asked for again at once (and used
#: until the new one comes).
SHAPES = {"weapons": 2}


class Content:
    def __init__(self, folder: Path, fetch: net.Fetch = net.fetch, clock=time.time):
        self.folder = Path(folder) / "content"
        self.fetch, self.clock = fetch, clock
        self._memory: dict[tuple[str, str], tuple[float, list]] = {}
        self._tried: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def _list(self, kind: str, lang: str) -> list:
        code = i18n.CONTENT.get(lang, "en-US")
        key = (kind, code)
        now = self.clock()
        with self._lock:
            saved = self._memory.get(key)
            if saved is None:
                saved = self._read(kind, code)
                if saved is not None:
                    self._memory[key] = saved
            if saved is not None and now - saved[0] < FRESH_SECONDS:
                return saved[1]
            if now - self._tried.get(key, -RETRY_SECONDS) < RETRY_SECONDS:
                return saved[1] if saved else []
            self._tried[key] = now
        status, body = self.fetch(BASE + KINDS[kind].format(lang=code), {"Accept": "application/json"})
        try:
            data = json.loads(body.decode("utf-8")).get("data") if status == 200 else None
        except (ValueError, AttributeError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict):  # /version: one item
            data = [data]
        if not isinstance(data, list):
            return saved[1] if saved else []
        if kind in SLIM:
            data = [SLIM[kind](item) for item in data if isinstance(item, dict) and item.get("uuid")]
        with self._lock:
            self._memory[key] = (now, data)
            self._write(kind, code, now, data)
        return data

    def _file(self, kind: str, code: str) -> Path:
        return self.folder / f"{kind}-{code}.json"

    def _read(self, kind: str, code: str):
        try:
            value = json.loads(self._file(kind, code).read_text(encoding="utf-8"))
            fresh = value.get("shape", 1) == SHAPES.get(kind, 1)
            return (float(value["saved"]) if fresh else 0.0), list(value["data"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write(self, kind: str, code: str, now: float, data: list) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self._file(kind, code)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"saved": now, "shape": SHAPES.get(kind, 1), "data": data}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass  # kept in memory for this run

    # -- what the pages use --------------------------------------------------------------------------

    def agents(self, lang: str) -> list:
        """Every playable agent, by name in ``lang``."""
        agents = [agent for agent in self._list("agents", lang) if isinstance(agent, dict) and agent.get("uuid")]
        return sorted(agents, key=lambda agent: str(agent.get("displayName", "")))

    def agent(self, lang: str, uuid: str) -> dict | None:
        return {str(agent["uuid"]).lower(): agent for agent in self.agents(lang)}.get(str(uuid).lower())

    def agent_index(self, lang: str) -> dict:
        return {str(agent["uuid"]).lower(): agent for agent in self.agents(lang)}

    def map_index(self, lang: str) -> dict:
        """uuid and the map's address name (/Game/Maps/Ascent/Ascent -> "ascent") -> the map."""
        found = {}
        for item in self._list("maps", lang):
            if not isinstance(item, dict) or not item.get("uuid"):
                continue
            found[str(item["uuid"]).lower()] = item
            address = str(item.get("mapUrl") or "").rsplit("/", 1)[-1].lower()
            if address:
                found[address] = item
        return found

    def tiers(self, lang: str, old: bool = False) -> dict:
        """The rank table: tier number -> {"tierName", "smallIcon", "rankTriangleUpIcon", ...}. The current one,
        or with ``old`` the one before Ascendant (Episodes 1-4: up to 24, Radiant), when it is there."""
        tables = []
        for item in self._list("competitivetiers", lang):
            if isinstance(item, dict):
                table = {int(tier["tier"]): tier for tier in item.get("tiers") or [] if isinstance(tier, dict)}
                if table:
                    tables.append(table)
        if not tables:
            return {}
        if old:
            before = [table for table in tables if max(table) <= 24]
            if before:
                return before[-1]
        return tables[-1]

    def index(self, kind: str, lang: str) -> dict:
        """One of the collection's lists by uuid, the uuids of each item's levels (and a skin's chromas) too."""
        found = {}
        for item in self._list(kind, lang):
            if not isinstance(item, dict) or not item.get("uuid"):
                continue
            found[str(item["uuid"]).lower()] = item
            for part in (item.get("levels") or []) + (item.get("chromas") or []):
                if isinstance(part, dict) and part.get("uuid"):
                    found.setdefault(str(part["uuid"]).lower(), item)
        return found

    def skins(self, lang: str) -> dict:
        """Every skin, its levels and chromas by uuid -> (the weapon, the skin)."""
        found = {}
        for weapon in self.weapons(lang):
            for skin in weapon.get("skins") or []:
                for part in [skin] + (skin.get("levels") or []) + (skin.get("chromas") or []):
                    if isinstance(part, dict) and part.get("uuid"):
                        found.setdefault(str(part["uuid"]).lower(), (weapon, skin))
        return found

    def arsenal(self, lang: str) -> dict:
        """Every weapon by uuid: ``{"displayName", "category", "killStreamIcon", "displayIcon"}``."""
        return {
            str(item["uuid"]).lower(): item
            for item in self._list("arsenal", lang)
            if isinstance(item, dict) and item.get("uuid")
        }

    def act(self, lang: str, now: float | None = None) -> dict | None:
        """The act being played: ``{"name": "EPISODE 9 · ACT III", "starts", "ends": datetime (UTC), "uuid"}``, or
        None."""
        now = self.clock() if now is None else now
        return next(
            (item for item in self.acts(lang) if item["starts"].timestamp() <= now < item["ends"].timestamp()), None
        )

    def acts(self, lang: str) -> list[dict]:
        """Every act, the latest first: ``{"name": "EPISODE 9 · ACT III", "starts", "ends": datetime (UTC), "uuid"}``."""
        seasons = [item for item in self._list("seasons", lang) if isinstance(item, dict)]
        by_id = {str(item.get("uuid") or "").lower(): item for item in seasons}
        found = []
        for item in seasons:
            if "Act" not in str(item.get("type") or ""):
                continue
            try:
                start = datetime.fromisoformat(str(item.get("startTime")).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(item.get("endTime")).replace("Z", "+00:00"))
            except ValueError:
                continue
            parent = by_id.get(str(item.get("parentUuid") or "").lower()) or {}
            names = [str(parent.get("displayName") or ""), str(item.get("displayName") or "")]
            found.append(
                {
                    "name": " · ".join(name for name in names if name),
                    "starts": start,
                    "ends": end,
                    "uuid": str(item.get("uuid") or "").lower(),
                }
            )
        return sorted(found, key=lambda item: item["starts"], reverse=True)

    def old_acts(self, lang: str) -> frozenset[str]:
        """The acts before Episode 5 (uuids): their ranks are numbered without Ascendant (todays_tier)."""
        return frozenset(act["uuid"] for act in self.acts(lang) if act["starts"] < ASCENDANT_SINCE)

    def weapons(self, lang: str) -> list:
        return [weapon for weapon in self._list("weapons", lang) if isinstance(weapon, dict)]

    def client_version(self) -> dict:
        """``{"riotClientVersion": "release-09.10-shipping-...", "riotClientBuild": "91.0.2..."}`` or {}."""
        found = self._list("version", i18n.DEFAULT)
        return found[0] if found and isinstance(found[0], dict) else {}


def _keep(item: dict, keys) -> dict:
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "")}


def _parts(item: dict, kind: str, keys) -> list:
    return [_keep(part, keys) for part in item.get(kind) or [] if isinstance(part, dict) and part.get("uuid")]


def _video(parts: list) -> list:
    """A level's or chroma's video only from the known places (the pages may play only those, CSP)."""
    for part in parts:
        if not str(part.get("streamedVideo") or "").startswith(VIDEOS):
            part.pop("streamedVideo", None)
    return parts


def _slim_weapon(weapon: dict) -> dict:
    """A weapon with what the collection and the skin library show: valorant-api.com's /weapons is megabytes
    (stats, every level's asset path), which would fill the small VM's memory once per language."""
    kept = _keep(weapon, ("uuid", "displayName", "category", "displayIcon", "defaultSkinUuid"))
    kept["cost"] = int(((weapon.get("shopData") or {}).get("cost")) or 0)
    kept["skins"] = [
        {
            **_keep(skin, ("uuid", "displayName", "contentTierUuid", "themeUuid", "displayIcon")),
            "levels": _video(
                _parts(skin, "levels", ("uuid", "displayName", "levelItem", "displayIcon", "streamedVideo"))
            ),
            "chromas": _video(
                _parts(skin, "chromas", ("uuid", "displayName", "displayIcon", "fullRender", "swatch", "streamedVideo"))
            ),
        }
        for skin in weapon.get("skins") or []
        if isinstance(skin, dict) and skin.get("uuid")
    ]
    return kept


def _slim_contract(item: dict) -> dict:
    """A contract (a battle pass, an event's pass, an agent's): its kind, what it is for and each level's XP."""
    about = item.get("content") if isinstance(item.get("content"), dict) else {}
    levels, epilogue = [], 0
    for chapter in about.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        for level in chapter.get("levels") or []:
            if isinstance(level, dict):
                levels.append(int(level.get("xp") or 0))
                epilogue += bool(chapter.get("isEpilogue"))
    return {
        **_keep(item, ("uuid", "displayName", "displayIcon")),
        "type": str(about.get("relationType") or ""),
        "of": str(about.get("relationUuid") or "").lower(),
        "levels": levels,
        "epilogue": epilogue,
    }


#: What each of the collection's lists keeps of an item (the rest is dropped before it is saved).
SLIM = {
    "contracts": _slim_contract,
    "weapons": _slim_weapon,
    "buddies": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "arsenal": lambda item: _keep(item, ("uuid", "displayName", "category", "killStreamIcon", "displayIcon")),
    "playercards": lambda item: _keep(item, ("uuid", "displayName", "smallArt", "wideArt")),
    "sprays": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon", "fullTransparentIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "playertitles": lambda item: _keep(item, ("uuid", "displayName", "titleText")),
    "flex": lambda item: _keep(item, ("uuid", "displayName", "displayIcon")),
    "contenttiers": lambda item: _keep(item, ("uuid", "displayName", "devName", "rank", "displayIcon")),
    "currencies": lambda item: _keep(item, ("uuid", "displayName", "displayNameSingular", "displayIcon")),
    "bundles": lambda item: _keep(item, ("uuid", "displayName", "displayIcon", "displayIcon2")),
    "themes": lambda item: _keep(item, ("uuid", "displayName")),
    "seasons": lambda item: _keep(item, ("uuid", "displayName", "type", "startTime", "endTime", "parentUuid")),
}


def variant_name(name, skin_name: str) -> str:
    """A chroma's own part of its name: "Reaver Vandal Level 4\r\n(Variant 1 Red)" -> "Variant 1 Red"."""
    name = " ".join(str(name or "").split())
    inside = re.search(r"\(([^()]+)\)\s*$", name)
    if inside:
        return inside.group(1).strip()
    rest = name[len(skin_name) :].strip() if skin_name and name.startswith(skin_name) else name
    return rest or name


def media_path(url) -> str | None:
    """A media.valorant-api.com picture's path under our /media/ (None for anything else)."""
    url = str(url or "")
    return url[len(MEDIA) :] if url.startswith(MEDIA) else None
