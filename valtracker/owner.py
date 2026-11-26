"""ストアとコレクション以外の持ち主のページ（ページの言語で、訪問者には断る）:

- /progress: バトルパス、イベントのパス、エージェントのコントラクト、ミッション（progress.py）
- /live: 今のゲーム（live.py）を数秒ごとに聞き直す: メニュー（パーティー、キューの開始・停止: POST /live/act）、
  エージェント選択（ピックとロック）、試合
- POST /collection/equip: ゲームのコレクションと同じく、スキン（そのバリアントの 1 つ）、武器のガンバディ、カード、
  称号、ホイールのスプレーかフレックスを装備する
- POST /collection/sets: ロードアウト全体をセットとして保存（instance/loadout_sets.json）、また装備、削除
- /friends（PC アプリだけ）: この PC の Riot Client が出す持ち主のフレンドとそれぞれの居場所（friends.py）
- /checkup（PC アプリだけ）: アプリが読むものが今この PC で読めるか（checkup.py）
- POST /app/update と /app/update/state（PC アプリだけ）: アプリの自動更新（update.py）と、その進み具合をページに伝える"""

from __future__ import annotations

import contextlib
import re
import secrets
import time
from datetime import datetime
from urllib.parse import urlsplit

from flask import Flask, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for

from valtracker import checkup as checkup_page
from valtracker import collection as owned_items
from valtracker import friends as friends_page
from valtracker import i18n, pdmatch, security
from valtracker import live as live_page
from valtracker import progress as progress_page
from valtracker import session as play_session
from valtracker.activity import LINE_QUEUES
from valtracker.collection import CHARM, STARTERS
from valtracker.content import media_path
from valtracker.henrik import REGIONS, slim_match
from valtracker.riot import ITEM_TYPES, RiotError
from valtracker.stats import JST
from valtracker.tools import moment_info
from valtracker.util import UUID, as_dict, lower_id
from valtracker.views import match_row, match_score, queue_name, riot_failure, tr

#: /live/next が Riot Client の次のイベントを待つ秒数。過ぎたら「変化無し」と答える。
PUSH_WAIT = 25
#: 試合が終わってから、/live が持ち主の最後の試合へのリンクを出す時間。
LAST_MATCH_KEPT = 3 * 3600
#: 持ち主の最後の試合を試合履歴に聞く間隔（owner._last_played）。
LAST_PLAYED_EVERY = 120
#: Riot からの最後の試合の結果（_last_result）: 最大 RESULT_TRIES 回、
#: RESULT_EVERY 秒に 1 回まで聞く（試合が終わって数秒で出る。射撃場には無い）。少しあとに書かれる RR も同じ。
RESULT_EVERY, RESULT_TRIES = 12.0, 8
#: 「今回のセッション」（_session）のために持ち主の RR の記録を聞き直す間隔。
SESSION_EVERY = 120
IDENTITY = {"cards": "PlayerCardID", "titles": "PlayerTitleID"}
#: /live のボタンを押した後の文: （文, うまくいったか）。
LIVE_SAID = {
    "ok-start": ("キューを開始しました。", True),
    "ok-leave": ("キューをキャンセルしました。", True),
    "ok-select": ("選択しました（未確定）。", True),
    "ok-lock": ("確定しました。", True),
    "notnow": (
        "今は実行できません（ゲームが未起動、パーティー外、またはエージェント選択外）。",
        False,
    ),
    "unknown": ("この操作はできません。", False),
}
#: アプリの自動更新（update.py）のページでの言い方: 進んでいる間と、止まった理由。
UPDATE_SAID = {
    "checking": "アップデートを確認中…",
    "downloading": "ダウンロード中… {percent}%",
    "ready": "ダウンロード完了。試合が終わったら「再起動して更新」を押してください。",
    "installing": "更新中です。アプリが数秒後に再起動します。",
}
UPDATE_FAILURES = {
    "notbuilt": "VALTracker.exe 版でのみ更新できます。",
    "unwritable": "アプリのフォルダーに書き込めないため更新できません。ダウンロードページから入れ直してください。",
    "playing": "試合中は更新できません。試合後にお試しください。",
    "nonewer": "最新バージョンです。",
    "notready": "新しいバージョンを準備中です。数分後にお試しください。",
    "network": "ダウンロードに失敗しました。少し待ってからお試しください。",
    "badfile": "ダウンロードしたファイルを検証できませんでした。もう一度お試しください。",
    "nostart": "新しいバージョンを起動できなかったため、今のバージョンのまま続けます。ダウンロードページから入れ直してください。",
}
#: 更新のボタン: 始めるものと、試合中にダウンロードしたビルドを入れ替えるもの。
UPDATE_BUTTONS = {"start": "いますぐ更新", "ready": "再起動して更新"}
#: コレクションのページで押してうまくいったときの文。
EQUIP_SAID = {
    "ok": "装備しました。",
    "okopen": "装備しました。ゲームには次の試合から反映されます。",
    "saved": "現在の装備をセットに保存しました。",
    "deleted": "セットを削除しました。",
}
#: 装備できなかった理由（コレクションのページに出す）。
EQUIP_FAILURES = {
    "unknown": "未所持のアイテムは装備できません。",
    "noweapon": "その武器はロードアウトにありません。",
    "notsaved": "装備が反映されませんでした。もう一度お試しください。",
    "melee": "近接武器にはガンバディーを付けられません。",
    "full": "セットは {count} 個までです。不要なものを削除してください。",
    "noset": "そのセットは存在しません。",
}


