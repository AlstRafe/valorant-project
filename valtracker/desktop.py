"""Windows アプリとしての VAL Tracker: ``python -m valtracker.desktop``、
または VALTracker.exe（.github/workflows/desktop.yml が desktop/VALTracker.py からビルド）。

サイト自身のページを、この PC だけ（127.0.0.1、空いているポート）
でアプリ専用のウィンドウ（WebView2: Windows に入っている Edge のエンジン）に出し、
この PC の Riot Client でサインインする（localclient.py）: 何も貼らなくてよく、
クライアントが試合成立を言った瞬間に /live が切り替わる。

VALORANT の邪魔を決してしないように作る:

- ゲームには一切触らない: プロセスやメモリを読み書きせず、上に何も描かず（オーバーレイ無し）、キーもクリックも送らない。
  サイトと同じく、Riot Client のローカル API と Riot のサーバーと話すだけ
- 通常より低い優先度で動くので、プロセッサーはゲームが先に使う（ウィンドウのプロセスも引き継ぐ。
  Chromium が自分で上げるいくつかは戻す）。Windows 11 の効率モードで、メモリも Windows が先に取り戻す
- ウィンドウはグラフィックカードもアニメーションも使わずに描く: GPU はゲームのもの
- 毎秒聞く代わりに Riot Client のイベントを待ち、隠れているか最小化されたウィンドウは何も聞かない
- 自分を前面に出さない（フルスクリーンのゲームのフォーカスを奪う）
  : 持ち主がほかのプログラムにいる間の試合成立はタスクバーのボタンを光らせ、ゲームが前面にあるときは何もしない（pc.py）
- サイトの裏の仕事（混む時間の推定のためにほかのプレイヤーを見る）は止める

ブラウザーに包まれていないウィンドウに必要なもの: ほかのサイトのリンクは既定のブラウザーで開く（app.js）、戻る・進む・
再読み込みはボタンとキー（app.js）、絵やカレンダーのファイルの保存は場所を聞く、
ウィンドウは前に閉じた場所と大きさで開く（window.json）、新しいビルドは全ページで知らせる（起動 1 分後、
そのあと数時間ごとにサイトに聞く: 小さなリクエスト 1 つ）、タイトルバーはページと同じ明暗（follow_look）。

サイトにはできずアプリができること: 持ち主のフレンドとそれぞれの居場所（friends.py）、
ほかのプログラムが前面にあるときの試合成立の通知と、Windows 起動時の最小化での開始（pc.py）、
この PC でアプリが読むもの全部のチェックアップ（checkup.py）、持ち主がボタンを押したときの自動更新（update.py）。"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import http.client
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from valtracker import NAME, create_app, pc, release, update
from valtracker.localclient import LocalClient, moment

#: ウィンドウのエンジンに伝えること: グラフィックカード無し、
#: アニメーション無し（サイトの prefers-reduced-motion のルール）。
BROWSER_ARGUMENTS = "--disable-gpu --force-prefers-reduced-motion --disable-features=ElasticOverscroll"
#: ウィンドウの最初の大きさと最小の大きさ（760 px 未満でサイトのスマホ向けレイアウト）。
SIZE, SMALLEST = (1180, 860), (400, 560)
#: 画面でウィンドウの下に空けておく場所: Windows のタスクバー。
TASKBAR = 48
#: ウィンドウが最初に開くページ。
FIRST_PAGE = "/live"
#: サーバーのスレッド: ページ、その絵、クライアントの次のイベントを待つページ。
THREADS = 8
#: アプリが動いている間持つもの: 2 回目の起動はこれを見つけ、2 つ起動せずにそう伝える。
MUTEX = "Local\\VALTracker.Desktop"
#: 同じタイトルのウィンドウのうちアプリのもの: WinForms はウィンドウのクラスにこう名前を付ける。
#: ほかのプログラムのウィンドウが同じタイトルのことがある（エクスプローラーで開いたデータフォルダーも "VAL Tracker"）。
WINDOW_CLASS = "WindowsForms10."
ALREADY_OPEN = "VAL Tracker はすでに起動しています（タスクバーを確認してください）。\nVAL Tracker is already open (see the taskbar)."
IN_BROWSER = "ブラウザーで開きました。OK を押すと VAL Tracker を終了します。"
#: ウィンドウが無い理由（window() の答え。出せたら ""）と、
#: そのとき持ち主に聞くこと: ブラウザーは持ち主がそう言ったときだけ開く（ゲームの横のブラウザーこそアプリが避けたいもの
#: ）。
NO_RUNTIME, NO_PART = "runtime", "part"
NO_WINDOW = {
    NO_RUNTIME: "アプリのウィンドウを開けませんでした。Microsoft Edge WebView2 Runtime が見つかりません。\n"
    "(The app's window could not be opened: Microsoft Edge WebView2 Runtime was not found.)",
    NO_PART: "アプリのウィンドウを開けませんでした。必要な部品を読み込めません。\n"
    "ログ: {log}\n"
    "(The app's window could not be opened: its parts could not be loaded. The reason is in that file.)",
}
IN_BROWSER_INSTEAD = (
    "代わりにブラウザーで開きますか？「いいえ」を選ぶと、何も開かずに終了します。\n"
    "(Open in the browser instead? No: the app ends and opens nothing.)"
)
#: インターネットから来たファイルに Windows が付ける印: ファイル本体の横にある 2 つ目のデータストリーム。
MARK = ":Zone.Identifier"
_held = []  # ミューテックスのハンドル。アプリが動いている間持つ
#: サイトが最新ビルドを答える場所（サイト自身のアドレスの後ろ）。
LATEST = "/app/latest"
#: サイトに聞く時刻: 起動 1 分後（ウィンドウが開いている途中ではない）、そのあと 6 時間ごと。聞けなかったとき、
#: 最新ビルドがまだ作っている途中のときは 30 分後。
LOOK_FIRST, LOOK_EVERY, LOOK_AGAIN = 60.0, 6 * 3600.0, 1800.0


def data_folder() -> Path:
    r"""設定、保存した一覧、絵: %LOCALAPPDATA%\VAL Tracker（VAL_DESKTOP_HOME で別の場所を指定できる）。"""
    named = os.environ.get("VAL_DESKTOP_HOME")
    if named:
        return Path(named)
    home = os.environ.get("LOCALAPPDATA")
    return Path(home) / NAME if home else Path.home() / ".local" / "share" / "val-tracker"


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel


def calm() -> list[str]:
    """このプロセス（と、優先度を引き継ぐウィンドウのプロセス）をゲームに譲らせる: 通常より低い優先度、
    効率モード（Windows 11: 遅いコア、低いクロック）、低いメモリ優先度。設定できたものを返す。"""
    if sys.platform != "win32":
        return []
    kernel, done = _kernel(), []
    me = kernel.GetCurrentProcess()
    if kernel.SetPriorityClass(me, 0x00004000):  # BELOW_NORMAL_PRIORITY_CLASS
        done.append("priority: below normal")

    class Throttling(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

    class Memory(ctypes.Structure):
        _fields_ = [("MemoryPriority", ctypes.c_ulong)]

    # ProcessPowerThrottling (4): PROCESS_POWER_THROTTLING_EXECUTION_SPEED オン = EcoQoS
    throttling = Throttling(1, 1, 1)
    if kernel.SetProcessInformation(me, 4, ctypes.byref(throttling), ctypes.sizeof(throttling)):
        done.append("efficiency mode")
    # ProcessMemoryPriority (0): MEMORY_PRIORITY_BELOW_NORMAL (4)
    memory = Memory(4)
    if kernel.SetProcessInformation(me, 0, ctypes.byref(memory), ctypes.sizeof(memory)):
        done.append("memory priority: below normal")
    return done


#: 通常より低い以下の優先度クラス（BELOW_NORMAL、IDLE）: そのままにする。
_LOW = (0x00004000, 0x00000040)
#: ウィンドウのプロセスを通常より低い優先度に戻す時刻（ウィンドウが開いてからの秒）: 起動したとき、
#: そのあと KEEP_LOW 秒ごと。
LOWER_AT, KEEP_LOW = (2, 5, 15), 30


class _Entry(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def descendants(pairs: list[tuple[int, int]], root: int) -> set[int]:
    """(プロセス, 親) の組から、``root`` が（何段下でも）起動したプロセス。"""
    found, grew = set(), True
    while grew:
        more = {child for child, parent in pairs if (parent == root or parent in found) and child not in found}
        more.discard(root)
        found |= more
        grew = bool(more)
    return found


def lower_window() -> int:
    """ウィンドウのプロセス（どれもこのアプリが起動した WebView2 のもの）を通常より低い優先度に戻す: 引き継ぐはずだが、
    Chromium がいくつかを自分で上げる（GPU プロセスは通常より高い）。下げた数を返す（Windows だけ）。"""
    if sys.platform != "win32":
        return 0
    kernel, pairs = _kernel(), []
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return 0
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    lowered = 0
    for pid in descendants(pairs, os.getpid()):
        handle = kernel.OpenProcess(0x0200 | 0x1000, False, pid)  # SET_INFORMATION、QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            if kernel.GetPriorityClass(handle) not in _LOW and kernel.SetPriorityClass(handle, 0x00004000):
                lowered += 1
        finally:
            kernel.CloseHandle(handle)
    return lowered


def keep_window_low(stop: threading.Event) -> None:
    """ウィンドウのプロセスが起動したら lower_window を呼び、そのあと KEEP_LOW 秒ごと（新しいレンダラーや、
    Chromium がまた上げたもの）、``stop`` まで。1 回にプロセスの小さな一覧 1 つ。"""

    def run():
        started = time.monotonic()
        for at in LOWER_AT:
            if stop.wait(max(0.0, at - (time.monotonic() - started))):
                return
            lowered = lower_window()
            if lowered:
                print(f"{NAME}: {lowered} window process(es) put below normal", flush=True)
        while not stop.wait(KEEP_LOW):
            lower_window()

    if sys.platform == "win32":
        threading.Thread(target=run, name="keep-low", daemon=True).start()


def first() -> bool:
    """ほかの VAL Tracker アプリが動いていないか（Windows。ほかの OS ではいつも True）。"""
    if sys.platform != "win32":
        return True
    kernel = _kernel()
    handle = kernel.CreateMutexW(None, False, MUTEX)
    _held.append(handle)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def is_app_window(title: str, kind: str) -> bool:
    """このタイトルとウィンドウクラスのウィンドウがアプリのもの（WINDOW_CLASS）か。"""
    return title == NAME and kind.startswith(WINDOW_CLASS)


def show_running() -> bool:
    """すでに動いている VAL Tracker のウィンドウを、最小化されていれば戻して前面に出す:
    持ち主がアプリをもう一度開いて頼んだ（アプリが前面に出る唯一の場合）。見つからない（まだ起動中）
    か Windows でなければ False。"""
    if sys.platform != "win32":
        return False
    user = ctypes.WinDLL("user32", use_last_error=True)
    each = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user.EnumWindows.argtypes = [each, ctypes.c_void_p]
    user.GetWindowTextW.argtypes = user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.IsIconic.argtypes = user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    found = []

    def look(handle, _):
        title, kind = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        user.GetWindowTextW(handle, title, 64)
        user.GetClassNameW(handle, kind, 128)
        if is_app_window(title.value, kind.value):
            found.append(handle)
        return not found  # 最初の 1 つで止める

    user.EnumWindows(each(look), None)
    if not found:
        return False
    if user.IsIconic(found[0]):
        user.ShowWindow(found[0], 9)  # SW_RESTORE
    user.SetForegroundWindow(found[0])
    return True


def tell(words: str) -> None:
    """メッセージボックス（Windows）。ほかではコンソールに 1 行。"""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, words, NAME, 0x40)  # MB_ICONINFORMATION
    else:
        print(words, flush=True)


def ask(words: str) -> bool:
    """はい・いいえのメッセージボックス（Windows。Enter で押されるのはいいえ）: はいが押されたか。ほかでは文を出力して、
    答えははい。"""
    if sys.platform != "win32":
        print(words, flush=True)
        return True
    # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2、IDYES
    return ctypes.windll.user32.MessageBoxW(None, words, NAME, 0x4 | 0x30 | 0x100) == 6


def in_browser(url: str, why: str, log: Path, built: bool | None = None) -> bool:
    """頼まれたから（``why`` が ""）、またはウィンドウが無いから（``why``: window() の答え）、
    ``url`` を既定のブラウザーで開く: 開いたか。ビルドしたアプリは勝手にブラウザーを開かない（ゲームの横のブラウザーこそ
    アプリが避けたいもの）: ウィンドウが無い理由を伝え、先に聞く。"""
    built = bool(getattr(sys, "frozen", False)) if built is None else built
    if built and why and not ask(f"{NO_WINDOW[why].format(log=log)}\n\n{IN_BROWSER_INSTEAD}"):
        return False
    webbrowser.open(url)
    return True


def unmark(home: Path | None, remove=os.remove) -> int:
    """アプリ自身のファイルから Windows の「インターネットから来たファイル」の印を外す: 印があった数を返す。

    ブラウザーはダウンロードした zip に印を付け、エクスプローラーは展開したすべてのファイルに印を引き継ぐ。
    .NET は印の付いたライブラリを読まない（"Operation is not supported"、0x80131515）ので、ウィンドウの部分（pythonnet、
     WebView2 のライブラリ）が起動できず、アプリは代わりにブラウザーで開いていた。
    印はファイルのデータの横の 2 つ目のストリームで、消すのはファイルのプロパティの「許可する」と同じこと。
    見るのはアプリ自身のフォルダーだけ（``home``: update.running_home()、ソースなら None）。
    変えられないファイルはそのままにする。"""
    if home is None:
        return 0
    count = 0
    for path in home.rglob("*"):
        try:
            remove(f"{path}{MARK}")
            count += 1
        except OSError:  # このファイルには印が無い（ふつうはこれ）、またはフォルダーに書けない
            continue
    return count


class OnlyHere:
    """この PC 自身のアドレス宛てのリクエストだけに答える: 名前を 127.0.0.1 に向けた Web ページ（DNS リバインディング）
    は持ち主のページから何も得られない。"""

    def __init__(self, app, hosts: set[str]):
        self.app, self.hosts = app, hosts

    def __call__(self, environ, start_response):
        if environ.get("HTTP_HOST", "") not in self.hosts:
            start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"Forbidden"]
        return self.app(environ, start_response)


def serve(app, port: int = 0):
    """サーバーをスレッドで起動する: （サーバー, アドレス）。"""
    from waitress.server import create_server

    hosts: set[str] = set()
    app.wsgi_app = OnlyHere(app.wsgi_app, hosts)
    server = create_server(app, host="127.0.0.1", port=port, threads=THREADS, ident=NAME)
    port = server.effective_port
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=server.run, name="server", daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def light_windows() -> bool:
    """Windows がアプリをライトに設定しているか（ページが描く前のウィンドウの色）。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except (ImportError, OSError):
        return False


