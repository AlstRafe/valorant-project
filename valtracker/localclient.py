"""デスクトップアプリ（desktop.py）用の、この PC の Riot Client: ゲーム自身のサインインとゲームの状態を、
数秒ごとに Riot のサーバーに聞く代わりにこの PC で読む。

Riot Client は 127.0.0.1 で答える（ポートとパスワードは動いている間だけある lockfile の中）
: クライアント自身のウィンドウが使うローカル API。そこから取れるもの:

- クライアントにサインインしているアカウントのトークン（設定ページに何も貼らなくてよい）
- そのアカウントの Riot ID と、VALORANT の起動情報からシャード、パーティーサーバー、クライアントのバージョン
- websocket 1 本でのイベント: 持ち主のプレゼンス（メニュー、マッチング中、試合成立、エージェント選択、試合）、
  VALORANT の起動・終了、パーティー・エージェント選択・試合についての Riot のプッシュ。
  来た瞬間に /live を読み直し（``wait``）、何も起きていない間は何も聞かない
- 持ち主のフレンドとそれぞれの居場所。クライアント自身のウィンドウと同じもの（``friends``）
- チェックアップのページ用に、これらがそれぞれ読めたか（``examine``: ステータスと形だけ、個人的なものは無し）

ここでは VALORANT 自体には触らない: プロセスやメモリを読み書きせず、上に何も描かず、キーも押さない。
ログは 1 回だけ読む（起動情報にサーバー名が無いときだけ）。
どの項目も無くてよいものとして読む: これはクライアント自身の API で、Riot は予告なく変えられる。"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import ssl
import threading
import time
from pathlib import Path

from valtracker.util import UUID, as_dict, as_list, to_float, token_claims

_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
#: Riot Client が動いている間に書くもの: ``name:pid:port:password:protocol``。
LOCKFILE = _HOME / "Riot Games" / "Riot Client" / "Config" / "lockfile"
#: VALORANT のログ: パーティーサーバーのアドレス（glz-<region>-1.<shard>.a.pvp.net）が最初の方の行にある。
LOG = _HOME / "VALORANT" / "Saved" / "Logs" / "ShooterGame.log"
#: 聞くイベント: 持ち主のプレゼンス、クライアントが開いているゲーム、Riot のプッシュ。
PRESENCES = "OnJsonApiEvent_chat_v4_presences"
SESSIONS = "OnJsonApiEvent_product-session_v1_external-sessions"
MESSAGES = "OnJsonApiEvent_riot-messaging-service_v1_message"
EVENTS = (PRESENCES, SESSIONS, MESSAGES)
#: /live の表示を変える Riot のプッシュ（パーティー、エージェント選択、試合）。
PUSHED = ("ares-parties", "ares-pregame", "ares-core-game")
#: Riot Client が閉じている間に lockfile を見る間隔の秒数（小さなファイルを 1 回読むだけ）。
CLOSED_WAIT = 5.0
#: websocket が閉じたあと（クライアントの再起動）、つなぎ直すまでの秒数。
RETRY_WAIT = 3.0
#: 静かな websocket にまだいるか聞く間隔（秒）。そのときプレゼンスも読み直す（取りこぼしたイベントもこの間隔で拾える）。
QUIET = 30.0
#: イベントが無い（websocket が無い）とき、VALORANT の起動情報（シャード、バージョン）を読み直すまでの秒数。
LAUNCH_KEEP = 60.0
#: 次の変化を同時に待てるページの数（それぞれサーバーのスレッドを 1 つ持つ）: 古いものから手放す。
WAITING = 3
#: トークンが切れるこの秒数前に取り直す。
EARLY = 300
#: サーバー名のために読む VALORANT のログの量（アドレスは最初の数 kB にある）。
LOG_BYTES = 2 * 1024 * 1024
#: フレンドリストを持つ秒数: 開いている間、ページは数秒ごとに聞き直す。
FRIENDS_KEEP = 5.0
#: 試合のパーティー（プレゼンスの partyId）を持つ秒数: パーティーは試合中に変わらないので、
#: 同じ試合の全員のものがあれば、エージェント選択（2 秒ごとの確認）でもクライアントに聞くのはこの間隔で 1 回。
PARTIES_KEEP = 20.0
#: 同じ試合にまだパーティーのわからない人がいる間に、プレゼンス全部を読み直す間隔（秒）。相手チームのプレゼンスは
#: 対戦が始まってから（ロード画面で）届くので、20 秒待つと試合中に出るのが遅れた。読むのはこの PC の Riot Client
#: の小さな答えだけ（Riot のサーバーにもゲームにも触らない）。
PARTIES_RETRY = 2.0
#: 同じ試合の人のパーティーがイベントで届いたとき、/live に読み直させる最短の間隔（秒）。10 人分が続けて届いても、
#: ページ（と GLZ の試合の読み込み）はこの間隔に 1 回まで。間に届いたものは間隔のあとにまとめて 1 回。
PARTY_NEWS_GAP = 2.0
#: 通知先（``listen``）に知らせる持ち主のゲームの瞬間: 試合成立（ゲームの 5 秒カウント）、
#: それを取りこぼしたらエージェント選択。
TOLD = ("found", "pregame")
_GLZ = re.compile(rb"https://glz-([a-z]+)-1\.([a-z]+)\.a\.pvp\.net")
_SHARDS = ("ap", "kr", "eu", "na", "pbe")


class LocalError(Exception):
    """Riot Client が答えなかった（閉じている、起動中、断った）。"""


def read_lockfile(path: Path) -> tuple[int, str] | None:
    """動いている Riot Client の (port, password)。動いていなければ None。"""
    try:
        parts = Path(path).read_text(encoding="utf-8", errors="replace").strip().split(":")
    except OSError:
        return None
    if len(parts) < 5 or not parts[2].isdigit() or not parts[3]:
        return None
    return int(parts[2]), parts[3]


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()


def _local_tls() -> ssl.SSLContext:
    """Riot Client 自身の証明書（127.0.0.1 用に作った Riot 独自のルート）
    は Windows が知らないもの: このアドレス（この PC 以外は答えられない）に限って受け入れる。"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def ask(door: tuple[int, str], path: str) -> tuple[int, object]:
    """Riot Client への GET ``path``: （ステータス, JSON か None）。ステータス 0: 答え無し。"""
    port, password = door
    connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=3, context=_local_tls())
    try:
        connection.request("GET", path, headers={"Authorization": _basic(password), "Accept": "application/json"})
        answer = connection.getresponse()
        body = answer.read(4 * 1024 * 1024)
        try:
            return answer.status, json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            return answer.status, None
    except (OSError, http.client.HTTPException):
        return 0, None
    finally:
        connection.close()