def _agents_on_the_map(map_id: str, queue: str) -> dict:
    """このサイトが見た試合での、そのキュー（ほかはコンペティティブ）・そのマップでの各エージェントの成績。"""
    queue = queue if queue in LINE_QUEUES else "competitive"
    return current_app.config["VAL_ACTIVITY"].agents(queue, "", map_id)


def _last_match(found: dict) -> str:
    """持ち主が最後にプレイした試合。プレイ中に（/live か、/live/state を聞くヘッダーのタブで）セッションに残し、
    終わったら /live からリンクする: その ID、または ""（プレイ中か、最近は無し）。"""
    match = as_dict(found.get("match"))
    playing = str(match.get("MatchID") or match.get("ID") or "").lower()
    if found.get("phase") == "ingame" and UUID.fullmatch(playing):
        kept = session.get("live_match")
        # 新しい試合のときと、時々だけ書く: 見るたびにセッション cookie が変わると、
        # ブラウザーが cookie ごとに持っているものを取り直してしまう
        if not isinstance(kept, list) or len(kept) != 2 or kept[0] != playing or time.time() - float(kept[1]) > 300:
            session["live_match"] = [playing, time.time()]
        return ""
    if found.get("phase") in ("ingame", "pregame"):
        return ""
    kept = session.get("live_match")
    if isinstance(kept, list) and len(kept) == 2:
        match_id, when = str(kept[0]), float(kept[1] or 0)
        if UUID.fullmatch(match_id) and time.time() - when < LAST_MATCH_KEPT:
            return match_id
    return _last_played()


def _last_played() -> str:
    """ゲーム自身の記録（PD の試合履歴）での持ち主の最後の試合。
    ここのどのページもプレイ中を見ていないとき用: PC アプリはゲームの裏では何も読まないし、
    サイトは閉じていたかもしれない。時々だけ聞く。 Riot が答えない、または試合の開始が何時間も前なら ""。"""
    riot = current_app.config["VAL_RIOT"]
    status, puuid = riot.status(game=True), riot.me(game=True)
    if not puuid:
        return ""
    kept = current_app.config.setdefault("VAL_LAST_PLAYED", {})
    now = time.time()
    if kept.get("puuid") != puuid or now - kept.get("asked", 0.0) >= LAST_PLAYED_EVERY:
        kept |= {"puuid": puuid, "asked": now, "id": "", "at": 0}
        try:
            matches = riot.history(status["shard"] if status["shard"] in REGIONS else "ap", puuid)["matches"]
        except RiotError:
            matches = []
        if matches:
            kept |= {"id": matches[0]["id"], "at": matches[0]["at"]}
    fresh = UUID.fullmatch(str(kept.get("id") or "")) and now - float(kept.get("at") or 0) < LAST_MATCH_KEPT
    return kept["id"] if fresh else ""


