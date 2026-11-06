"""The owner's pages beyond the store and the collection (Japanese only, refused to visitors):

- /progress: the battle pass, the events' passes, the agents' contracts and the missions (progress.py);
- /live: the game right now (live.py), asked again every few seconds: the menus (the party, start or stop a
  queue: POST /live/act), agent select (pick and lock an agent) or the match;
- POST /collection/equip: wear a skin (one of its variants), a buddy on a weapon, a card, a title, or a spray or a
  flex on the wheel, as the game's collection does;
- POST /collection/sets: save the whole loadout as a set (instance/loadout_sets.json), wear one again, or delete it.
"""

from __future__ import annotations

import re
import secrets
import time
from datetime import datetime

from flask import Flask, current_app, g, jsonify, redirect, render_template, request, session, url_for

from valtracker import collection as owned_items
from valtracker import live as live_page
from valtracker import progress as progress_page
from valtracker import security
from valtracker.activity import LINE_QUEUES
from valtracker.collection import CHARM, STARTERS
from valtracker.henrik import REGIONS
from valtracker.riot import ITEM_TYPES, RiotError
from valtracker.stats import JST
from valtracker.tools import _when
from valtracker.views import QUEUES, RIOT_FAILURES, _t

#: What can be worn from the collection page -> where the loadout keeps it.
#: How long /live links to the match the owner played last, once it is over.
LAST_MATCH_KEPT = 3 * 3600
_MATCH_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
IDENTITY = {"cards": "PlayerCardID", "titles": "PlayerTitleID"}
#: What /live says after one of its buttons: (words, whether it worked).
LIVE_SAID = {
    "ok-start": ("キューを開始しました。", True),
    "ok-leave": ("キューをやめました。", True),
    "ok-select": ("選びました。まだ確定していません。", True),
    "ok-lock": ("確定しました。", True),
    "notnow": (
        "いまはできません（ゲームが開いていない、パーティーにいない、またはエージェント選択中ではない）。",
        False,
    ),
    "unknown": ("そのボタンは使えません。", False),
}
#: Why wearing something did not work (shown on the collection page).
EQUIP_FAILURES = {
    "unknown": "持っていないものは装備できません。",
    "noweapon": "その武器がロードアウトにありませんでした。",
    "notsaved": "Riot は受け付けましたが、装備は変わっていませんでした。",
    "melee": "近接武器にはガンバディーを付けられません。",
    "full": f"セットは {owned_items.MAX_SETS} 個までです。使わないものを消してから保存してください。",
    "noset": "そのセットはもうありません。",
}


def _id(value) -> str:
    return str(value or "").lower()


def _queue_name(key: str) -> str:
    key = key.lower()
    return _t(QUEUES[key]) if key in QUEUES else key


def _agents_on_the_map(map_id: str, queue: str) -> dict:
    """How each agent does on a map in the matches this site has seen, in the queue (competitive for others)."""
    queue = queue if queue in LINE_QUEUES else "competitive"
    return current_app.config["VAL_ACTIVITY"].agents(queue, "", map_id)


def _last_match(found: dict) -> str:
    """The match the owner played last, kept in their session while it is played (by /live or the header's tab
    asking /live/state), for the link to it on /live once it is over: its id, or "" (playing, or none lately)."""
    match = found.get("match") if isinstance(found.get("match"), dict) else {}
    playing = str(match.get("MatchID") or match.get("ID") or "").lower()
    if found.get("phase") == "ingame" and _MATCH_ID.match(playing):
        session["live_match"] = [playing, time.time()]
        return ""
    kept = session.get("live_match")
    if found.get("phase") in ("ingame", "pregame") or not isinstance(kept, list) or len(kept) != 2:
        return ""
    match_id, when = str(kept[0]), float(kept[1] or 0)
    return match_id if _MATCH_ID.match(match_id) and time.time() - when < LAST_MATCH_KEPT else ""


def _match_link(match_id: str) -> str:
    """The owner's last match on this site, with their line marked (?me=)."""
    if not match_id:
        return ""
    status = current_app.config["VAL_RIOT"].status()
    region = status["shard"] if status["shard"] in REGIONS else "ap"
    me = f"{status['name']}#{status['tag']}" if status["name"] and status["tag"] else None
    return url_for("match", region=region, match_id=match_id, me=me)


def _problem(error: RiotError) -> dict:
    return {
        "problem": RIOT_FAILURES.get(error.kind, RIOT_FAILURES["down"]),
        "detail": error.detail,
        "settings_link": error.kind not in ("busy", "down"),
    }


