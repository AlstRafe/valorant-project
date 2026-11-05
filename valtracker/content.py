"""The game's names and pictures (agents, maps, ranks, and the owner's collection: weapons and their skins,
buddies, cards, sprays, titles) in the visitor's language, from valorant-api.com: a free community API with no
key, which has every one of our languages (English as en-US).

Each list is saved in instance/content/ and asked for again once a day; when the site cannot be reached the
saved one is used, however old. Its pictures are on media.valorant-api.com, which the pages do not load
from (no outside addresses, CSP): media.py fetches them once and serves them from here.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from valtracker import i18n, net

BASE = "https://valorant-api.com/v1"
MEDIA = "https://media.valorant-api.com/"
KINDS = {
    "agents": "/agents?isPlayableCharacter=true&language={lang}",
    "maps": "/maps?language={lang}",
    "competitivetiers": "/competitivetiers?language={lang}",
    # the owner's collection (collection.py)
    "weapons": "/weapons?language={lang}",
    "buddies": "/buddies?language={lang}",
    "playercards": "/playercards?language={lang}",
    "sprays": "/sprays?language={lang}",
    "playertitles": "/playertitles?language={lang}",
    "flex": "/flex?language={lang}",
    "contenttiers": "/contenttiers?language={lang}",
    "currencies": "/currencies?language={lang}",
    # the game's version, which Riot's own servers want to be told (riot.py)
    "version": "/version",
}
#: A saved list is asked for again after this long.
FRESH_SECONDS = 24 * 3600
#: After a failed try, the saved list (or none) is used this long before the next try.
RETRY_SECONDS = 600


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
            return float(value["saved"]), list(value["data"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write(self, kind: str, code: str, now: float, data: list) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self._file(kind, code)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"saved": now, "data": data}, ensure_ascii=False), encoding="utf-8")
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


def _slim_weapon(weapon: dict) -> dict:
    """A weapon with what the collection shows: valorant-api.com's /weapons is megabytes (stats, videos, every
    level's asset path), which would fill the small VM's memory once per language."""
    kept = _keep(weapon, ("uuid", "displayName", "category", "displayIcon"))
    kept["cost"] = int(((weapon.get("shopData") or {}).get("cost")) or 0)
    kept["skins"] = [
        {
            **_keep(skin, ("uuid", "displayName", "contentTierUuid", "displayIcon")),
            "levels": _parts(skin, "levels", ("uuid", "displayIcon")),
            "chromas": _parts(skin, "chromas", ("uuid", "displayName", "displayIcon", "fullRender", "swatch")),
        }
        for skin in weapon.get("skins") or []
        if isinstance(skin, dict) and skin.get("uuid")
    ]
    return kept


#: What each of the collection's lists keeps of an item (the rest is dropped before it is saved).
SLIM = {
    "weapons": _slim_weapon,
    "buddies": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "playercards": lambda item: _keep(item, ("uuid", "displayName", "smallArt", "wideArt")),
    "sprays": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon", "fullTransparentIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "playertitles": lambda item: _keep(item, ("uuid", "displayName", "titleText")),
    "flex": lambda item: _keep(item, ("uuid", "displayName", "displayIcon")),
    "contenttiers": lambda item: _keep(item, ("uuid", "displayName", "devName", "rank", "displayIcon")),
    "currencies": lambda item: _keep(item, ("uuid", "displayName", "displayNameSingular", "displayIcon")),
}


def media_path(url) -> str | None:
    """A media.valorant-api.com picture's path under our /media/ (None for anything else)."""
    url = str(url or "")
    return url[len(MEDIA) :] if url.startswith(MEDIA) else None