def _last_result(match_id: str) -> dict | None:
    """ゲーム自身のサーバーからの、終わった瞬間の持ち主の最後の試合（ここの試合ページは HenrikDev を待つので数分かかる）
     : プレイヤーの一覧と同じ形の行（views.match_row）と、動いた RR。 Riot がまだ持っていない、
    または記録の無い試合（射撃場）なら None。1 試合につき Riot に聞くのは数回まで。試合はすぐ持ち主のアーカイブ（通算）
    に入る。"""
    if not match_id:
        return None
    riot = current_app.config["VAL_RIOT"]
    status, puuid = riot.status(game=True), riot.me(game=True)  # 試合をプレイしたアカウント
    if not puuid:
        return None
    region = status["shard"] if status["shard"] in REGIONS else "ap"
    kept = current_app.config.setdefault("VAL_LAST_RESULT", {})
    if kept.get("id") != match_id:
        kept.clear()
        kept |= {"id": match_id, "match": None, "rr": None, "tries": 0, "rr_tries": 0, "tried": 0.0, "rr_tried": 0.0}
    now = time.time()
    archive = current_app.config["VAL_ARCHIVE"]
    if kept["match"] is None:
        if kept["tries"] >= RESULT_TRIES or now - kept["tried"] < RESULT_EVERY:
            return None
        kept["tries"] += 1
        kept["tried"] = now
        try:
            details = riot.match_details(region, match_id)
        except RiotError:
            return None
        maps = current_app.config["VAL_CONTENT"].map_index("en-GB")
        raw = pdmatch.to_v4(details, region, maps, puuid, status["name"], status["tag"])
        if not raw or not raw["metadata"].get("is_completed", True):
            return None  # （まだ）終わった試合ではない
        match = kept["match"] = slim_match(raw)
        archive.keep([match], "riot")
    match = kept["match"]
    player = next((item for item in match.get("players") or [] if item.get("puuid") == puuid), None)
    if player is None:
        return None
    queue = str(((match.get("metadata") or {}).get("queue") or {}).get("id") or "").lower()
    # RR は試合の少しあとに書かれる: ページの次の数回の確認で聞く
    waiting = kept["rr"] is None and kept["rr_tries"] < RESULT_TRIES and now - kept["rr_tried"] >= RESULT_EVERY
    if queue == "competitive" and waiting:
        kept["rr_tries"] += 1
        kept["rr_tried"] = now
        with contextlib.suppress(RiotError):
            kept["rr"] = riot.rr_updates(region, puuid)["changes"].get(match_id)
        if kept["rr"]:
            archive.keep_rr(puuid, {match_id: kept["rr"]})
            told = current_app.config.get("VAL_SESSION") or {}
            if told.get("puuid") == puuid:  # 今回のセッションにすぐ入る（聞き直しを待たない）
                told["changes"] = {**told.get("changes", {}), match_id: kept["rr"]}
    row = match_row(match, player, {match_id: kept["rr"]} if kept["rr"] else {})
    row["score"] = match_score(row["line"], row["rounds"], row["queue_id"], row["tier"], row["result"]["won"])
    return row


def _session() -> dict | None:
    """/live の「今回のセッション」（session.summary）: ゲームをプレイしているアカウントの最近のコンペティティブの
    試合と RR。Riot に聞くのは SESSION_EVERY 秒に 1 回まで（ページは数秒ごとに読み直す）。各試合へのリンクと、
    今のランクの名前と絵を付ける。"""
    riot = current_app.config["VAL_RIOT"]
    status, puuid = riot.status(game=True), riot.me(game=True)
    if not puuid:
        return None
    region = status["shard"] if status["shard"] in REGIONS else "ap"
    kept = current_app.config.setdefault("VAL_SESSION", {})
    now = time.time()
    if kept.get("puuid") != puuid or now - kept.get("asked", 0.0) >= SESSION_EVERY:
        changes = kept.get("changes", {}) if kept.get("puuid") == puuid else {}
        with contextlib.suppress(RiotError):
            changes = riot.rr_updates(region, puuid)["changes"]
        kept.clear()
        kept |= {"puuid": puuid, "asked": now, "changes": changes}
    found = play_session.summary(kept["changes"], now)
    if found is None:
        return None
    me = f"{status['name']}#{status['tag']}" if status["name"] and status["tag"] else None
    for row in found["matches"]:
        row["link"] = url_for("match", region=region, match_id=row["id"], me=me) if UUID.fullmatch(row["id"]) else ""
        row["when"] = datetime.fromtimestamp(row["at"], JST).strftime("%H:%M")
    tier = current_app.config["VAL_CONTENT"].tiers(g.lang).get(found["tier"]) or {}
    name = str(tier.get("tierName") or "")
    found["rank"] = {"name": name.title() if name.isupper() else name, "icon": tier.get("smallIcon")}
    return found


