"""The PC app's checkup (/checkup): whether each thing the app reads can be read on this PC right now.

The app reads from four places: the Riot Client on this PC (who is signed in, where the game is, its events, the
friends), Riot's own servers (the match, the collection, the store, the passes, the match history), the names and
pictures of valorant-api.com, and HenrikDev (other players). When a page stays empty, this page says which of them
does not answer, in the owner's words, and gives a report to paste where help is asked.

The report holds statuses and the shapes of the answers only: no name, no id, no token (ids in what a server said
are taken out, ``scrub``).

The Riot Client's part is local and read as the page opens. The rest asks servers (about ten small requests), so
it runs only when the owner presses the button (``far``).
"""

from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from valtracker import friends
from valtracker.henrik import REGIONS, HenrikError
from valtracker.localclient import moment
from valtracker.riot import RiotError

#: The groups' titles.
GROUPS = {
    "local": "この PC の Riot クライアント",
    "riot": "Riot のサーバー",
    "others": "ほかの取得元",
    "app": "アプリ",
}
#: What is checked, by its key in the report.
NAMES = {
    "lockfile": "Riot クライアント",
    "token": "サインイン",
    "name": "Riot ID",
    "game": "VALORANT の起動",
    "server": "ゲームのサーバー",
    "presence": "自分の居場所",
    "events": "クライアントからの知らせ",
    "friends": "フレンド一覧",
    "live": "いまのゲーム（ライブ）",
    "collection": "所持品",
    "shop": "ストア",
    "contracts": "進み具合（パスと契約）",
    "agents": "持っているエージェント",
    "history": "自分の試合の履歴",
    "rr": "自分の RR の動き",
    "content": "名前と画像（valorant-api.com）",
    "henrik": "ほかのプレイヤーの検索（HenrikDev）",
    "site": "新しい版の確認（VAL Tracker のサイト）",
    "build": "この版",
    "calm": "ゲームを優先する設定",
    "folder": "データのフォルダー",
}
#: What is said of each (the words take the values of the same item).
SAID = {
    "open": "開いています",
    "closed": "開いていません。Riot クライアントか VALORANT を開いてから、もう一度確かめてください。",
    "signed": "サインインしています",
    "unsigned": "サインインを読めません（Riot クライアントでサインインしているか確かめてください）",
    "read": "読めました",
    "unread": "読めません",
    "part": "一部だけ読めました（読めなかったもの: {missing}）",
    "running": "開いています（サーバー: {shard}）",
    "notrunning": "開いていません（開くと、居場所とライブも確かめられます）",
    "server": "{region} / {shard}",
    "noserver": "まだわかりません（VALORANT を開くと決まります。それまでは ap として読みます）",
    "there": "{where}（{shape}）",
    "notthere": "VALORANT は開いていますが、自分の居場所が届いていません",
    "strange": "知らない形の居場所です（報告をコピーして知らせてください）",
    "nogame": "VALORANT を開くと確かめられます",
    "listening": "待っています（これまでに 居場所 {presences} 回・起動 {sessions} 回・試合の知らせ {pushes} 回）",
    "deaf": "つながっていません（数秒おきにつなぎ直します）",
    "people": "{count} 人",
    "phase": "読めました（{where}）",
    "names": "エージェント {agents}・マップ {maps}",
    "nokey": "キーがありません（設定に入れると、ほかのプレイヤーを検索できます）",
    "reached": "つながりました",
    "unreached": "つながりません",
    "source": "ソースから実行中",
    "built": "{date} · {commit}",
    "calm": "有効です（アプリの優先度を下げて、CPU とメモリーをゲームに譲っています）",
    "notcalm": "優先度を下げられませんでした",
    "notwindows": "Windows ではないので、何もしていません",
    "writable": "書き込めます",
    "readonly": "書き込めません",
}
#: A presence's two shapes, as the page names them.
SHAPES = {"nested": "いまの形", "flat": "以前の形"}
#: Where the game is, as the page says it (friends.WORDS' and live.STATE_WORDS' words).
WHERE = {
    "menus": "メニュー",
    "queue": "マッチング中",
    "found": "試合が見つかりました",
    "pregame": "エージェント選択中",
    "ingame": "対戦中",
    "": "VALORANT は開いていません",
}
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG = re.compile(r"[A-Za-z0-9_\-+/]{40,}")


def scrub(text: str) -> str:
    """What a server said, without the ids and anything as long as a token."""
    return _LONG.sub("<…>", _ID.sub("<id>", str(text)))[:160]