def _wear_skin(found: dict, skin_uuid: str, chroma_uuid: str):
    """The change to the loadout that wears this skin (its best owned level, the chosen variant), or the reason
    it cannot be worn."""
    content = current_app.config["VAL_CONTENT"]
    known = content.skins(g.lang).get(skin_uuid)
    if not known or _id(known[1].get("uuid")) != skin_uuid:
        return None, "unknown"
    weapon, skin = known
    owned = found.get("owned") or {}
    levels = [_id(level.get("uuid")) for level in skin.get("levels") or []]
    have = [level for level in levels if level in (owned.get("skins") or {})]
    if not have:
        return None, "unknown"
    chromas = [_id(part.get("uuid")) for part in skin.get("chromas") or []]
    wearable = chromas[:1] + [part for part in chromas[1:] if part in (owned.get("chromas") or {})]
    chroma = chroma_uuid if chroma_uuid in wearable else (wearable[0] if wearable else "")
    weapon_uuid = _id(weapon.get("uuid"))

    def change(loadout: dict) -> None:
        for gun in loadout.get("Guns") or []:
            if isinstance(gun, dict) and _id(gun.get("ID")) == weapon_uuid:
                gun["SkinID"], gun["SkinLevelID"] = skin_uuid, have[-1]
                if chroma:
                    gun["ChromaID"] = chroma
                return
        raise RiotError("noweapon")

    return change, ""


def _wear_identity(found: dict, kind: str, uuid: str):
    owned = (found.get("owned") or {}).get(kind) or {}
    if uuid not in owned:
        return None, "unknown"

    def change(loadout: dict) -> None:
        identity = loadout.get("Identity")
        if not isinstance(identity, dict):
            raise RiotError("down", "equip: no Identity")
        identity[IDENTITY[kind]] = uuid

    return change, ""


def _wear_expression(found: dict, kind: str, uuid: str, slot: str):
    """The change that puts an owned spray or flex (``kind``) in one place of the wheel (one the loadout has). One
    already elsewhere on the wheel swaps places with what is there, as the game does."""
    content = current_app.config["VAL_CONTENT"]
    index = content.index(owned_items.LISTS[kind], g.lang)
    owned = {_id((index.get(item) or {}).get("uuid")) for item in (found.get("owned") or {}).get(kind) or {}}
    if uuid not in owned:  # PD names a spray's level (its uuid or its level's)
        return None, "unknown"
    loadout = found.get("loadout") if isinstance(found.get("loadout"), dict) else {}
    if slot not in {place["slot"] for place in owned_items.wheel(loadout, content, g.lang, kind)}:
        return None, "unknown"
    wanted = ITEM_TYPES[kind]

    def change(loadout: dict) -> None:
        if isinstance(loadout.get("ActiveExpressions"), list):  # playerloadout v3: the place is the position
            places = loadout["ActiveExpressions"]
            at = int(slot) if slot.isdigit() else -1
            if 0 <= at < len(places) and isinstance(places[at], dict):
                was = {"TypeID": places[at].get("TypeID"), "AssetID": places[at].get("AssetID")}
                for other in places:
                    if other is not places[at] and isinstance(other, dict) and _id(other.get("AssetID")) == uuid:
                        other.update(was)
                places[at]["TypeID"], places[at]["AssetID"] = wanted, uuid
                return
        elif kind == "sprays":
            places = [item for item in loadout.get("Sprays") or [] if isinstance(item, dict)]  # v2
            target = next((item for item in places if str(item.get("EquipSlotID")) == slot), None)
            if target is not None:
                for other in places:
                    if other is not target and _id(other.get("SprayID")) == uuid:
                        other["SprayID"], other["SprayLevelID"] = target.get("SprayID"), target.get("SprayLevelID")
                target["SprayID"], target["SprayLevelID"] = uuid, None
                return
        raise RiotError("down", "equip: no place on the wheel")

    return change, ""


