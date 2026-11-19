"""PC アプリのチェックアップ（/checkup）: アプリが読むものが、今この PC で読めるかを 1 つずつ確かめる。

アプリが読む場所は 4 つ: この PC の Riot Client（誰がサインインしているか、ゲームの状態、イベント、フレンド）、
Riot 自身のサーバー（試合、コレクション、ストア、パス、試合履歴）、valorant-api.com の名前と絵、
HenrikDev（ほかのプレイヤー）。ページが空のままのとき、どれが答えていないかを持ち主にわかる言葉で出し、
助けを求める場所に貼れるレポートも出す。

レポートに入るのはステータスと答えの形だけ: 名前も ID もトークンも入れない（サーバーの答えの中の ID は ``scrub``
で消す）。

Riot Client の部分はローカルなので、ページを開いたときに読む。残りはサーバーに聞く（小さなリクエスト 10 回ほど）ので、
持ち主がボタンを押したときだけ（``far``）。"""

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

#: グループの見出し。
GROUPS = {
    "local": "この PC の Riot クライアント",
    "riot": "Riot のサーバー",
    "others": "外部データ",
    "app": "アプリ",
}
#: 確かめるもの（レポートでのキー）。
NAMES = {
    "lockfile": "Riot クライアント",
    "token": "サインイン",
    "name": "Riot ID",
    "game": "VALORANT の起動",
    "server": "ゲームのサーバー",
    "presence": "自分のステータス",
    "parties": "試合のパーティー",
    "events": "クライアントからの通知",
    "friends": "フレンド一覧",
    "live": "現在のゲーム（ライブ）",
    "collection": "所持品",
    "shop": "ストア",
    "contracts": "進行状況（パスと契約）",
    "agents": "所持エージェント",
    "history": "自分の試合履歴",
    "rr": "自分の RR 履歴",
    "content": "名前と画像（valorant-api.com）",
    "henrik": "プレイヤー検索（HenrikDev）",
    "site": "アップデート確認（VAL Tracker のサイト）",
    "build": "バージョン",
    "calm": "ゲーム優先モード",
    "folder": "データフォルダー",
}
#: それぞれの結果の文（同じ項目の値を埋める）。
SAID = {
    "open": "起動中",
    "closed": "起動していません。Riot クライアントか VALORANT を起動して再チェックしてください。",
    "signed": "サインイン済み",
    "unsigned": "サインイン情報を取得できません（Riot クライアントのサインインを確認してください）",
    "read": "取得できました",
    "unread": "取得できません",
    "part": "一部のみ取得（未取得: {missing}）",
    "running": "起動中（サーバー: {shard}）",
    "notrunning": "起動していません（起動するとステータスとライブもチェックできます）",
    "server": "{region} / {shard}",
    "noserver": "未確定（VALORANT の起動後に決まります。それまでは ap 扱い）",
    "there": "{where}（{shape}）",
    "notthere": "VALORANT は起動中ですが、自分のステータスが届いていません",
    "strange": "未対応の形式です（レポートをコピーして報告してください）",
    "nogame": "VALORANT の起動後にチェックできます",
    "partiesin": "直近の対戦: {total} 人中 {known} 人のパーティーがわかりました",
    "partiespre": "直近のエージェント選択: 味方 {total} 人中 {known} 人（相手チームは対戦が始まってから届きます）",
    "nomatch": "試合中か試合のあとにチェックできます",
    "listening": "待機中（受信: ステータス {presences} 回・起動 {sessions} 回・試合通知 {pushes} 回）",
    "deaf": "未接続（数秒おきに再接続します）",
    "people": "{count} 人",
    "phase": "取得できました（{where}）",
    "names": "エージェント {agents}・マップ {maps}",
    "nokey": "キー未設定（設定するとプレイヤー検索が使えます）",
    "reached": "接続 OK",
    "unreached": "接続できません",
    "source": "ソースから実行中",
    "built": "{date} · {commit}",
    "calm": "有効（アプリの優先度を下げ、CPU とメモリーをゲームに譲ります）",
    "notcalm": "優先度を下げられませんでした",
    "notwindows": "Windows 以外のため無効",
    "writable": "書き込み可",
    "readonly": "書き込み不可",
}
#: プレゼンスの 2 つの形（ページでの呼び方）。
SHAPES = {"nested": "現行形式", "flat": "旧形式"}
#: ページでのゲームの状態の言い方（friends.WORDS と live.STATE_WORDS の文）。
WHERE = {
    "menus": "メニュー",
    "queue": "マッチング中",
    "found": "試合が見つかりました",
    "pregame": "エージェント選択中",
    "ingame": "対戦中",
    "": "VALORANT 未起動",
}
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG = re.compile(r"[A-Za-z0-9_\-+/]{40,}")