class _Socket:
    """LocalClient が使う形の Riot Client の websocket（websocket-client）: ``send``、
    ``recv``（QUIET 秒何も来なければ None、閉じたら ConnectionError）、``close``。"""

    def __init__(self, door: tuple[int, str]):
        import websocket  # デスクトップアプリ専用の依存（requirements-desktop.txt）

        self._closed = (websocket.WebSocketConnectionClosedException,)
        self._quiet = websocket.WebSocketTimeoutException
        port, password = door
        self.socket = websocket.create_connection(
            f"wss://127.0.0.1:{port}/",
            header=[f"Authorization: {_basic(password)}"],
            sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
            http_no_proxy=["127.0.0.1", "localhost"],  # PC が指定するプロキシは決して通さない
            timeout=QUIET,
            enable_multithread=True,
        )

    def send(self, text: str) -> None:
        self.socket.send(text)

    def recv(self) -> str | None:
        try:
            message = self.socket.recv()
        except self._quiet:
            return None
        except self._closed as error:
            raise ConnectionError("closed") from error
        if message is None:
            raise ConnectionError("closed")
        return message.decode("utf-8", "replace") if isinstance(message, bytes) else message

    def ping(self) -> None:
        self.socket.ping()

    def close(self) -> None:
        try:
            self.socket.close()
        except Exception:  # 壊れたソケットを閉じる
            pass


def decode_private(value) -> dict:
    """プレゼンスの ``private`` の部分: JSON オブジェクトの base64（またはオブジェクトそのもの）。"""
    if isinstance(value, dict):
        return value
    text = str(value or "")
    for decode in (lambda: base64.b64decode(text + "=" * (-len(text) % 4)), text.encode):
        try:
            found = json.loads(decode())
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(found, dict):
            return found
    return {}


