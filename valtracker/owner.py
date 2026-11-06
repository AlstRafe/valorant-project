"""The owner's pages beyond the store and the collection (Japanese only, refused to visitors):

- /progress: the battle pass, the events' passes, the agents' contracts and the missions (progress.py);
- /live: the game right now (live.py), asked again every few seconds: the menus (the party, start or stop a
  queue: POST /live/act), agent select (pick and lock an agent) or the match;
- POST /collection/equip: wear a skin (one of its variants), a card or a title, as the game's collection does.
"""

from __future__ import annotations

import re
import time

from flask import Flask, current_app, g, jsonify, redirect, render_template, request, session, url_for

from valtracker import live as live_page
from valtracker import progress as progress_page
from valtracker import security
from valtracker.activity import LINE_QUEUES
from valtracker.collection import STARTERS
from valtracker.henrik import REGIONS
from valtracker.riot import RiotError
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


def _wear_spray(found: dict, uuid: str, slot: str):
    """The change that puts an owned spray in one place of the wheel (one the loadout has)."""
    index = current_app.config["VAL_CONTENT"].index("sprays", g.lang)
    owned = {_id((index.get(item) or {}).get("uuid")) for item in (found.get("owned") or {}).get("sprays") or {}}
    if uuid not in owned:  # PD names a spray's level (its uuid or its level's)
        return None, "unknown"
    loadout = found.get("loadout") if isinstance(found.get("loadout"), dict) else {}
    slots = [str(spray.get("EquipSlotID")) for spray in loadout.get("Sprays") or [] if isinstance(spray, dict)]
    if slot not in slots:
        return None, "unknown"

    def change(loadout: dict) -> None:
        for spray in loadout.get("Sprays") or []:
            if isinstance(spray, dict) and str(spray.get("EquipSlotID")) == slot:
                spray["SprayID"], spray["SprayLevelID"] = uuid, None
                return
        raise RiotError("down", "equip: no spray slot")

    return change, ""


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
        show = show if show in ("skins", "cards", "titles", "sprays") else "skins"
        uuid = _id(request.form.get("item"))
        riot = current_app.config["VAL_RIOT"]
        # back to where the button was: the skins of that weapon (the game's collection opens one at a time)
        known = current_app.config["VAL_CONTENT"].skins(g.lang).get(uuid) if show == "skins" else None
        weapon = _id(known[0].get("uuid")) if known else None

        def back(why: str):
            return redirect(
                url_for("collection", show=None if show == "skins" else show, weapon=weapon, equip=why), 303
            )

        try:
            found = riot.collection()
        except RiotError as error:
            return back(error.kind)
        if show == "skins":
            change, why = _wear_skin(found, uuid, _id(request.form.get(f"chroma.{uuid}")))
        elif show == "sprays":
            change, why = _wear_spray(found, uuid, request.form.get(f"slot.{uuid}", ""))
        else:
            change, why = _wear_identity(found, show, uuid)
        if change is not None:
            try:
                riot.equip(change)
                why = "ok"
            except RiotError as error:
                why = error.kind
        return back(why)


def equip_said(kind: str) -> tuple[str, bool]:
    """The collection page's words after wearing something: (words, whether it worked)."""
    if not kind:
        return "", False
    if kind == "ok":
        return "装備しました。ゲームのコレクションにも出ています。", True
    return EQUIP_FAILURES.get(kind) or RIOT_FAILURES.get(kind) or RIOT_FAILURES["down"], False
