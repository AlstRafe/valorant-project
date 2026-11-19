"""ゲームの名前と絵（エージェント、マップ、ランク、持ち主のコレクション: 武器とスキン、ガンバディ、カード、スプレー、
称号）を訪問者の言語で。valorant-api.com から: キー不要の無料のコミュニティ API で、
こちらの言語が全部ある（英語は en-US）。

各一覧は instance/content/ に保存し、1 日 1 回取り直す。サイトに届かないときは、どんなに古くても保存したものを使う。
絵は media.valorant-api.com にあり、ページはそこから読まない（外部アドレス無し、 CSP）
: media.py が 1 回取ってここから配る。"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from valtracker import i18n, net
from valtracker.util import as_dict, as_list, to_int

BASE = "https://valorant-api.com/v1"
MEDIA = "https://media.valorant-api.com/"
#: スキン動画の場所（Riot 自身の CDN。名前は valorant-api.com が出す）
#: : ページが再生してよい唯一の外部アドレス（security.CSP の media-src）。
VIDEOS = ("https://valorant.dyn.riotcdn.net/", MEDIA)
#: ゲームが自分の文に色を付ける印（アビリティの "<whitebold>…</>"、"<debuffred>…</>"）: ページでは意味が無い。
MARKS = re.compile(r"</?[A-Za-z][A-Za-z0-9_]*>|</>")


#: エピソード 5（2022 年 6 月 22 日）でダイヤモンドとイモータルの間にアセンダントが入った:
#: それより前に始まったアクトはイモータルが 21〜23、レディアントが 24 で、今より 3 少ない。
ASCENDANT_SINCE = datetime(2022, 6, 21, tzinfo=UTC)


def todays_tier(tier: int, old: bool) -> int:
    """今の表でのランクの番号。``old``: エピソード 5 より前のアクトのもの。"""
    return tier + 3 if old and tier >= 21 else tier


def plain(text) -> str:
    """ゲームの文（エージェントやアビリティの説明）からゲームの印を除いたもの。"""
    return MARKS.sub("", str(text or ""))


KINDS = {
    "agents": "/agents?isPlayableCharacter=true&language={lang}",
    "maps": "/maps?language={lang}",
    "competitivetiers": "/competitivetiers?language={lang}",
    # 全員のページ用の武器の名前とキルフィードの絵。"weapons"（全スキン入り）は持ち主用
    "arsenal": "/weapons?language={lang}",
    # 持ち主のコレクション（collection.py）
    "weapons": "/weapons?language={lang}",
    "buddies": "/buddies?language={lang}",
    "playercards": "/playercards?language={lang}",
    "sprays": "/sprays?language={lang}",
    "playertitles": "/playertitles?language={lang}",
    "flex": "/flex?language={lang}",
    "contenttiers": "/contenttiers?language={lang}",
    "currencies": "/currencies?language={lang}",
    # スキン図鑑（skinlib.py）: スキンのシリーズの名前（Prime、Reaver など）
    "themes": "/themes?language={lang}",
    # 持ち主の進み具合（progress.py）: バトルパスとエージェントのコントラクト、各レベルの XP
    "contracts": "/contracts?language={lang}",
    # ストアのバンドル（shop.py）: 名前と絵
    "bundles": "/bundles?language={lang}",
    # エピソードとアクト、それぞれの終わり
    "seasons": "/seasons?language={lang}",
    # Riot 自身のサーバーに伝える必要があるゲームのバージョン（riot.py）
    "version": "/version",
}
#: 保存した一覧はこの時間が過ぎたら取り直す。
FRESH_SECONDS = 24 * 3600
#: 取り直しに失敗したら、次に試すまでこの時間は保存した一覧（か無し）を使う。
RETRY_SECONDS = 600
#: 残すキー（SLIM）が変わった一覧: 古い形で保存したものはすぐに取り直す（新しいものが来るまでは古いものを使う）。
SHAPES = {"weapons": 2, "arsenal": 2}
#: 同じ答え（同じアドレス）から作る一覧: 1 回のダウンロード（/weapons は数 MB）で両方を保存する。
TWINS = {"arsenal": ("weapons",), "weapons": ("arsenal",)}


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
        if isinstance(data, dict):  # /version: 1 項目
            data = [data]
        if not isinstance(data, list):
            return saved[1] if saved else []
        made = {name: _slimmed(name, data) for name in (kind, *TWINS.get(kind, ()))}
        with self._lock:
            for name, listed in made.items():
                self._memory[(name, code)] = (now, listed)
                self._tried[(name, code)] = now
                self._write(name, code, now, listed)
        return made[kind]

    def _file(self, kind: str, code: str) -> Path:
        return self.folder / f"{kind}-{code}.json"

    def _read(self, kind: str, code: str):
        try:
            value = json.loads(self._file(kind, code).read_text(encoding="utf-8"))
            fresh = value.get("shape", 1) == SHAPES.get(kind, 1)
            return (float(value["saved"]) if fresh else 0.0), list(value["data"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):  # AttributeError: 辞書でない JSON
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
            pass  # この実行の間はメモリに持つ

    # -- ページが使うもの ----------------------------------------------------------------------------

    def agents(self, lang: str) -> list:
        """プレイできる全エージェント（``lang`` での名前で）。"""
        agents = [agent for agent in self._list("agents", lang) if isinstance(agent, dict) and agent.get("uuid")]
        return sorted(agents, key=lambda agent: str(agent.get("displayName", "")))

    def agent(self, lang: str, uuid: str) -> dict | None:
        return self.agent_index(lang).get(str(uuid).lower())

    def agent_index(self, lang: str) -> dict:
        return {str(agent["uuid"]).lower(): agent for agent in self.agents(lang)}

    def map_index(self, lang: str) -> dict:
        """uuid とマップのアドレス名（/Game/Maps/Ascent/Ascent -> "ascent"）-> マップ。"""
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
        """ランクの表: tier の番号 -> {"tierName", "smallIcon", "rankTriangleUpIcon", ...}。今のもの、
        ``old`` ならアセンダント以前のもの（エピソード 1〜4: レディアントの 24 まで）があればそれ。"""
        tables = []
        for item in self._list("competitivetiers", lang):
            if isinstance(item, dict):
                # 番号の無い・読めないランクは飛ばす（1 つのせいでランクの絵が全部消えないように）
                table = {
                    to_int(tier.get("tier"), -1): tier
                    for tier in as_list(item.get("tiers"))
                    if isinstance(tier, dict) and to_int(tier.get("tier"), -1) >= 0
                }
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
        """コレクションの一覧の 1 つを uuid で。各項目のレベル（とスキンのクロマ）の uuid でも引ける。"""
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
        """全スキン、そのレベルとクロマの uuid -> （武器, スキン）。"""
        found = {}
        for weapon in self.weapons(lang):
            for skin in weapon.get("skins") or []:
                for part in [skin] + (skin.get("levels") or []) + (skin.get("chromas") or []):
                    if isinstance(part, dict) and part.get("uuid"):
                        found.setdefault(str(part["uuid"]).lower(), (weapon, skin))
        return found

    def arsenal(self, lang: str) -> dict:
        """全武器を uuid で: ``{"displayName", "killStreamIcon", "displayIcon", "category", "cost", "stats"}``
        （``stats`` は _slim_arsenal。近接武器には無い）。"""
        return {
            str(item["uuid"]).lower(): item
            for item in self._list("arsenal", lang)
            if isinstance(item, dict) and item.get("uuid")
        }

    def act(self, lang: str, now: float | None = None) -> dict | None:
        """今のアクト: ``{"name": "EPISODE 9 · ACT III", "starts", "ends": datetime（UTC）, "uuid"}``、無ければ None。"""
        now = self.clock() if now is None else now
        return next(
            (item for item in self.acts(lang) if item["starts"].timestamp() <= now < item["ends"].timestamp()), None
        )

    def acts(self, lang: str) -> list[dict]:
        """全アクト（新しい順）: ``{"name": "EPISODE 9 · ACT III", "starts", "ends": datetime（UTC）, "uuid"}``。"""
        seasons = [item for item in self._list("seasons", lang) if isinstance(item, dict)]
        by_id = {str(item.get("uuid") or "").lower(): item for item in seasons}
        found = []
        for item in seasons:
            if "Act" not in str(item.get("type") or ""):
                continue
            try:
                start = datetime.fromisoformat(str(item.get("startTime")))
                end = datetime.fromisoformat(str(item.get("endTime")))
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
        """エピソード 5 より前のアクト（uuid）: ランクの番号にアセンダントが無い（todays_tier）。"""
        return frozenset(act["uuid"] for act in self.acts(lang) if act["starts"] < ASCENDANT_SINCE)

    def weapons(self, lang: str) -> list:
        return [weapon for weapon in self._list("weapons", lang) if isinstance(weapon, dict)]

    def client_version(self) -> dict:
        """``{"riotClientVersion": "release-09.10-shipping-...", "riotClientBuild": "91.0.2..."}``、無ければ {}。"""
        found = self._list("version", i18n.DEFAULT)
        return found[0] if found and isinstance(found[0], dict) else {}


def _slimmed(kind: str, data: list) -> list:
    """保存する形の一覧: SLIM のある種類は各項目の使う部分だけ。"""
    if kind not in SLIM:
        return data
    return [SLIM[kind](item) for item in data if isinstance(item, dict) and item.get("uuid")]


def _keep(item: dict, keys) -> dict:
    """残す項目はどれも文字列（名前、uuid、絵のアドレス）: ほかの型のもの（壊れた答え）は無いことにする。"""
    return {key: item[key] for key in keys if isinstance(item.get(key), str) and item[key]}


def _parts(item: dict, kind: str, keys) -> list:
    return [_keep(part, keys) for part in as_list(item.get(kind)) if isinstance(part, dict) and part.get("uuid")]


def _video(parts: list) -> list:
    """レベルやクロマの動画。わかっている場所のものだけ（ページはそれしか再生できない、CSP）。"""
    for part in parts:
        if not str(part.get("streamedVideo") or "").startswith(VIDEOS):
            part.pop("streamedVideo", None)
    return parts


def _slim_weapon(weapon: dict) -> dict:
    """コレクションとスキン図鑑が出す部分だけの武器: valorant-api.com の /weapons は数 MB（性能、
    全レベルのアセットパス）あり、言語ごとに持つと小さな VM のメモリが埋まる。"""
    kept = _keep(weapon, ("uuid", "displayName", "category", "displayIcon", "defaultSkinUuid"))
    shop = as_dict(weapon.get("shopData"))
    kept["cost"] = to_int(shop.get("cost"))
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
        for skin in as_list(weapon.get("skins"))
        if isinstance(skin, dict) and skin.get("uuid")
    ]
    return kept


def _numbers(item, keys) -> dict:
    """数のキーだけ（壊れた値・nan は無いことにする）。"""
    found = {}
    for key in keys:
        value = as_dict(item).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            found[key] = value
    return found


def _enum(value) -> str:
    """ゲームの列挙の名前の最後の部分: "EWallPenetrationDisplayType::Medium" -> "Medium"。"""
    return str(value or "").rsplit("::", 1)[-1] if isinstance(value, str) else ""


def _slim_arsenal(weapon: dict) -> dict:
    """全員のページ用の武器: 名前とキルフィードの絵、武器のページ（weapons.py）の性能と値段。
    ``stats``: 連射・マガジン・リロード・持ち替え・初弾の精度・壁抜き・ペレット数・ADS（倍率と連射）と、
    距離ごとのダメージ ``ranges``（[始まり m, 終わり m, 頭, 体, 脚]）。性能の無いもの（近接武器）は ``stats`` 無し。"""
    kept = _keep(weapon, ("uuid", "displayName", "killStreamIcon", "displayIcon", "category"))
    kept["category"] = _enum(kept.get("category"))
    shop = as_dict(weapon.get("shopData"))
    kept["cost"] = to_int(shop.get("cost"))
    numbers = as_dict(weapon.get("weaponStats"))
    if not numbers:
        return kept
    stats = _numbers(
        numbers,
        (
            "fireRate",
            "magazineSize",
            "reloadTimeSeconds",
            "equipTimeSeconds",
            "firstBulletAccuracy",
            "shotgunPelletCount",
        ),
    )
    stats["wall"] = _enum(numbers.get("wallPenetration"))
    stats["feature"] = _enum(numbers.get("feature"))
    stats["mode"] = _enum(numbers.get("fireMode"))
    stats["alt"] = _enum(numbers.get("altFireType"))
    stats["ads"] = _numbers(
        numbers.get("adsStats"), ("zoomMultiplier", "fireRate", "burstCount", "firstBulletAccuracy")
    )
    ranges = []
    for part in as_list(numbers.get("damageRanges")):
        found = _numbers(part, ("rangeStartMeters", "rangeEndMeters", "headDamage", "bodyDamage", "legDamage"))
        if len(found) == 5 and found["bodyDamage"] > 0:
            ranges.append(
                [
                    found["rangeStartMeters"],
                    found["rangeEndMeters"],
                    found["headDamage"],
                    found["bodyDamage"],
                    found["legDamage"],
                ]
            )
    stats["ranges"] = sorted(ranges)
    kept["stats"] = stats
    return kept


def _slim_contract(item: dict) -> dict:
    """コントラクト（バトルパス、イベントのパス、エージェントのもの）: 種類、何のものか、各レベルの XP。"""
    about = as_dict(item.get("content"))
    levels, epilogue = [], 0
    for chapter in as_list(about.get("chapters")):
        if not isinstance(chapter, dict):
            continue
        for level in as_list(chapter.get("levels")):
            if isinstance(level, dict):
                levels.append(max(0, to_int(level.get("xp"))))
                epilogue += bool(chapter.get("isEpilogue"))
    return {
        **_keep(item, ("uuid", "displayName", "displayIcon")),
        "type": str(about.get("relationType") or ""),
        "of": str(about.get("relationUuid") or "").lower(),
        "levels": levels,
        "epilogue": epilogue,
    }


#: コレクションの各一覧で項目から残すもの（残りは保存前に捨てる）。
SLIM = {
    "contracts": _slim_contract,
    "weapons": _slim_weapon,
    "buddies": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "arsenal": _slim_arsenal,
    "playercards": lambda item: _keep(item, ("uuid", "displayName", "smallArt", "wideArt")),
    "sprays": lambda item: {
        **_keep(item, ("uuid", "displayName", "displayIcon", "fullTransparentIcon")),
        "levels": _parts(item, "levels", ("uuid",)),
    },
    "playertitles": lambda item: _keep(item, ("uuid", "displayName", "titleText")),
    "flex": lambda item: _keep(item, ("uuid", "displayName", "displayIcon")),
    "contenttiers": lambda item: _keep(item, ("uuid", "displayName", "devName", "displayIcon")),
    "currencies": lambda item: _keep(item, ("uuid", "displayName", "displayIcon")),
    "bundles": lambda item: _keep(item, ("uuid", "displayName", "displayIcon", "displayIcon2")),
    "themes": lambda item: _keep(item, ("uuid", "displayName")),
    "seasons": lambda item: _keep(item, ("uuid", "displayName", "type", "startTime", "endTime", "parentUuid")),
}


def variant_name(name, skin_name: str) -> str:
    """クロマ自身の名前の部分: "Reaver Vandal Level 4 (Variant 1 Red)" -> "Variant 1 Red"。"""
    name = " ".join(str(name or "").split())
    inside = re.search(r"\(([^()]+)\)\s*$", name)
    if inside:
        return inside.group(1).strip()
    rest = name[len(skin_name) :].strip() if skin_name and name.startswith(skin_name) else name
    return rest or name


def media_path(url) -> str | None:
    """media.valorant-api.com の絵の、こちらの /media/ の下でのパス（ほかのものは None）。"""
    url = str(url or "")
    return url[len(MEDIA) :] if url.startswith(MEDIA) else None