def scrub(text: str) -> str:
    """サーバーの答えから ID とトークンほど長いものを消したもの。"""
    return _LONG.sub("<…>", _ID.sub("<id>", str(text)))[:160]


def _item(key: str, state: str, said: str, code: str = "", **values) -> dict:
    """ページの 1 行: ``state`` は ok、part、bad、skip（今は確かめられない）、info のどれか。"""
    return {"key": key, "name": NAMES[key], "state": state, "words": SAID[said], "values": values, "code": scrub(code)}


def _flags(found: dict, *keys: str) -> str:
    return " ".join(f"{key}={int(bool(found.get(key)))}" for key in keys)


def local_items(local) -> list[dict]:
    """この PC の Riot Client: LocalClient.examine が見つけたもの。"""
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
    items.append(_parties_item(seen["parties"]))
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


def _parties_item(parties: dict) -> dict:
    """試合のパーティー: 直近の試合で、同じ試合の人のプレゼンス（partyId）が何人分届いたか。数だけ。"""
    match = parties["match"]
    code = f"others={parties['others']} party={parties['with_party']} empty={parties['empty']} jwt={parties['jwt']}"
    code += "".join(f" {phase}={counts['known']}/{counts['players']}" for phase, counts in sorted(match.items()))
    for phase, said in (("ingame", "partiesin"), ("pregame", "partiespre")):
        counts = match.get(phase)
        if counts:
            whole = counts["known"] >= counts["players"]
            return _item(
                "parties", "ok" if whole else "part", said, code, total=counts["players"], known=counts["known"]
            )
    return _item("parties", "skip", "nomatch", code)


def _riot(key: str, read, said=None) -> dict:
    """Riot のサーバーを 1 回読んだ結果: 読めた、一部だけ読めた（``missing``）、読めない理由。"""
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
    """持ち主のサインインでの Riot 自身のサーバー: アプリが読むものを 1 回ずつ。"""
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
        # 答えの部分の名前（値は入れない）: ゲームの新しい部分が最初に出る場所。
        # パッチ 13.06 のエージェントのマスタリーレベルのように、公開の資料にまだ載っていないもの
        _riot(
            "contracts",
            riot.contracts,
            lambda found: _item("contracts", "ok", "read", "parts=" + ",".join(sorted(found["contracts"])[:14])),
        ),
        _riot("agents", lambda: {"agents": riot.agents()}, lambda found: counted("agents", len(found["agents"]))),
        _riot(
            "history",
            lambda: riot.history(region, puuid),
            lambda found: counted("history", len(found.get("matches") or [])),
        ),
        _riot("rr", lambda: riot.rr_updates(region, puuid), lambda found: counted("rr", int(found.get("count") or 0))),
    ]


def other_items(app, lang: str, ask_site) -> list[dict]:
    """名前と絵、HenrikDev、新しいビルドを知らせるサイト。"""
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


def run(app, far: bool, lang: str = "ja") -> list[dict]:
    """ページのグループ: ``[{"key", "name", "items": [...]}]``。
    ``far``: Riot のサーバーとほかの出どころも確かめる（でなければ省く）。"""
    groups = [("local", local_items(app.config["VAL_LOCAL"]))]
    if far:
        groups += [("riot", riot_items(app.config["VAL_RIOT"])), ("others", other_items(app, lang, _site))]
    groups.append(("app", app_items(app)))
    return [{"key": key, "name": GROUPS[key], "items": items} for key, items in groups]


def report(groups: list[dict], build: dict | None) -> str:
    """貼り付け用の素のテキストのチェックアップ: キー、状態、見つけたもの。個人的なものは入れない。"""
    lines = [
        "VAL Tracker checkup",
        f"build: {build['date']} {build['commit'][:7]}" if build else "build: source",
        f"system: {platform.system()} {platform.release()} {platform.version()}"[:80],
    ]
    for group in groups:
        lines.append(f"[{group['key']}]")
        lines.extend(f"{item['key']}: {item['state']} {item['code']}".rstrip() for item in group["items"])
    return "\n".join(lines)


#: 翻訳のテスト用の、このページの全文。
TEXTS = (*GROUPS.values(), *NAMES.values(), *SAID.values(), *SHAPES.values(), *WHERE.values(), *friends.WORDS.values())