def read_build() -> dict | None:
    """ビルドしたアプリのコミットと作った時刻（desktop.yml が valtracker/BUILD に書く）。
    ソースから動かしているなら None。"""
    return release.read_build(Path(__file__).with_name("BUILD"))


def _latest(site: str) -> object:
    """サイトが答える最新ビルド（release.py）。"""
    if not site.startswith("https://"):
        raise ValueError("not a site to ask")
    request = urllib.request.Request(site + LATEST, headers={"User-Agent": "VALTracker-desktop"})
    with urllib.request.urlopen(request, timeout=15) as answer:  # https（上で確かめた）
        return json.loads(answer.read(4096))


def look_for_updates(app, stop: threading.Event, read=_latest) -> None:
    """アプリが動いている間、時々（LOOK_FIRST、 LOOK_EVERY）
    最新ビルドをサイトに聞く: 新しいものは app.config["VAL_UPDATE"] に入れる（全ページでそう出る）。
    ビルドしたアプリでなければ（ソースから）何もしない。"""
    build = app.config.get("VAL_BUILD")
    if not build:
        return

    def run():
        wait = LOOK_FIRST
        while not stop.wait(wait):
            wait = LOOK_EVERY
            try:
                found = release.newer(build, read(app.config["VAL_PUBLIC"]), time.time())
            except (OSError, ValueError, http.client.HTTPException):
                # HTTPException も: 途中で切れた答えでこのスレッドが止まると、もう二度と確かめない
                wait = LOOK_AGAIN  # ネット無し、またはサイトが落ちている: 30 分後に
                continue
            if found and not found["ready"]:
                wait = LOOK_AGAIN  # 変更から数分しか経っていない: そのビルドはまだ作っている途中
                continue
            app.config["VAL_UPDATE"] = found
            app.config["VAL_UPDATE_LOOKED"] = time.time()

    threading.Thread(target=run, name="updates", daemon=True).start()