def _match_link(match_id: str) -> str:
    """このサイトでの持ち主の最後の試合（本人の行に印、?me=）。"""
    if not match_id:
        return ""
    status = current_app.config["VAL_RIOT"].status(game=True)
    region = status["shard"] if status["shard"] in REGIONS else "ap"
    me = f"{status['name']}#{status['tag']}" if status["name"] and status["tag"] else None
    return url_for("match", region=region, match_id=match_id, me=me)


def _problem(error: RiotError) -> dict:
    return {
        "problem": riot_failure(error.kind),
        "detail": error.detail,
        "settings_link": error.kind not in ("busy", "down"),
    }


def _wear_skin(found: dict, skin_uuid: str, chroma_uuid: str):
    """このスキン（所持している一番良いレベル、選んだバリアント）を装備するロードアウトの変更、または装備できない理由。"""
    content = current_app.config["VAL_CONTENT"]
    known = content.skins(g.lang).get(skin_uuid)
    if not known or lower_id(known[1].get("uuid")) != skin_uuid:
        return None, "unknown"
    weapon, skin = known
    owned = found.get("owned") or {}
    levels = [lower_id(level.get("uuid")) for level in skin.get("levels") or []]
    have = [level for level in levels if level in (owned.get("skins") or {})]
    if not have:
        return None, "unknown"
    chromas = [lower_id(part.get("uuid")) for part in skin.get("chromas") or []]
    wearable = chromas[:1] + [part for part in chromas[1:] if part in (owned.get("chromas") or {})]
    chroma = chroma_uuid if chroma_uuid in wearable else (wearable[0] if wearable else "")
    weapon_uuid = lower_id(weapon.get("uuid"))

    def change(loadout: dict) -> None:
        for gun in loadout.get("Guns") or []:
            if isinstance(gun, dict) and lower_id(gun.get("ID")) == weapon_uuid:
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
    """所持しているスプレーかフレックス（``kind``）をホイールの 1 か所（ロードアウトにある場所）に入れる変更。
    ホイールのほかの場所にすでにあれば、ゲームと同じくそこにあるものと入れ替える。"""
    content = current_app.config["VAL_CONTENT"]
    index = content.index(owned_items.LISTS[kind], g.lang)
    owned = {lower_id((index.get(item) or {}).get("uuid")) for item in (found.get("owned") or {}).get(kind) or {}}
    if uuid not in owned:  # PD はスプレーをレベルで挙げる（その uuid か、レベルの uuid）
        return None, "unknown"
    loadout = as_dict(found.get("loadout"))
    if slot not in {place["slot"] for place in owned_items.wheel(loadout, content, g.lang, kind)}:
        return None, "unknown"
    wanted = ITEM_TYPES[kind]

    def change(loadout: dict) -> None:
        if isinstance(loadout.get("ActiveExpressions"), list):  # playerloadout v3: 場所は位置
            places = loadout["ActiveExpressions"]
            at = int(slot) if slot.isdigit() else -1
            if 0 <= at < len(places) and isinstance(places[at], dict):
                was = {"TypeID": places[at].get("TypeID"), "AssetID": places[at].get("AssetID")}
                for other in places:
                    if other is not places[at] and isinstance(other, dict) and lower_id(other.get("AssetID")) == uuid:
                        other.update(was)
                places[at]["TypeID"], places[at]["AssetID"] = wanted, uuid
                return
        elif kind == "sprays":
            places = [item for item in loadout.get("Sprays") or [] if isinstance(item, dict)]  # v2
            target = next((item for item in places if str(item.get("EquipSlotID")) == slot), None)
            if target is not None:
                for other in places:
                    if other is not target and lower_id(other.get("SprayID")) == uuid:
                        other["SprayID"], other["SprayLevelID"] = target.get("SprayID"), target.get("SprayLevelID")
                target["SprayID"], target["SprayLevelID"] = uuid, None
                return
        raise RiotError("down", "equip: no place on the wheel")

    return change, ""