def _item(key: str, state: str, said: str, code: str = "", **values) -> dict:
    """One line of the page: ``state`` is ok, part, bad, skip (cannot be checked now) or info."""
    return {"key": key, "name": NAMES[key], "state": state, "words": SAID[said], "values": values, "code": scrub(code)}


def _flags(found: dict, *keys: str) -> str:
    return " ".join(f"{key}={int(bool(found.get(key)))}" for key in keys)


def local_items(local) -> list[dict]:
    """The Riot Client on this PC: what LocalClient.examine found."""
    seen = local.examine()
    if not seen["lockfile"]:
        return [_item("lockfile", "bad", "closed", "no lockfile")]
    token, chat, sessions, presence = seen["token"], seen["chat"], seen["sessions"], seen["presence"]
    signed = token["status"] == 200 and token["access"] and token["entitlements"] and token["subject"]
    named = chat["status"] == 200 and chat["name"] and chat["mine"]
    running = bool(sessions.get("running"))
    items = [
        _item("lockfile", "ok", "open"),
        _item(
            "token",
            "ok" if signed else "bad",
            "signed" if signed else "unsigned",
            f"{token['status']} {_flags(token, 'access', 'entitlements', 'subject')}",
        ),
        _item(
            "name",
            "ok" if named else "bad",
            "read" if named else "unread",
            f"{chat['status']} {_flags(chat, 'name', 'mine')} state={chat['state']}",
        ),
    ]
    code = (
        f"{sessions['status']} running={int(running)} shard={sessions.get('shard') or '-'} "
        f"version={sessions.get('version') or '-'} products={','.join(sessions['products']) or '-'}"
    )
    if sessions["status"] != 200:
        items.append(_item("game", "bad", "unread", code))
    elif running:
        items.append(_item("game", "ok", "running", code, shard=sessions.get("shard") or "?"))
    else:
        items.append(_item("game", "skip", "notrunning", code))
    log = seen.get("log")
    if log or sessions.get("shard"):
        region, shard = log if log and (not sessions.get("shard") or log[1] == sessions["shard"]) else ("", "")
        shard = shard or sessions.get("shard") or ""
        code = f"launch={sessions.get('shard') or '-'} log={'/'.join(log) if log else '-'}"
        items.append(_item("server", "ok", "server", code, region=region or shard, shard=shard))
    else:
        items.append(_item("server", "info", "noserver", "launch=- log=-"))
    code = (
        f"{presence['status']} count={presence['count']} mine={int(presence['mine'])} "
        f"shape={presence['shape'] or '-'} loop={presence['loop'] or '-'} party_state={presence['party_state'] or '-'} "
        f"party={int(presence['party'])} queue={presence['queue'] or '-'} keys={','.join(presence['keys']) or '-'}"
    )
    if presence["mine"] and presence["shape"] and presence["loop"]:
        place = moment({"loop": presence["loop"], "party_state": presence["party_state"]})
        items.append(
            _item("presence", "ok", "there", code, where=WHERE.get(place, place), shape=SHAPES[presence["shape"]])
        )
    elif presence["mine"]:
        items.append(_item("presence", "bad", "strange", code))
    elif running:
        items.append(_item("presence", "bad", "notthere", code))
    else:
        items.append(_item("presence", "skip", "nogame", code))
    heard = seen["heard"]
    items.append(
        _item(
            "events",
            "ok" if seen["listening"] else "bad",
            "listening" if seen["listening"] else "deaf",
            f"listening={int(seen['listening'])} " + " ".join(f"{key}={heard[key]}" for key in sorted(heard)),
            **heard,
        )
    )
    listed = local.friends()
    if listed is None:
        items.append(_item("friends", "bad", "unread", "no answer"))
    else:
        count = len(listed["friends"])
        items.append(
            _item("friends", "ok", "people", f"friends={count} presences={len(listed['presences'])}", count=count)
        )
    return items


def _riot(key: str, read, said=None) -> dict:
    """One read of Riot's servers: read, read in part (``missing``), or why not."""
    try:
        found = read()
    except RiotError as error:
        return _item(key, "skip" if error.kind == "nologin" else "bad", "unread", f"{error.kind} {error.detail}")
    missing = [str(item) for item in found.get("missing") or []] if isinstance(found, dict) else []
    if missing:
        code = f"missing={','.join(missing)} {found.get('problem') or ''}"
        return _item(key, "part", "part", code, missing="、".join(missing))
    return said(found) if said else _item(key, "ok", "read")


