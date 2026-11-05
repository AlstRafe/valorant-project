"""The owner's pages beyond the store and the collection (Japanese only, refused to visitors):

- /progress: the battle pass, the events' passes, the agents' contracts and the missions (progress.py);
- /live: the match being played, at agent select or in the match (live.py), asked again every few seconds;
- POST /collection/equip: wear a skin (one of its variants), a card or a title, as the game's collection does.
"""

from __future__ import annotations

import time

from flask import Flask, current_app, g, redirect, render_template, request, url_for

from valtracker import live as live_page
from valtracker import progress as progress_page
from valtracker import security
from valtracker.collection import STARTERS
from valtracker.riot import RiotError
from valtracker.tools import _when
from valtracker.views import QUEUES, RIOT_FAILURES, _t

#: What can be worn from the collection page -> where the loadout keeps it.
IDENTITY = {"cards": "PlayerCardID", "titles": "PlayerTitleID"}
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
        values = live_page.page(found, current_app.config["VAL_CONTENT"], g.lang, me, owned, _queue_name)
        return render_template("live.html", **values, names_missing="names" in (found.get("missing") or []))

    @app.post("/collection/equip")
    def equip():
        security.owner_only()
        show = request.form.get("show", "skins")
        show = show if show in ("skins", "cards", "titles") else "skins"
        uuid = _id(request.form.get("item"))
        riot = current_app.config["VAL_RIOT"]
        try:
            found = riot.collection()
        except RiotError as error:
            return redirect(url_for("collection", show=None if show == "skins" else show, equip=error.kind), 303)
        if show == "skins":
            change, why = _wear_skin(found, uuid, _id(request.form.get(f"chroma.{uuid}")))
        else:
            change, why = _wear_identity(found, show, uuid)
        if change is not None:
            try:
                riot.equip(change)
                why = "ok"
            except RiotError as error:
                why = error.kind
        return redirect(url_for("collection", show=None if show == "skins" else show, equip=why), 303)


def equip_said(kind: str) -> tuple[str, bool]:
    """The collection page's words after wearing something: (words, whether it worked)."""
    if not kind:
        return "", False
    if kind == "ok":
        return "装備しました。ゲームのコレクションにも出ています。", True
    return EQUIP_FAILURES.get(kind) or RIOT_FAILURES.get(kind) or RIOT_FAILURES["down"], False