def _wear_buddy(found: dict, uuid: str, weapon_uuid: str):
    """所持しているガンバディを武器に着ける変更（``uuid`` が "" なら外す）。
    コピー 1 つは武器 1 つにだけ着く: ゲームと同じく、空いているコピーを使うか、ほかの武器のものを移す。"""
    content = current_app.config["VAL_CONTENT"]
    weapon = next((item for item in content.weapons(g.lang) if lower_id(item.get("uuid")) == weapon_uuid), None)
    if weapon is None:
        return None, "noweapon"
    if owned_items.melee(weapon):
        return None, "melee"
    copies: list[tuple[str, str]] = []
    if uuid:
        buddy = content.index("buddies", g.lang).get(uuid)
        if not buddy or lower_id(buddy.get("uuid")) != uuid:
            return None, "unknown"
        instances = found.get("instances") or {}
        copies = [
            (lower_id(level.get("uuid")), copy)
            for level in buddy.get("levels") or []
            for copy in instances.get(lower_id(level.get("uuid"))) or []
        ]
        if not copies:
            return None, "unknown"

    def change(loadout: dict) -> None:
        guns = [gun for gun in loadout.get("Guns") or [] if isinstance(gun, dict)]
        gun = next((gun for gun in guns if lower_id(gun.get("ID")) == weapon_uuid), None)
        if gun is None:
            raise RiotError("noweapon")
        if not uuid:
            for key in CHARM:
                gun.pop(key, None)
            return
        if lower_id(gun.get("CharmID")) == uuid:
            return
        used = {lower_id(other.get("CharmInstanceID")): other for other in guns if other.get("CharmInstanceID")}
        level, copy = next(((level, copy) for level, copy in copies if copy not in used), copies[0])
        if copy in used:  # コピーが全部どれかの武器に着いている: これを外して使う
            for key in CHARM:
                used[copy].pop(key, None)
        gun["CharmInstanceID"], gun["CharmID"], gun["CharmLevelID"] = copy, uuid, level

    return change, ""


def _wear(change) -> str:
    """``change`` を送る（riot.Riot.equip。うまくいかなければ RiotError）: ページの文 "ok"、
    またはゲームが開いているなら "okopen"（ゲームは開始時に読んだロードアウトで描く。次の試合は Riot のものを使う）。"""
    riot = current_app.config["VAL_RIOT"]
    riot.equip(change)
    try:
        return "okopen" if riot.live().get("running") else "ok"
    except RiotError:
        return "ok"


def _riot_said(detail: str) -> str | None:
    """Riot が断ったときの Riot 自身の文（説明と一緒に出す。断られたのかサーバーが遅いだけかわかる）。"""
    return re.sub(r"[^\w .:/-]", "", detail)[:80] or None


def update_state() -> dict:
    """ページ用のアプリ自身の更新の進み具合:
    ページの言語での ``{"possible", "state", "busy", "words", "failed", "button"}``（``possible``:
    自分を入れ替えられるビルドしたアプリ）。"""
    updater = current_app.config["VAL_UPDATER"]
    if updater is None or not updater.possible:
        return {"possible": False, "state": "idle", "busy": False, "words": "", "failed": False, "button": ""}
    now = updater.state()
    state = now["state"]
    if state == "failed":
        words = tr(UPDATE_FAILURES.get(now["problem"]) or UPDATE_FAILURES["network"])
    elif state == "downloading":
        percent = round(100 * now["done"] / now["total"]) if now["total"] else 0
        words = tr(UPDATE_SAID["downloading"], percent=percent)
    else:
        words = tr(UPDATE_SAID[state]) if state in UPDATE_SAID else ""
    return {
        "possible": True,
        "state": state,
        "busy": state in ("checking", "downloading", "installing"),
        "words": words,
        "failed": state == "failed",
        "button": tr(UPDATE_BUTTONS["ready" if state == "ready" else "start"]),
    }