#: ウィンドウを閉じた場所: データフォルダーの window.json。
PLACE = "window.json"


def placed(folder: Path, screens: list[tuple[int, int, int, int]]) -> dict:
    """前に閉じた場所からの create_window の大きさと位置: 大きさ（最小以上、画面以下）、
    位置は左上が今の ``screens``（x, y, 幅, 高さ）のどれかにあるときだけ、最大化していたか。"""
    try:
        saved = json.loads((folder / PLACE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}

    def number(key, low, high, default):
        value = saved.get(key)
        return int(value) if isinstance(value, int | float) and low <= value <= high else default

    found = {
        "width": number("width", SMALLEST[0], 10000, SIZE[0]),
        "height": number("height", SMALLEST[1], 10000, SIZE[1]),
        "maximized": saved.get("maximized") is True,
    }
    x, y = number("x", -20000, 20000, None), number("y", -20000, 20000, None)
    screen = screens[0] if screens else None  # メイン画面（位置の無いウィンドウはここに開く）
    if x is not None and y is not None:
        # タイトルバーの左端は画面の上にないといけない（その後外した画面なら真ん中に開く）
        for left, top, wide, high in screens:
            if left <= x + 40 < left + wide and top <= y + 10 < top + high:
                found |= {"x": x, "y": y}
                screen = (left, top, wide, high)
                break
    if screen:
        # 画面より大きくしない（小さい画面や拡大表示: 1366 x 768、1920 x 1080 で 150 %）。下にタスクバーの場所を空ける
        found["width"] = max(SMALLEST[0], min(found["width"], screen[2]))
        found["height"] = max(SMALLEST[1], min(found["height"], screen[3] - TASKBAR))
    return found


def left(form) -> dict | None:
    """閉じるときのウィンドウ（WinForms のフォーム）の状態: 通常の大きさと位置（最大化・
    最小化しているなら戻ったときのもの）と、最大化しているか。読めなければ None。"""
    try:
        scale = float(
            getattr(form, "_scale", 1.0) or 1.0
        )  # pywebview のもの: ウィンドウの 1 ピクセルあたりの画面のピクセル
        shape = str(form.WindowState).rsplit(".", 1)[-1]
        bounds = form.Bounds if shape == "Normal" else form.RestoreBounds
        found = {
            "x": round(bounds.X / scale),
            "y": round(bounds.Y / scale),
            "width": round(bounds.Width / scale),
            "height": round(bounds.Height / scale),
            "maximized": shape == "Maximized",
        }
    except Exception:  # WinForms でない（別のプラットフォーム）、またはフォームがもう無い
        return None
    return found if found["width"] >= SMALLEST[0] and found["height"] >= SMALLEST[1] else None


def remember(window, folder: Path) -> None:
    """閉じるときにウィンドウの場所を書く（window.json）: 次回はそこに開く（placed）。"""

    def closing():
        found = left(getattr(window, "native", None))
        if found is not None:
            with contextlib.suppress(OSError):
                (folder / PLACE).write_text(json.dumps(found), encoding="utf-8")

    window.events.closing += closing


#: 今ページが暗いか: ヘッダーのボタンでの持ち主の選択（<html data-theme>、theme.py）、無ければ Windows 自身の設定。
IS_DARK = (
    "(function(){var t=document.documentElement.getAttribute('data-theme');"
    "return t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches})()"
)


def title_bar(handle: int, dark: bool) -> bool:
    """ウィンドウのタイトルバーを暗く・明るくする（2020 年以降の Windows 10。古いものは自分のまま）。
    Windows が受け入れたか。"""
    if sys.platform != "win32" or not handle:
        return False
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        def put(what: int, value: int) -> bool:
            number = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(handle, what, ctypes.byref(number), ctypes.sizeof(number)) == 0

        took = put(20, int(dark))  # DWMWA_USE_IMMERSIVE_DARK_MODE
        put(38, 2 if dark else 1)  # DWMWA_SYSTEMBACKDROP_TYPE（pywebview が Windows 自身の見た目で設定するもの）
        return took
    except (OSError, AttributeError):
        return False


def follow_look(window, put=title_bar) -> None:
    """タイトルバーをページごとにページの見た目に合わせる: pywebview は Windows 自身の見た目にするので、
    持ち主がヘッダーのボタンでダーク・ライトを選ぶとページと合わなくなる（白いバーの下に暗いページ）。
    読み込み終わったページに 1 回聞くだけで、その間は何もしない。"""

    def loaded():
        handle = window_handle(window)
        try:
            dark = window.evaluate_js(IS_DARK)
        except Exception:  # ウィンドウが閉じる途中
            return
        if handle and isinstance(dark, bool):
            put(handle, dark)

    window.events.loaded += loaded


def window_handle(shown) -> int:
    """ウィンドウのハンドル（Windows のもの）。無い間は 0（まだウィンドウが無い、または WinForms のものでない）。"""
    try:
        return int(shown.native.Handle.ToInt64())
    except Exception:  # ウィンドウが無い、別のプラットフォーム、または閉じる途中
        return 0


def _screens(webview) -> list[tuple[int, int, int, int]]:
    try:
        return [(screen.x, screen.y, screen.width, screen.height) for screen in webview.screens]
    except Exception:  # 不明: ウィンドウは真ん中に開く
        return []


def window(url: str, folder: Path, close_after: float = 0, minimized: bool = False, made=None) -> str:
    """閉じるまで ``url`` をアプリのウィンドウに出し、"" を返す。
    ウィンドウが出せなければその理由（理由はアプリのログに出力する）: NO_PART（pywebview が無い、
    またはその .NET 部分が起動できない）か NO_RUNTIME（WebView2 の無い Windows）。
    ``minimized``: タスクバーに開く（Windows 起動時の開始）。``made(window)``: ウィンドウができたら知らせる。"""
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", BROWSER_ARGUMENTS)
    try:
        import webview

        if sys.platform == "win32":
            from webview.platforms import winforms

            if winforms.renderer != "edgechromium":  # Internet Explorer のエンジン: ページには新しいものが必要
                print(f"{NAME}: no window: WebView2 Runtime was not found (engine: {winforms.renderer})", flush=True)
                return NO_RUNTIME
    except Exception as error:  # pywebview が無い、またはその .NET 部分が起動できない
        print(f"{NAME}: no window: {type(error).__name__}: {str(error)[:600]}", flush=True)
        return NO_PART
    # ページが保存する絵やカレンダーのファイル: Windows の「名前を付けて保存」（無いとウィンドウが捨ててしまう）
    webview.settings["ALLOW_DOWNLOADS"] = True
    start = placed(folder, _screens(webview))
    shown = webview.create_window(
        NAME,
        url,
        width=start["width"],
        height=start["height"],
        x=start.get("x"),
        y=start.get("y"),
        maximized=start["maximized"] and not minimized,
        minimized=minimized,
        min_size=SMALLEST,
        background_color="#f6f4ef" if light_windows() else "#161513",  # サイトの紙の色
        text_select=True,
    )
    remember(shown, folder)
    follow_look(shown)
    if made is not None:
        made(shown)

    def close_later():
        time.sleep(close_after)
        shown.destroy()

    # cookie（言語、セッション）とページ自身の一覧はアプリのフォルダーに残す
    webview.start(close_later if close_after else None, private_mode=False, storage_path=str(folder / "window"))
    return ""


def check(base: str) -> int:
    """ビルドのテスト（--check）: ページが答え、外のアドレスは断る。全部よければ 0。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    failed = 0
    for path, wanted, host in (
        ("/", 200, None),
        ("/live", 200, None),
        ("/live/state", 200, None),
        ("/live/next?after=-1", 200, None),
        ("/settings", 200, None),
        ("/friends", 200, None),
        ("/checkup", 200, None),
        ("/tools", 200, None),
        ("/", 403, "rebound.example"),
    ):
        request = urllib.request.Request(base + path, headers={"Host": host} if host else {})
        started = time.perf_counter()
        try:
            with opener.open(request, timeout=60) as answer:
                status, body = answer.status, answer.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, b""
        except OSError as error:
            status, body = 0, str(error).encode()
        took = time.perf_counter() - started
        ok = status == wanted
        failed += not ok
        print(
            f"{'ok ' if ok else 'BAD'} {status} {path} ({host or 'here'}) {took * 1000:.0f} ms {len(body)} B",
            flush=True,
        )
    return 1 if failed else 0


def _output(folder: Path) -> None:
    """コンソールが無い（.exe）ときは、出力するものをアプリのフォルダーの desktop.log に書く。"""
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        try:
            log = open(folder / "desktop.log", "w", encoding="utf-8", buffering=1)
        except OSError:  # 書けないフォルダー・空きの無いディスク: ログ無しで起動する（起動できないよりよい）
            log = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stderr = log


def _install_over(target: str, pid: int) -> int:
    """更新の新しいビルドの部分（update.install_over）。
    したことはアプリのデータフォルダーの update.log に書く（.exe にはコンソールが無い。
    アプリ自身のログは動いているアプリのもの）。"""
    folder = data_folder()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        log = open(folder / "update.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        log = None

    def write(words: str) -> None:
        if log is not None:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {words}\n")

    try:
        return update.install_over(Path(target), pid, log=write)
    finally:
        if log is not None:
            log.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="VALTracker", description="VAL Tracker を PC のアプリとして開く")
    parser.add_argument("--port", type=int, default=0, help="the server's port (0: any free one)")
    parser.add_argument("--browser", action="store_true", help="open in the default browser, not a window")
    parser.add_argument("--check", action="store_true", help="start, check that the pages answer, stop")
    parser.add_argument("--close-after", type=float, default=0, help="close the window after this many seconds")
    parser.add_argument(pc.MINIMISED, action="store_true", help="open in the taskbar (the start with Windows)")
    parser.add_argument(update.INSTALL, metavar="FOLDER", help="put this build in place of the app in FOLDER")
    parser.add_argument("--wait-pid", type=int, default=0, help="with --install-over: the app to wait for")
    parser.add_argument(update.UPDATED, action="store_true", help="the app was just updated (the page says so)")
    args = parser.parse_args(argv)
    if args.install_over:
        # ダウンロードしたアプリ（update.py）が起動した新しいビルド: アプリと入れ替わって終わる
        return _install_over(args.install_over, args.wait_pid)
    if not args.check and not first():
        # 2 回開いた: すでにあるウィンドウが戻ってくる（最小化、またはほかのウィンドウの裏）
        if not show_running():
            tell(ALREADY_OPEN)
        return 0
    folder = data_folder()
    folder.mkdir(parents=True, exist_ok=True)
    _output(folder)  # 上の確認の後: 動いているアプリのログは空にしない
    calmed = calm()
    print(f"{NAME}: {', '.join(calmed) or 'no Windows priorities (not Windows)'}", flush=True)
    # ダウンロードした zip から展開した: ウィンドウの部分を読む前に Windows の印を外す
    home = update.running_home()
    unmarked = unmark(home)
    if unmarked:
        print(f"{NAME}: {unmarked} of the app's files were marked as come from the internet: unmarked", flush=True)
    os.environ.setdefault("VAL_COLLECT_SECONDS", "0")  # 混む時間のコレクター: サイトの仕事で、PC の仕事ではない
    local = LocalClient()
    app = create_app(instance=folder, local=local)
    app.config["VAL_BUILD"] = read_build()
    app.config["VAL_CALM"] = calmed
    # ほかのプログラムが前面にあるときの試合成立: タスクバーのボタンが光る（pc.py）
    shown: list = []
    app.config["VAL_ALERT"] = alert = pc.Alert(
        app.config["VAL_SETTINGS"], folder, lambda: window_handle(shown[0]) if shown else 0, pc.windows()
    )
    local.listen(alert)
    app.config["VAL_AUTOSTART"] = pc.Autostart.here()
    # 持ち主がボタンを押したら自動更新（update.py）
    stopping = threading.Event()

    def where() -> str:
        seen = local.presence()
        return moment(seen) if seen and seen.get("running") else ""

    def close() -> None:
        stopping.set()
        if shown:
            shown[0].destroy()

    app.config["VAL_UPDATER"] = update.Updater(app, home, where, close)
    app.config["VAL_UPDATED"] = bool(args.updated)
    server, base = serve(app, args.port)
    local.start()
    if not args.check:
        look_for_updates(app, stopping)
        update.clean(folder, home)
    print(f"{NAME}: {base}/ (data: {folder})", flush=True)
    try:
        if args.check:
            return check(base)
        keep_window_low(stopping)
        first_page = base + FIRST_PAGE
        why = "" if args.browser else window(first_page, folder, args.close_after, args.minimized, shown.append)
        if args.browser or why:
            if not in_browser(first_page, why, folder / "desktop.log"):
                return 0  # ウィンドウが無く、持ち主はブラウザーを望まなかった
            if getattr(sys, "frozen", False):  # 止めるためのコンソールが無い: ボックスの OK がその代わり
                tell(IN_BROWSER)
                return 0
            print("Ctrl+C で終わります。", flush=True)
            while not stopping.wait(3600):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        local.stop()
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