def _wear_buddy(found: dict, uuid: str, weapon_uuid: str):
    """The change that puts an owned buddy on a weapon (``uuid`` "": takes the weapon's off). Each copy of a buddy
    goes on one weapon: a free copy is used, or the one on another weapon moves, as the game does."""
    content = current_app.config["VAL_CONTENT"]
    weapon = next((item for item in content.weapons(g.lang) if _id(item.get("uuid")) == weapon_uuid), None)
    if weapon is None:
        return None, "noweapon"
    if owned_items.melee(weapon):
        return None, "melee"
    copies: list[tuple[str, str]] = []
    if uuid:
        buddy = content.index("buddies", g.lang).get(uuid)
        if not buddy or _id(buddy.get("uuid")) != uuid:
            return None, "unknown"
        instances = found.get("instances") or {}
        copies = [
            (_id(level.get("uuid")), copy)
            for level in buddy.get("levels") or []
            for copy in instances.get(_id(level.get("uuid"))) or []
        ]
        if not copies:
            return None, "unknown"

    def change(loadout: dict) -> None:
        guns = [gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)]
        gun = next((gun for gun in guns if _id(gun.get("ID")) == weapon_uuid), None)
        if gun is None:
            raise RiotError("noweapon")
        if not uuid:
            for key in CHARM:
                gun.pop(key, None)
            return
        if _id(gun.get("CharmID")) == uuid:
            return
        used = {_id(other.get("CharmInstanceID")): other for other in guns if other.get("CharmInstanceID")}
        level, copy = next(((level, copy) for level, copy in copies if copy not in used), copies[0])
        if copy in used:  # every copy is on a weapon: this one comes off it
            for key in CHARM:
                used[copy].pop(key, None)
        gun["CharmInstanceID"], gun["CharmID"], gun["CharmLevelID"] = copy, uuid, level

    return change, ""


def _wear(change) -> str:
    """Sends ``change`` (riot.Riot.equip; RiotError when it did not work): what the page says, "ok", or "okopen"
    when the game is open (it draws the loadout it read when it started; a match takes Riot's)."""
    riot = current_app.config["VAL_RIOT"]
    riot.equip(change)
    try:
        return "okopen" if riot.live().get("running") else "ok"
    except RiotError:
        return "ok"


def _riot_said(detail: str) -> str | None:
    """Riot's own words when it refused (shown with the note, so a refusal can be told from a slow server)."""
    return re.sub(r"[^\w .:/-]", "", detail)[:80] or None