def register(app: Flask) -> None:
    @app.get("/progress")
    def progress():
        security.owner_only()
        try:
            found = current_app.config["VAL_RIOT"].contracts()
        except RiotError as error:
            values = _problem(error)
            return render_template("progress.html", **values), 503 if error.kind in ("busy", "down") else 200
        values = progress_page.page(found, current_app.config["VAL_CONTENT"], g.lang, time.time(), moment_info)
        return render_template("progress.html", **values)

    @app.get("/live")
    def live():
        security.owner_only()
        riot = current_app.config["VAL_RIOT"]
        local = current_app.config["VAL_LOCAL"]
        # ここまでのクライアントのイベントは今読んだものに入っている: /live/next は次のものから答える
        g.push_version = local.version if local is not None else 0
        try:
            found = riot.live()
        except RiotError as error:
            values = _problem(error)
            return render_template("live.html", **values), 503 if error.kind in ("busy", "down") else 200
        owned = None
        if found.get("phase") == "pregame":
            try:
                owned = riot.agents() | STARTERS  # マッチング中に先に読んだもの（riot.Riot.live）
            except RiotError:
                owned = None
        me = riot.me(game=True)
        values = live_page.page(
            found, current_app.config["VAL_CONTENT"], g.lang, me, owned, queue_name, _agents_on_the_map
        )
        # PC アプリ: ほかのページがどのアカウントを表示していても、ゲームは Riot Client のアカウントのもの
        playing, shown = riot.status(game=True), riot.status()
        other = bool(playing["linked"] and shown["linked"]) and (playing["name"], playing["tag"]) != (
            shown["name"],
            shown["tag"],
        )
        last = _last_match(found)
        said = session.pop("live_said", "")  # 1 回だけ: ページは数秒ごとに聞き直す
        words, worked = LIVE_SAID.get(said) or (riot_failure(said) if said else "", False)
        words = tr(words) if said in LIVE_SAID else words
        return render_template(
            "live.html",
            **values,
            names_missing="names" in (found.get("missing") or []),
            said=words,
            said_ok=worked,
            live_state=live_page.state(found, current_app.config["VAL_CONTENT"], g.lang),
            last_match=_match_link(last),
            last_result=_last_result(last) if found.get("phase") not in ("pregame", "ingame") else None,
            play_session=_session() if found.get("phase") not in ("pregame", "ingame") else None,
            # ゲームが閉じた: 持ち主のほかのページへのリンク（アカウントは設定ページと同じく読む）
            account=shown if not found.get("phase") else None,
            game_account=f"{playing['name']}#{playing['tag']}" if other else "",
        )

    @app.get("/live/state")
    def live_state():
        """ヘッダーのライブタブ用の持ち主のゲームの状態（live.state）: app.js が全ページで、
        見えている間は数秒ごとに聞き、エージェント選択が始まったら持ち主に知らせる。
        ``problem``: 読めなかった理由（RiotError の kind。nologin と signedout なら聞くのを止める）。"""
        security.owner_only()
        try:
            found = current_app.config["VAL_RIOT"].live()
        except RiotError as error:
            answer = jsonify({"phase": "", "words": "", "problem": error.kind})
        else:
            _last_match(found)  # 別のページを開いている間にプレイした試合は、あとで /live からリンクする
            told = live_page.state(found, current_app.config["VAL_CONTENT"], g.lang)
            told["icons"] = [url_for("media", path=media_path(url)) for url in told["icons"] if media_path(url)]
            answer = jsonify(told | {"problem": ""})
        answer.headers["Cache-Control"] = "no-store"
        return answer

    @app.get("/live/next")
    def live_next():
        """デスクトップアプリだけ: この PC の Riot Client が ``after`` より後の変化（試合成立、エージェント選択、
        ピック、ゲームの起動・終了）を知らせたら答える。何も無ければ PUSH_WAIT 秒後に答える。
        /live とライブタブは次の確認を待たずにすぐ読み直す。"""
        security.owner_only()
        local = current_app.config["VAL_LOCAL"]
        if local is None:
            abort(404)
        try:
            after = int(request.args.get("after", "-1"))
        except ValueError:
            after = -1
        answer = jsonify({"version": local.wait(after, PUSH_WAIT)})
        answer.headers["Cache-Control"] = "no-store"
        return answer

    @app.post("/account")
    def account_switch():
        """ページに出す Riot アカウントを持ち主が選ぶ（riot.Riot.switch）
        : 持ち主のもののある場所にあるボタン（_accounts.html）。ボタンのあったページに戻る。"""
        security.owner_only()
        riot = current_app.config["VAL_RIOT"]
        claim(riot.me())
        with contextlib.suppress(RiotError):  # その間に解除されたアカウント: ページは今あるものを出す
            riot.switch(request.form.get("account", "")[:36])
        return redirect(own_page(request.form.get("next", "")[:400]), 303)

    @app.get("/friends")
    def friends():
        """PC アプリだけ: この PC の Riot Client が出す持ち主のフレンド。
        ページが見えている間は数秒ごとに読み直す（ローカルの読み込み 2 回。ゲームにも Riot のサーバーにも聞かない）。"""
        security.owner_only()
        local = current_app.config["VAL_LOCAL"]
        if local is None:
            abort(404)
        found = local.friends()
        if found is None:
            return render_template("friends.html", closed=True)
        content = current_app.config["VAL_CONTENT"]
        look = {"tiers": content.tiers(g.lang), "maps": content.map_index(g.lang), "queue_name": queue_name}

        def when(moment: datetime) -> dict:
            moment = moment.astimezone(JST)
            return {"iso": moment.isoformat(), "text": i18n.when(g.lang, moment)}

        return render_template("friends.html", closed=False, **friends_page.rows(found, look, time.time(), when))

    @app.get("/friends/count")
    def friends_count():
        """PC アプリだけ: VALORANT にいるフレンドとオンラインのフレンドの数（トップページのカード用。
        描いたあとにページが聞く。フレンドページと同じローカルの読み込み 2 回で、数秒持つ）。"""
        security.owner_only()
        local = current_app.config["VAL_LOCAL"]
        if local is None:
            abort(404)
        found = local.friends()
        answer = {"open": False}
        if found is not None:
            look = {"tiers": {}, "maps": {}, "queue_name": lambda queue: ""}
            rows = friends_page.rows(found, look, time.time(), lambda moment: {"iso": "", "text": ""})
            playing = len(rows["playing"])
            answer = {"open": True, "playing": playing, "online": playing + len(rows["online"])}
        response = jsonify(answer)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/checkup", methods=["GET", "POST"])
    def checkup():
        """PC アプリだけ: 今この PC でアプリが読めるものを 1 つずつと、名前・ID・
        トークンを含まないそのレポート（助けを求める場所に貼る用）。
        Riot Client の部分はページを開いたときに読む（ローカル）。
        Riot のサーバーとほかの出どころは持ち主がボタンを押したときだけ。"""
        security.owner_only()
        local = current_app.config["VAL_LOCAL"]
        if local is None:
            abort(404)
        groups = checkup_page.run(current_app, far=request.method == "POST", lang=g.lang)
        return render_template(
            "checkup.html",
            groups=groups,
            far=request.method == "POST",
            report=checkup_page.report(groups, current_app.config.get("VAL_BUILD")),
        )

    @app.post("/app/update")
    def app_update():
        """PC アプリだけ: 持ち主の「更新」ボタン。最新ビルドをダウンロードして確かめ、動いているアプリと入れ替え、
        アプリは閉じてまた開く（update.py）。答えは状態（ページのスクリプトには JSON、ほかは設定ページ）。"""
        security.owner_only()
        updater = current_app.config["VAL_UPDATER"]
        if current_app.config["VAL_LOCAL"] is None or updater is None:
            abort(404)
        updater.press()
        if "application/json" in request.headers.get("Accept", ""):
            answer = jsonify(update_state())
            answer.headers["Cache-Control"] = "no-store"
            return answer
        return redirect(url_for("settings") + "#app", 303)

    @app.get("/app/update/state")
    def app_update_state():
        """PC アプリだけ: 更新の進み具合（進んでいる間、ページが毎秒聞く）。"""
        security.owner_only()
        if current_app.config["VAL_LOCAL"] is None:
            abort(404)
        answer = jsonify(update_state())
        answer.headers["Cache-Control"] = "no-store"
        return answer

    @app.post("/live/act")
    def live_act():
        """/live の持ち主のボタン: キューの開始・停止、エージェントのピック・ロック（riot.Riot.act）。"""
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
        uuid = lower_id(request.form.get("item"))
        riot = current_app.config["VAL_RIOT"]
        # 開いている武器のガンバディ（専用のボタン: 選んだものを着ける、または外す）
        buddy_on = lower_id(request.form.get("buddy") or request.form.get("unbuddy"))[:36] if show == "skins" else ""
        # ボタンのあった場所に戻る: その武器のスキン（ゲームのコレクションも 1 つずつ開く）
        known = current_app.config["VAL_CONTENT"].skins(g.lang).get(uuid) if show == "skins" else None
        weapon = buddy_on or (lower_id(known[0].get("uuid")) if known else None)

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
            put = lower_id(request.form.get(f"buddy.{buddy_on}")) if request.form.get("buddy") else ""
            change, why = _wear_buddy(found, put, buddy_on)
        elif show == "skins":
            change, why = _wear_skin(found, uuid, lower_id(request.form.get(f"chroma.{uuid}")))
        elif show == "buddies":
            change, why = _wear_buddy(found, uuid, lower_id(request.form.get(f"weapon.{uuid}")))
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
        """持ち主のセット: ``do`` が save（今の装備をその場で Riot から読み、名前を付けて）、use（セットをまた装備）、
        delete。"""
        security.owner_only()
        store = current_app.config["VAL_SETS"]
        me = current_app.config["VAL_RIOT"].me()
        every = [item for item in store.load().get("sets") or [] if isinstance(item, dict)]
        sets = sets_of(every, me)  # セットは保存したアカウントのもの
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
            name = " ".join(request.form.get("name", "").split())[:30] or tr("セット {number}", number=len(sets) + 1)
            when = datetime.now(JST).isoformat(timespec="minutes")
            saved = {"id": secrets.token_hex(8), "name": name, "at": when, "account": me}
            store.save(sets=[*every, saved | owned_items.snapshot(loadout)])
            return back("saved")
        kept = next((item for item in sets if item.get("id") == picked), None)
        if kept is None:
            return back("noset")
        if do == "delete":
            store.save(sets=[item for item in every if item is not kept])
            return back("deleted")
        try:
            return back(_wear(owned_items.wear_set(kept)))
        except RiotError as error:
            return back(error.kind, error.detail)