def private_of(item: dict) -> dict:
    """プレゼンスの VALORANT の中身: ``private``。それが空なら ``privateJwt``（JWT の中身。その中に private
    があればそれ）: クライアントは予告なく形を変える（ほかのトラッカーも空の private に備えた）。無ければ {}。"""
    data = decode_private(item.get("private"))
    if data:
        return data
    claims = token_claims(str(item.get("privateJwt") or ""))
    inner = claims.get("private")
    return decode_private(inner) if inner else claims


def presence_parties(presences) -> dict[str, str]:
    """プレゼンスの一覧の VALORANT のパーティー: ``{puuid: パーティーの ID}``。ほかのゲームのものと読めないものは
    入れない（product の無いものは VALORANT とみなす: 試合の人のものに付かない形があっても拾う）。名前は読まない。"""
    found = {}
    for item in as_list(presences):
        if not isinstance(item, dict) or str(item.get("product") or "valorant").lower() != "valorant":
            continue
        puuid, party = str(item.get("puuid") or "").lower(), where(item)["party"]
        if UUID.fullmatch(puuid) and party:
            found[puuid] = party
    return found


def where(item: dict) -> dict:
    """持ち主のプレゼンス
    -> ``{"loop": MENUS / PREGAME / INGAME か "", "party": パーティーの ID, "party_state": MATCHMAKING や MATCHMADE_GAME_STARTING など,
    "queue"}``。ゲームが使ってきた両方の形を読む: 全部が一番上（2024 年より前）、
    または matchPresenceData / partyPresenceData の中。"""
    data = private_of(item)
    match = data.get("matchPresenceData") if isinstance(data.get("matchPresenceData"), dict) else data
    party = data.get("partyPresenceData") if isinstance(data.get("partyPresenceData"), dict) else data
    party_id = str(party.get("partyId") or data.get("partyId") or "").lower()
    return {
        "loop": str(match.get("sessionLoopState") or data.get("sessionLoopState") or "").upper(),
        "party": party_id if UUID.fullmatch(party_id) else "",
        "party_state": str(party.get("partyState") or data.get("partyState") or "").upper(),
        "queue": str(match.get("queueId") or data.get("queueId") or "").lower(),
    }


def moment(seen: dict | None) -> str:
    """プレゼンス（``where``）が示す持ち主のゲームの状態: "found"（試合成立、エージェント選択の前）、"pregame"、
    "ingame"、"queue"（マッチング中）、"menus"、""（不明）。"""
    if not seen:
        return ""
    if seen["loop"] == "PREGAME":
        return "pregame"
    if seen["loop"] == "INGAME":
        return "ingame"
    if seen["party_state"] == "MATCHMADE_GAME_STARTING":
        return "found"
    if seen["party_state"] == "MATCHMAKING":
        return "queue"
    return "menus" if seen["loop"] == "MENUS" else ""


def launched(sessions) -> dict:
    """クライアントが開いているゲーム（product-session の外部セッション）
    -> ``{"running": VALORANT が開いているか, "shard", "version": クライアントのバージョン（release-...）
    }``（シャードは起動引数から）。"""
    for session in (sessions or {}).values() if isinstance(sessions, dict) else []:
        if not isinstance(session, dict) or str(session.get("productId") or "").lower() != "valorant":
            continue
        config = as_dict(session.get("launchConfiguration"))
        arguments = " ".join(str(item) for item in as_list(config.get("arguments")) if isinstance(item, str))
        found = re.search(r"-ares-deployment=([a-z]+)", arguments) or re.search(
            r"shared\.([a-z]+)\.a\.pvp\.net", arguments
        )
        version = str(session.get("version") or "")
        return {
            "running": True,
            "shard": found.group(1) if found and found.group(1) in _SHARDS else "",
            "version": version if version.startswith("release-") else "",
        }
    return {"running": False, "shard": "", "version": ""}


def logged_server(path: Path) -> tuple[str, str] | None:
    """VALORANT のログにあるパーティーサーバーの (region, shard)。無ければ None。
    ゲームのためにログは開いたままにして読む。"""
    try:
        with open(path, "rb") as log:
            found = _GLZ.search(log.read(LOG_BYTES))
    except OSError:
        return None
    return (found.group(1).decode(), found.group(2).decode()) if found else None