def register(app: Flask) -> None:
    @app.get("/progress")
    def progress():
        security.owner_only()
        try:
            found = current_app.config["VAL_RIOT"].contracts()
        except RiotError as error:
            values = _problem(error)
            return render_template("progress.html", **values), 503 if error.kind in ("busy", "down") else 200
        values = progress_page.page(found, current_app.config["VAL_CONTENT"], g.lang, time.time(), _when)
        return render_template("progress.html", **values)

    @app.get("/live")
    def live():
        security.owner_only()
        riot = current_app.config["VAL_RIOT"]
        try:
            found = riot.live()
        except RiotError as error:
            values = _problem(error)
            return render_template("live.html", **values), 503 if error.kind in ("busy", "down") else 200
        owned = None
        if found.get("phase") == "pregame":
            try:
                owned = set((riot.collection().get("owned") or {}).get("agents") or {}) | STARTERS
            except RiotError:
                owned = None
        me = _id(riot.store.load().get("puuid"))
        values = live_page.page(
            found, current_app.config["VAL_CONTENT"], g.lang, me, owned, _queue_name, _agents_on_the_map
        )
        said = session.pop("live_said", "")  # once: the page is asked again every few seconds
        words, worked = LIVE_SAID.get(said) or (RIOT_FAILURES.get(said, RIOT_FAILURES["down"]) if said else "", False)
        return render_template(
            "live.html",
            **values,
            names_missing="names" in (found.get("missing") or []),
            said=words,
            said_ok=worked,
            live_state=live_page.state(found, current_app.config["VAL_CONTENT"], g.lang),
            last_match=_match_link(_last_match(found)),
        )

    @app.get("/live/state")
    def live_state():
        """Where the owner's game is (live.state), for the header's ライブ tab: app.js asks every few seconds on
        every page while it is in view, and tells the owner when agent select starts. ``problem``: why it could not
        be read (RiotError's kind: nologin and signedout stop the asking)."""
        security.owner_only()
        try:
            found = current_app.config["VAL_RIOT"].live()
        except RiotError as error:
            answer = jsonify({"phase": "", "words": "", "problem": error.kind})
        else:
            _last_match(found)  # a match played while another page was open is linked from /live afterwards
            answer = jsonify(live_page.state(found, current_app.config["VAL_CONTENT"], g.lang) | {"problem": ""})
        answer.headers["Cache-Control"] = "no-store"
        return answer

    @app.post("/live/act")
    def live_act():
        """The owner's buttons on /live: start or stop a queue, pick or lock an agent (riot.Riot.act)."""
        security.owner_only()
        action = request.form.get("do", "")
        try:
            current_app.config["VAL_RIOT"].act(action, request.form.get("value", ""))
            session["live_said"] = f"ok-{action}"
        except RiotError as error:
            session["live_said"] = error.kind
        return redirect(url_for("live"), 303)

    @app.post("/collection/equip")
    def equip():
        security.owner_only()
        show = request.form.get("show", "skins")
        show = show if show in ("skins", "buddies", "cards", "titles", "sprays", "flex") else "skins"
        uuid = _id(request.form.get("item"))
        riot = current_app.config["VAL_RIOT"]
        # the open weapon's buddy (its own buttons: put the picked one on, or take it off)
        buddy_on = _id(request.form.get("buddy") or request.form.get("unbuddy"))[:36] if show == "skins" else ""
        # back to where the button was: the skins of that weapon (the game's collection opens one at a time)
        known = current_app.config["VAL_CONTENT"].skins(g.lang).get(uuid) if show == "skins" else None
        weapon = buddy_on or (_id(known[0].get("uuid")) if known else None)

        def back(why: str, detail: str = ""):
            return redirect(
                url_for(
                    "collection",
                    show=None if show == "skins" else show,
                    weapon=weapon,
                    equip=why,
                    riot=_riot_said(detail),
                ),
                303,
            )

        try:
            found = riot.collection()
        except RiotError as error:
            return back(error.kind, error.detail)
        if buddy_on:
            put = _id(request.form.get(f"buddy.{buddy_on}")) if request.form.get("buddy") else ""
            change, why = _wear_buddy(found, put, buddy_on)
        elif show == "skins":
            change, why = _wear_skin(found, uuid, _id(request.form.get(f"chroma.{uuid}")))
        elif show == "buddies":
            change, why = _wear_buddy(found, uuid, _id(request.form.get(f"weapon.{uuid}")))
        elif show in ("sprays", "flex"):
            change, why = _wear_expression(found, show, uuid, request.form.get(f"slot.{uuid}", ""))
        else:
            change, why = _wear_identity(found, show, uuid)
        if change is not None:
            try:
                why = _wear(change)
            except RiotError as error:
                return back(error.kind, error.detail)
        return back(why)

    @app.post("/collection/sets")
    def loadout_sets():
        """The owner's sets: ``do`` save (what is worn now, read from Riot this moment, with a name), use (wear a
        set again) or delete."""
        security.owner_only()
        store = current_app.config["VAL_SETS"]
        sets = [item for item in store.load().get("sets") or [] if isinstance(item, dict)]
        do, picked = request.form.get("do", ""), request.form.get("set", "")[:32]

        def back(why: str, detail: str = ""):
            return redirect(url_for("collection", show="sets", equip=why, riot=_riot_said(detail)), 303)

        if do == "save":
            if len(sets) >= owned_items.MAX_SETS:
                return back("full")
            try:
                loadout = current_app.config["VAL_RIOT"].loadout()
            except RiotError as error:
                return back(error.kind, error.detail)
            name = " ".join(request.form.get("name", "").split())[:30] or f"セット {len(sets) + 1}"
            when = datetime.now(JST).isoformat(timespec="minutes")
            sets.append({"id": secrets.token_hex(8), "name": name, "at": when, **owned_items.snapshot(loadout)})
            store.save(sets=sets)
            return back("saved")
        kept = next((item for item in sets if item.get("id") == picked), None)
        if kept is None:
            return back("noset")
        if do == "delete":
            store.save(sets=[item for item in sets if item is not kept])
            return back("deleted")
        try:
            return back(_wear(owned_items.wear_set(kept)))
        except RiotError as error:
            return back(error.kind, error.detail)


def equip_said(kind: str) -> tuple[str, bool]:
    """The collection page's words after wearing something: (words, whether it worked)."""
    if not kind:
        return "", False
    if kind == "ok":
        return "装備しました。Riot に保存されています。", True
    if kind == "saved":
        return "いまの装備をセットに保存しました。", True
    if kind == "deleted":
        return "セットを消しました。", True
    if kind == "okopen":
        return (
            "装備しました。Riot に保存されています。開いているゲームでは次の試合から使われます"
            "（ゲームのコレクション画面は、開き直すまで前の装備のままのことがあります）。",
            True,
        )
    return EQUIP_FAILURES.get(kind) or RIOT_FAILURES.get(kind) or RIOT_FAILURES["down"], False