def sets_of(sets: list[dict], account: str) -> list[dict]:
    """1 つのアカウント（puuid）の保存セット。複数のアカウントを連携できる前に保存したセットはまだ誰のものでもなく（
    claim）、どのアカウントでも出る。"""
    return [item for item in sets if not item.get("account") or item.get("account") == account]


def claim(account: str) -> None:
    """アカウントが 1 つだった間に残したもの（ストア日記、保存セット）
    を ``account`` のものにする: 別のアカウントを連携・表示する直前に呼ぶので、次のアカウントは自分のものから始まる。"""
    if not account:
        return
    current_app.config["VAL_DIARY"].claim(account)
    store = current_app.config["VAL_SETS"]
    sets = [item for item in store.load().get("sets") or [] if isinstance(item, dict)]
    if any(not item.get("account") for item in sets):
        store.save(sets=[item if item.get("account") else item | {"account": account} for item in sets])


def own_page(address: str) -> str:
    """``address`` がこのサイトのページ（マウント下のパス）ならそれ、でなければトップページ: 持ち主のボタンの戻り先。"""
    parts = urlsplit(address)
    inside = address.startswith(request.script_root + "/") and not address.startswith("//")
    if parts.scheme or parts.netloc or not inside or "\\" in address:
        return url_for("top")
    return address


def equip_said(kind: str) -> tuple[str, bool]:
    """装備したあとのコレクションのページの文（ページの言語で）: （文, うまくいったか）。"""
    if not kind:
        return "", False
    if kind in EQUIP_SAID:
        return tr(EQUIP_SAID[kind]), True
    if kind in EQUIP_FAILURES:
        return tr(EQUIP_FAILURES[kind], count=owned_items.MAX_SETS), False
    return riot_failure(kind), False