class LocalClient:
    """この PC の Riot Client。``start`` でイベントを聞き始める（スレッド。何も起きていない間は眠る）。
    ``signed_in`` は riot.Riot にトークンを渡す。``presence`` と ``version`` / ``wait`` は /live
    にゲームの状態と変わった時を伝える。``ask`` と ``connect`` はテスト用。"""

    def __init__(self, lockfile: Path = LOCKFILE, log: Path = LOG, ask=ask, connect=_Socket, clock=time.time):
        self.lockfile, self.log = Path(lockfile), Path(log)
        self.ask, self.connect, self.clock = ask, connect, clock
        self._lock = threading.Lock()
        self._changed = threading.Condition()
        #: /live がすぐ出すべき変化のたびに 1 つ増える。
        self.version = 0
        #: それを待っているページ（``wait``）: その番号（古い順）と、待つのをやめるよう言われたもの。
        self._tickets = 0
        self._waiting: list[int] = []
        self._let_go: set[int] = set()
        self._tokens: dict | None = None
        self._account: dict | None = None
        #: VALORANT の起動情報（{"running", "shard", "version"}）。変わったとクライアントが言ったときに読む。
        self._game: dict | None = None
        self._server: tuple[str, str] | None = None
        #: 聞いていないとき（イベント無し）に読んだ起動情報: （時刻, 起動情報）。LAUNCH_KEEP 過ぎたら聞き直す。
        self._launch_read: tuple[float, dict] | None = None
        #: VALORANT が開いている間の持ち主のプレゼンス（where）。
        self._seen: dict | None = None
        self._listening = False
        #: アプリ起動からのイベントを種類ごとに数えたもの（チェックアップ: クライアントのイベントは来ているか）。
        self.heard = {"presences": 0, "sessions": 0, "pushes": 0}
        #: 持ち主の試合が成立したら知らせる先（``listen``）: moment -> None。どれも聞いているスレッドで呼ぶ。
        self._listeners: list = []
        #: 最後に読んだフレンド: （時刻, 扉, {"friends", "presences"}）。
        self._friends: tuple[float, tuple[int, str], dict] | None = None
        #: わかっているプレゼンスのパーティー {puuid: パーティーの ID}: 全部を読んだもの（``parties``）に、
        #: そのあとイベントで届いたものを足す。
        self._known: dict[str, str] = {}
        #: 最後にプレゼンス全部を読んだ（時刻, 扉）。
        self._known_read: tuple[float, tuple[int, str]] | None = None
        #: 持ち主の今の試合のプレイヤー（riot.Riot が ``parties`` に渡す）: その人のパーティーが届いたら /live に知らせる。
        self._wanted: frozenset[str] = frozenset()
        #: パーティーの知らせ（PARTY_NEWS_GAP）: 最後に知らせた時刻と、あとでまとめて知らせるタイマー。
        self._news_at = 0.0
        self._news_timer: threading.Timer | None = None
        #: チェックアップ用: 直近の試合の段階（"pregame"、"ingame"）ごとの、プレイヤーの数とパーティーのわかった人数。
        self.match_parties: dict[str, dict[str, int]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- riot.Riot とページが使うもの ------------------------------------------------------------------

    def signed_in(self, renew: bool = False) -> tuple[dict, dict] | None:
        """Riot Client にサインインしているアカウントの (tokens, account)。
        閉じているか誰もサインインしていなければ None。
        tokens: riot.Riot の ``{"access", "entitlements", "until", "version"}``、
        account: ``{"puuid", "name", "tag", "shard", "region"}``。
        クライアントには 1 時間に 1 回聞く（``renew``＝トークンを断られたときも）。"""
        door = read_lockfile(self.lockfile)
        if door is None:
            with self._lock:
                self._tokens = self._account = None
            return None
        with self._lock:
            tokens = self._tokens
            if renew or tokens is None or tokens["door"] != door or tokens["until"] <= self.clock():
                tokens = self._read_tokens(door)
                if tokens is None:
                    return None
            if self._account is None or self._account["puuid"] != tokens["puuid"] or self._account["door"] != door:
                self._account = self._read_account(door, tokens["puuid"])
            game = self._launch(door)
            server = self._server_name(game)
            account = {
                "puuid": tokens["puuid"],
                "name": self._account["name"],
                "tag": self._account["tag"],
                "shard": server[1],
                "region": server[0],
            }
            given = {key: tokens[key] for key in ("access", "entitlements", "until")}
            return given | {"version": game.get("version") or ""}, account

    def presence(self) -> dict | None:
        """クライアントのイベントからのゲームの状態: ``{"running": False}``（VALORANT は閉じている）、
        ``{"running": True, "loop", "party", "party_state", "queue"}``、または None（不明: 聞いていない、
        またはゲームがまだ起動中。そのときはアプリが無いときと同じくパーティーサーバーに聞く）。"""
        with self._lock:
            if not self._listening or self._game is None:
                return None
            if not self._game["running"]:
                return {"running": False}
            if not self._seen or not self._seen["loop"]:
                return None
            return {"running": True, **self._seen}

    def wait(self, seen: int, seconds: float) -> int:
        """``version`` が ``seen`` でなくなるまで（か ``seconds`` 過ぎるまで）待つ: そのときの version を返す。
        同時に待てるのは WAITING まで: 待っている間に離れたページ（サーバーのスレッドはまだ待っている）
        は次のページに譲る。"""
        with self._changed:
            self._tickets += 1
            ticket = self._tickets
            self._waiting.append(ticket)
            if len(self._waiting) > WAITING:
                self._let_go.add(self._waiting.pop(0))
                self._changed.notify_all()
            try:
                self._changed.wait_for(
                    lambda: self.version != seen or ticket in self._let_go, timeout=max(0.0, seconds)
                )
            finally:
                self._let_go.discard(ticket)
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
            return self.version

    def listen(self, told) -> None:
        """持ち主の試合が成立したら ``told(moment)`` を呼ぶ（"found"。
        クライアントが最初に言ったのがエージェント選択なら "pregame"）。 1 試合 1 回、
        聞いているスレッドから呼ぶので待ってはいけない。"""
        self._listeners.append(told)

    def friends(self) -> dict | None:
        """持ち主のフレンドと、クライアントが持つプレゼンス（フレンドと持ち主のもの）
        をクライアントが出すまま: ``{"friends": [...], "presences": [...], "me": 持ち主の puuid}``。
        クライアントが閉じているか答えなければ None。ローカルの小さな読み込み 2 回で、FRIENDS_KEEP 秒持つ。"""
        door = read_lockfile(self.lockfile)
        if door is None:
            return None
        with self._lock:
            kept = self._friends
            if kept is not None and kept[1] == door and self.clock() - kept[0] < FRIENDS_KEEP:
                return kept[2]
        signed = self.signed_in()  # 持ち主が誰か（保持: クライアントには 1 時間に 1 回聞く）
        me = signed[1]["puuid"] if signed else ""
        try:
            listed, present = self._get(door, "/chat/v4/friends"), self._get(door, "/chat/v4/presences")
        except LocalError:
            return None
        found = {
            "friends": [item for item in as_list(listed.get("friends")) if isinstance(item, dict)]
            if isinstance(listed, dict)
            else [],
            "presences": [item for item in as_list(present.get("presences")) if isinstance(item, dict)]
            if isinstance(present, dict)
            else [],
            "me": me,
        }
        with self._lock:
            self._friends = (self.clock(), door, found)
        return found

    def parties(self, wanted=(), phase: str = "") -> dict[str, str] | None:
        """クライアントが持つ VALORANT のプレゼンスのパーティー: ``{puuid: パーティーの ID}``（private の partyId）。
        試合ではフレンドでない同じ試合のプレイヤーのプレゼンスも来る（エージェント選択では味方、対戦が始まると相手も）
        ので、誰と誰が組んでいるかがわかる（ほかのトラッカーと同じ読み方）。名前は読まない: 名前を隠している人の名前も
        来ることがあるが、使わない。クライアントが閉じているか答えなければ None。

        ``wanted``: 持ち主の今の試合のプレイヤー（puuid）、``phase``: その段階（チェックアップ用）。全員のパーティーが
        わかっていれば PARTIES_KEEP 秒、わからない人がいれば PARTIES_RETRY 秒ごとにローカルで読み直す。その人たちの
        パーティーがイベントで届けば、その時に /live に知らせる（``version``）。"""
        door = read_lockfile(self.lockfile)
        if door is None:
            return None
        wanted = frozenset(wanted)
        with self._lock:
            self._wanted = wanted
            read = self._known_read
            age = self.clock() - read[0] if read is not None and read[1] == door else None
            missing = wanted - self._known.keys()
            if age is not None and (age < PARTIES_RETRY or (age < PARTIES_KEEP and not missing)):
                return self._tally(wanted, phase)
        try:
            present = self._get(door, "/chat/v4/presences")
        except LocalError:
            return None
        found = presence_parties(as_dict(present).get("presences"))
        with self._lock:
            # 読んでいる間にイベントで届いた試合の人のものは残す。ほかの古いものは今のものに置き換える
            self._known = {subject: party for subject, party in self._known.items() if subject in wanted} | found
            self._known_read = (self.clock(), door)
            return self._tally(wanted, phase)

    def _tally(self, wanted: frozenset[str], phase: str) -> dict[str, str]:
        """``parties`` の答え（ロックの中で）。試合の段階ごとに、何人のパーティーがわかったかを覚える
        （チェックアップ: 試合のあとでも、相手のプレゼンスが届いたかがわかる）。"""
        if wanted and phase in ("pregame", "ingame"):
            if phase == "pregame" and "ingame" in self.match_parties:
                self.match_parties = {}  # 次の試合
            self.match_parties[phase] = {"players": len(wanted), "known": len(wanted & self._known.keys())}
        return dict(self._known)

    def _hear_parties(self, presences) -> None:
        """イベントで届いたプレゼンスのパーティーを覚える。今の試合の人のものが新しければ /live に知らせる。"""
        heard = presence_parties(presences)
        if not heard:
            return
        with self._lock:
            news = any(self._known.get(subject) != party for subject, party in heard.items() if subject in self._wanted)
            self._known.update(heard)
        if news:
            self._party_news()

    def _party_news(self) -> None:
        """同じ試合の人のパーティーが届いた: /live に読み直させる。PARTY_NEWS_GAP に 1 回まで: 間に届いたものは、
        間隔のあとに 1 回でまとめて知らせる。"""
        with self._lock:
            if self._news_timer is not None:
                return  # もう知らせる予定がある: そのときに今のものも読まれる
            wait = self._news_at + PARTY_NEWS_GAP - self.clock()
            timer = None
            if wait > 0:
                timer = self._news_timer = threading.Timer(min(wait, PARTY_NEWS_GAP), self._party_news_late)
                timer.daemon = True
            else:
                self._news_at = self.clock()
        if timer is not None:
            timer.start()
        else:
            self._changed_now()

    def _party_news_late(self) -> None:
        with self._lock:
            self._news_timer = None
            self._news_at = self.clock()
        self._changed_now()

    def examine(self) -> dict:
        """チェックアップ用の、今クライアントが答えるもの: 読み込みごとのステータスと答えの形。トークン・名前・
        ID は入れない。クライアントが閉じていれば ``{"lockfile": bool}`` だけ。"""
        door = read_lockfile(self.lockfile)
        found: dict = {"lockfile": door is not None, "log": logged_server(self.log)}
        with self._lock:
            found["listening"], found["heard"] = self._listening, dict(self.heard)
        if door is None:
            return found
        status, value = self.ask(door, "/entitlements/v1/token")
        value = as_dict(value)
        puuid = str(value.get("subject") or "").lower()
        found["token"] = {
            "status": status,
            "access": bool(value.get("accessToken")),
            "entitlements": bool(value.get("token")),
            "subject": bool(UUID.fullmatch(puuid)),
        }
        status, value = self.ask(door, "/chat/v1/session")
        value = as_dict(value)
        found["chat"] = {
            "status": status,
            "name": bool(value.get("game_name")) and bool(value.get("game_tag")),
            "mine": bool(puuid) and str(value.get("puuid") or "").lower() == puuid,
            "state": str(value.get("state") or "")[:24],
        }
        status, value = self.ask(door, "/product-session/v1/external-sessions")
        sessions = as_dict(value)
        products = sorted(
            {str(item.get("productId") or "")[:24] for item in sessions.values() if isinstance(item, dict)}
        )
        found["sessions"] = {"status": status, "products": products[:8], **launched(sessions)}
        status, value = self.ask(door, "/chat/v4/presences")
        presences = value.get("presences") if isinstance(value, dict) else None
        presences = [item for item in presences or [] if isinstance(item, dict)]
        mine = next(
            (
                item
                for item in presences
                if str(item.get("puuid") or "").lower() == puuid
                and str(item.get("product") or "").lower() == "valorant"
            ),
            None,
        )
        private = private_of(mine) if mine else {}
        seen = where(mine) if mine else None
        found["presence"] = {
            "status": status,
            "count": len(presences),
            "mine": mine is not None,
            "shape": "nested" if "matchPresenceData" in private else "flat" if "sessionLoopState" in private else "",
            "keys": sorted(str(key)[:40] for key in private)[:40],
            "loop": seen["loop"][:24] if seen else "",
            "party_state": seen["party_state"][:40] if seen else "",
            "party": bool(seen and seen["party"]),
            "queue": seen["queue"][:40] if seen else "",
        }
        # ほかの人のプレゼンス（フレンドと、試合中は同じ試合の人）: 数だけ。パーティーが読める形で届いているか
        others = [
            item
            for item in presences
            if str(item.get("product") or "valorant").lower() == "valorant"
            and str(item.get("puuid") or "").lower() != puuid
        ]
        with self._lock:
            match = {phase: dict(counts) for phase, counts in self.match_parties.items()}
        found["parties"] = {
            "others": len(others),
            "with_party": sum(bool(where(item)["party"]) for item in others),
            "empty": sum(not item.get("private") for item in others),
            "jwt": sum(bool(item.get("privateJwt")) for item in presences),
            "match": match,
        }
        return found

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._listen, name="riot-client", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- クライアントを読む ----------------------------------------------------------------------------

    def _get(self, door: tuple[int, str], path: str):
        status, value = self.ask(door, path)
        if status != 200:
            raise LocalError(f"{path} {status or 'no answer'}")
        return value

    def _read_tokens(self, door: tuple[int, str]) -> dict | None:
        try:
            found = self._get(door, "/entitlements/v1/token")
        except LocalError:
            return None
        found = as_dict(found)
        access, entitlements = str(found.get("accessToken") or ""), str(found.get("token") or "")
        puuid = str(found.get("subject") or token_claims(access).get("sub") or "").lower()
        if not access or not entitlements or not UUID.fullmatch(puuid):
            return None  # クライアントは開いているが、まだ誰もサインインしていない
        # exp が読めない（数でない）トークンは 1 時間もつとみなす: float() で落ちてサインインが読めなくならないように
        until = (to_float(token_claims(access).get("exp")) or self.clock() + 3600) - EARLY
        self._tokens = {"access": access, "entitlements": entitlements, "until": until, "puuid": puuid, "door": door}
        return self._tokens

    def _read_account(self, door: tuple[int, str], puuid: str) -> dict:
        try:
            found = self._get(door, "/chat/v1/session")
        except LocalError:
            found = {}
        found = as_dict(found)
        mine = str(found.get("puuid") or "").lower() == puuid
        return {
            "puuid": puuid,
            "door": door,
            "name": str(found.get("game_name") or "") if mine else "",
            "tag": str(found.get("game_tag") or "") if mine else "",
        }

    def _read_game(self, door: tuple[int, str]) -> dict:
        try:
            return launched(self._get(door, "/product-session/v1/external-sessions"))
        except LocalError:
            return {"running": False, "shard": "", "version": ""}

    def _launch(self, door: tuple[int, str]) -> dict:
        """VALORANT の起動情報: イベントからのもの（来たときに保持）、無ければ時々読む。"""
        if self._game is not None:
            return self._game
        if self._launch_read is None or self.clock() - self._launch_read[0] > LAUNCH_KEEP:
            self._launch_read = (self.clock(), self._read_game(door))
        return self._launch_read[1]

    def _server_name(self, game: dict) -> tuple[str, str]:
        """パーティーサーバーの (region, shard): 起動情報のシャード、ログのアドレス、
        それも無ければアジア太平洋（持ち主は日本で遊ぶ）。"""
        if self._server is None:
            self._server = logged_server(self.log)
        if self._server and (not game.get("shard") or self._server[1] == game["shard"]):
            return self._server
        shard = game.get("shard") or "ap"
        return shard, shard

    def _read_presence(self, door: tuple[int, str], puuid: str) -> dict | None:
        try:
            found = self._get(door, "/chat/v4/presences")
        except LocalError:
            return None
        return self._mine(found.get("presences") if isinstance(found, dict) else None, puuid)

    @staticmethod
    def _mine(presences, puuid: str) -> dict | None:
        for item in presences or []:
            if (
                isinstance(item, dict)
                and str(item.get("puuid") or "").lower() == puuid
                and str(item.get("product") or "").lower() == "valorant"
            ):
                return where(item)
        return None

    # -- イベント ------------------------------------------------------------------------------------

    def _changed_now(self) -> None:
        with self._changed:
            self.version += 1
            self._changed.notify_all()

    def _tell(self, was: dict | None, seen: dict | None) -> None:
        """試合成立を通知先に知らせる: プレゼンスがメニューかマッチング中から、
        試合成立かエージェント選択に変わったとき（その 2 つの間の変化と、
        アプリがゲームの状態を今知っただけのときは除く）。"""
        before, now = moment(was), moment(seen)
        if now not in TOLD or before not in ("menus", "queue"):
            return
        for told in self._listeners:
            try:
                told(now)
            except Exception:  # 通知先自身の問題で待ち受けを終わらせない
                continue

    def _look_again(self, door: tuple[int, str]) -> None:
        """ゲームの起動情報と持ち主のプレゼンスを読む（つないだときと、時々）。"""
        game = self._read_game(door)
        found = self.signed_in()
        seen = self._read_presence(door, found[1]["puuid"]) if found and game["running"] else None
        with self._lock:
            changed = (game, seen) != (self._game, self._seen)
            if game.get("shard") and self._game and game["shard"] != self._game.get("shard"):
                self._server = None
            was = self._seen
            self._game, self._seen = game, seen
        if changed:
            self._changed_now()
            self._tell(was, seen)

    def handle(self, door: tuple[int, str], text: str) -> None:
        """websocket のメッセージ 1 つ: ``[8, event, {"data", "eventType", "uri"}]``。"""
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, list) or len(message) < 3 or message[0] != 8 or not isinstance(message[2], dict):
            return
        event, payload = message[1], message[2]
        data = as_dict(payload.get("data"))
        if event == PRESENCES:
            with self._lock:
                puuid = (self._tokens or {}).get("puuid", "")
            self.heard["presences"] += 1
            self._hear_parties(data.get("presences"))
            seen = self._mine(data.get("presences"), puuid) if puuid else None
            if seen is None:
                with self._lock:
                    self._friends = None  # フレンドのプレゼンス: フレンドページは一覧を読み直す
                return
            with self._lock:
                changed = seen != self._seen
                was = self._seen
                self._seen = seen
                if seen["loop"] == "MENUS":
                    self._wanted = frozenset()  # 試合が終わった: その人たちのパーティーはもう知らせない
            if changed:
                self._changed_now()
                self._tell(was, seen)
        elif event == SESSIONS:
            self.heard["sessions"] += 1
            game = self._read_game(door)
            with self._lock:
                changed = game != self._game
                self._game = game
                if not game["running"]:
                    self._seen = None
            if changed:
                self._changed_now()
        elif event == MESSAGES:
            said = f"{payload.get('uri') or ''} {data.get('service') or ''} {data.get('resource') or ''}"
            if any(word in said for word in PUSHED):
                self.heard["pushes"] += 1
                self._changed_now()

    def _listen(self) -> None:
        while not self._stop.is_set():
            door = read_lockfile(self.lockfile)
            if door is None:
                self._forget()
                self._stop.wait(CLOSED_WAIT)
                continue
            try:
                socket = self.connect(door)
            except Exception:  # クライアントが起動中、または websocket のライブラリが無い
                self._stop.wait(RETRY_WAIT)
                continue
            try:
                for event in EVENTS:
                    socket.send(json.dumps([5, event]))
                with self._lock:
                    self._listening = True
                self._look_again(door)
                looked = self.clock()
                while not self._stop.is_set():
                    text = socket.recv()
                    if text is None:  # 静か: まだいるか？（取りこぼしたイベントも拾う）
                        socket.ping()
                        if self.clock() - looked >= QUIET:
                            self._look_again(door)
                            looked = self.clock()
                    elif text:
                        self.handle(door, text)
            except Exception:  # クライアントが閉じたか再起動した: つなぎ直す
                pass
            finally:
                socket.close()
                self._forget()
            self._stop.wait(RETRY_WAIT)

    def _forget(self) -> None:
        with self._lock:
            was = self._listening or self._game is not None
            self._listening, self._game, self._seen, self._server = False, None, None, None
            self._friends, self._known, self._known_read, self._wanted = None, {}, None, frozenset()
        if was:
            self._changed_now()