def riot_items(riot) -> list[dict]:
    """Riot's own servers, with the owner's sign-in: each of the app's reads once."""
    status, puuid = riot.status(), riot.me()
    region = status["shard"] if status["shard"] in REGIONS else "ap"

    def lived(found: dict) -> dict:
        phase = str(found.get("phase") or "")
        code = f"phase={phase or '-'} running={found.get('running')}"
        return _item("live", "ok", "phase", code, where=WHERE.get(phase, phase))

    def counted(key: str, count: int) -> dict:
        return _item(key, "ok", "read", f"count={count}")

    return [
        _riot("live", riot.live, lived),
        _riot("collection", riot.collection),
        _riot("shop", riot.shop),
        _riot("contracts", riot.contracts),
        _riot("agents", lambda: {"agents": riot.agents()}, lambda found: counted("agents", len(found["agents"]))),
        _riot(
            "history",
            lambda: riot.history(region, puuid),
            lambda found: counted("history", len(found.get("matches") or [])),
        ),
        _riot("rr", lambda: riot.rr_updates(region, puuid), lambda found: counted("rr", int(found.get("count") or 0))),
    ]


def other_items(app, lang: str, ask_site) -> list[dict]:
    """The names and pictures, HenrikDev, and the site that tells of a newer build."""
    content, henrik = app.config["VAL_CONTENT"], app.config["VAL_HENRIK"]
    agents, maps = len(content.agents(lang)), len({item.get("uuid") for item in content.map_index(lang).values()})
    items = [
        _item("content", "ok", "names", f"agents={agents} maps={maps}", agents=agents, maps=maps)
        if agents and maps
        else _item("content", "bad", "unread", f"agents={agents} maps={maps}")
    ]
    if not app.config["VAL_SETTINGS"].henrik_key():
        items.append(_item("henrik", "skip", "nokey", "no key"))
    else:
        try:
            henrik.status("ap")
            items.append(_item("henrik", "ok", "reached"))
        except HenrikError as error:
            items.append(_item("henrik", "bad", "unreached", f"{error.kind} {error.detail}"))
    site = app.config.get("VAL_PUBLIC") or ""
    try:
        found = ask_site(site)
        ok = isinstance(found, dict) and bool(found.get("commit"))
        items.append(_item("site", "ok" if ok else "bad", "reached" if ok else "unreached", "" if ok else "no commit"))
    except (OSError, ValueError) as error:
        items.append(_item("site", "bad", "unreached", type(error).__name__))
    return items


def app_items(app) -> list[dict]:
    build = app.config.get("VAL_BUILD")
    items = [
        _item("build", "info", "built", date=build["date"], commit=build["commit"][:7])
        if build
        else _item("build", "info", "source")
    ]
    calm = app.config.get("VAL_CALM") or []
    if any(done.startswith("priority") for done in calm):
        items.append(_item("calm", "ok", "calm", "; ".join(calm)))
    elif sys.platform == "win32":
        items.append(_item("calm", "bad", "notcalm", "; ".join(calm) or "nothing set"))
    else:
        items.append(_item("calm", "skip", "notwindows", sys.platform))
    folder = Path(app.instance_path)
    writable = os.access(folder, os.W_OK)
    items.append(_item("folder", "ok" if writable else "bad", "writable" if writable else "readonly"))
    return items


def _site(site: str):
    from valtracker.desktop import _latest

    return _latest(site)


def run(app, far: bool, lang: str = "ja", ask_site=None) -> list[dict]:
    """The page's groups: ``[{"key", "name", "items": [...]}]``. ``far``: Riot's servers and the other sources
    too (else they are left out)."""
    groups = [("local", local_items(app.config["VAL_LOCAL"]))]
    if far:
        groups += [("riot", riot_items(app.config["VAL_RIOT"])), ("others", other_items(app, lang, ask_site or _site))]
    groups.append(("app", app_items(app)))
    return [{"key": key, "name": GROUPS[key], "items": items} for key, items in groups]


def report(groups: list[dict], build: dict | None) -> str:
    """The checkup as plain lines to paste: keys, states and what was found, nothing personal."""
    lines = [
        "VAL Tracker checkup",
        f"build: {build['date']} {build['commit'][:7]}" if build else "build: source",
        f"system: {platform.system()} {platform.release()} {platform.version()}"[:80],
    ]
    for group in groups:
        lines.append(f"[{group['key']}]")
        lines.extend(f"{item['key']}: {item['state']} {item['code']}".rstrip() for item in group["items"])
    return "\n".join(lines)


#: Every text of this page, for the translations' test.
TEXTS = (*GROUPS.values(), *NAMES.values(), *SAID.values(), *SHAPES.values(), *WHERE.values(), *friends.WORDS.values())
