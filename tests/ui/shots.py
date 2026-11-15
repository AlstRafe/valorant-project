"""docs/screenshots 用の PC アプリのスクリーンショット: 本物のウィンドウ（valtracker/desktop.py の WebView2
ウィンドウなので Windows で動かす）で、作りものの持ち主のページを、
ゲームの本物の名前と絵で撮る（serve.py --desktop --real）。

python tests/ui/shots.py docs/screenshots

.github/workflows/screenshots.yml が Windows のランナーで実行し、画像をコミットする。写っているプレイヤー・試合・
数値は作りもの（tests/fixtures、tests/ui/played.py）で、誰のアカウントからも読まない。"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from valtracker import desktop

PORT = 5123
BASE = f"http://127.0.0.1:{PORT}"
#: アプリが初めて開くときのウィンドウと、2 つ目の画面でゲームの横に置く細いもの。
WIDE, NARROW = desktop.SIZE, (430, 860)
#: ページが読み込まれ絵もそろったあと: ページ自身のスクリプト（グラフ、時刻、ゲームの状態）が動いた後。
SETTLE = 1.5
#: HenrikDev の答えが記録を持つ作りもののプレイヤー（serve.py --real）と、その人の最後の試合。
PLAYER, MATCH = "/p/Tester/JP1", "/m/ap/aaaaaaaa-0000-4000-8000-000000000001?me=Tester%23JP1"
#: 撮る前にページですること。エージェント選択で 3 番目のエージェントを押す（ウィンドウのボタン: 持ち主と同じく
#: /live/act）と、ピックを確定するボタンが出る。
PICK = "[...document.querySelectorAll('.pick-grid .pick:not([disabled])')].slice(0, 3).pop().click()"
#: 最初のラウンドのキルを開き、エージェント一覧から最初のエージェントのページへ。クロスヘアのページが描く、
#: ゲームがコピーする形のクロスヘアコード（小さいシアンの十字）。
CROSSHAIR = "0%3BP%3Bc%3B5%3Bh%3B0%3B0l%3B4%3B0o%3B2%3B0a%3B1%3B0f%3B0%3B1b%3B0"
ROUND = "document.querySelector('details.round-feed').open = true"
AGENT = "document.querySelector('.agent-grid a').click()"


def at(section: str) -> str:
    """見出し ``section``（その id）がウィンドウのヘッダーの下に来るようにスクロールする。"""
    return (
        f"(function(){{var e=document.getElementById({json.dumps(section)});"
        "scrollTo(0,e.getBoundingClientRect().top+scrollY-76)})()"
    )


#: 名前、アドレス、ウィンドウの大きさ、ページの見た目、撮る前にページですること（JavaScript。
#: 別のページへ行くなら前に "go:"）。この順で: 試合中はアプリが自分で /live に行くので、
#: 試合中の /live より後のページはすべて /live でなければならない。
SHOTS = (
    ("01-live-agent-select", "/live?phase=pregame", WIDE, "dark", PICK),
    ("02-live-in-match", "/live?phase=ingame", WIDE, "dark", ""),
    ("03-player", f"{PLAYER}?full=1", WIDE, "dark", ""),
)
#: 離れたページには印を付ける。着いたページには印が無く、全部そろっている。
LEAVE = "window.__left = true;"
ARRIVED = "!window.__left && document.readyState === 'complete'"
#: ウィンドウの中でまだ届いていない絵（下の方の絵はスクロールしたときに取る）と、届かなかった絵。
SEEN = (
    "[...document.images].filter(function(i){var b=i.getBoundingClientRect();"
    "return b.width>0&&b.bottom>0&&b.top<innerHeight&&b.right>0&&b.left<innerWidth})"
)
WAITING = f"{SEEN}.filter(function(i){{return !i.complete}}).length"
MISSING = f"{SEEN}.filter(function(i){{return i.complete&&!i.naturalWidth}}).map(function(i){{return i.src}})"


def handle() -> int:
    """アプリのウィンドウ（desktop.show_running と同じくタイトルとウィンドウクラスで探す）。無ければ 0。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    each = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user.EnumWindows.argtypes = [each, ctypes.c_void_p]
    user.GetWindowTextW.argtypes = user.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    found = []

    def look(window, _):
        title, kind = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        user.GetWindowTextW(window, title, 64)
        user.GetClassNameW(window, kind, 128)
        if desktop.is_app_window(title.value, kind.value):
            found.append(window)
        return not found

    user.EnumWindows(each(look), None)
    return found[0] if found else 0


def bounds(window: int) -> tuple[int, int, int, int]:
    """画面に見えるままのウィンドウ（Windows がリサイズ用に付ける見えない枠を除く）。"""
    rect = wintypes.RECT()
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    dwm.DwmGetWindowAttribute(window, 9, ctypes.byref(rect), ctypes.sizeof(rect))  # DWMWA_EXTENDED_FRAME_BOUNDS
    return rect.left, rect.top, rect.right, rect.bottom


def wait_for_server(server: subprocess.Popen) -> None:
    for _ in range(180):
        if server.poll() is not None:
            raise SystemExit(f"The made-up server ended (exit {server.returncode}).")
        try:
            with urllib.request.urlopen(f"{BASE}/help", timeout=5):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit("The made-up server did not answer in three minutes.")


def ask(window, script: str):
    """今のページで ``script`` が返すもの。ウィンドウがページの切り替え途中なら None。"""
    try:
        return window.run_js(script)
    except Exception:  # 聞けるページが無い
        return None


def until(window, script: str, wanted, seconds: float = 40.0) -> bool:
    """``script`` が ``seconds`` 秒以内に ``wanted`` を返したか。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ask(window, script) == wanted:
            return True
        time.sleep(0.25)
    return False


def leave(window, how: str) -> bool:
    """ウィンドウを ``how``（表示中のページでの JavaScript）の行き先へ。新しいページに着いたか。
    ページ自身に移動させる: WebView2 は表示中のアドレスを読み直さない。"""
    ask(window, f"(function(){{{LEAVE}{how}}})()")
    return until(window, ARRIVED, True, 60.0)


def show(window, path: str, look: str) -> bool:
    """頼んだ見た目で ``path`` を読み直す（ヘッダーのボタンは localStorage の "theme" に入れる: 離れるページで書き、
    次のページが開くときに読む）。"""
    kept = f"localStorage.setItem('theme',{json.dumps(look)});"
    return leave(window, f"try{{{kept}}}catch(e){{}}location.replace({json.dumps(BASE + path)});")


def take(window, folder: Path) -> None:
    """SHOTS の画像を全部撮ってウィンドウを閉じる。失敗があれば知らせて exit 1 で終わる。"""
    from PIL import ImageGrab

    failed = []
    try:
        if not window.events.loaded.wait(90):
            raise RuntimeError("the first page did not load")
        time.sleep(3)  # ウィンドウが画面にある
        found = handle()
        if not found:
            raise RuntimeError("the app's window was not found")
        # ランナーのポインターは画面の真ん中にある: その下のものは押されたように見える
        ctypes.WinDLL("user32").SetCursorPos(2, 2)
        size = WIDE
        for name, path, wanted, look, script in SHOTS:
            if wanted != size:
                window.resize(*wanted)
                size = wanted
                time.sleep(1)
            if not show(window, path, look):
                failed.append(f"{name}: {path} did not load")
                continue
            if script.startswith("go:"):
                if not leave(window, script[3:]):
                    failed.append(f"{name}: the page after {path} did not load")
                    continue
            elif script:
                until(window, WAITING, 0)
                time.sleep(SETTLE)
                done = ask(window, f"(function(){{try{{{script};return true}}catch(e){{return String(e)}}}})()")
                if done is not True:
                    failed.append(f"{name}: {done}")
            if not until(window, WAITING, 0):
                failed.append(f"{name}: some pictures did not come")
            time.sleep(SETTLE)
            if not until(window, f"({ARRIVED}) && {WAITING} === 0", True, 20.0):  # 自分で読み直したページ
                failed.append(f"{name}: the page was not whole when its picture was taken")
            for address in ask(window, MISSING) or []:
                failed.append(f"{name}: no picture at {address}")
            picture = ImageGrab.grab(bbox=bounds(found), all_screens=True)
            picture.save(folder / f"{name}.png", optimize=True)
            shown = ask(window, "location.pathname+location.search")
            print(f"{name}.png {picture.size[0]}x{picture.size[1]} {shown}", flush=True)
    except Exception as error:  # 下で知らせる。何があってもウィンドウは閉じる
        failed.append(f"{type(error).__name__}: {error}")
    finally:
        for line in failed:
            print(f"FAILED {line}", flush=True)
        (folder / ".failed").write_text("\n".join(failed), encoding="utf-8")
        window.destroy()


def main() -> int:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    serve = [sys.executable, str(ROOT / "tests" / "ui" / "serve.py"), str(PORT), "--desktop", "--real"]
    server = subprocess.Popen(serve, cwd=ROOT)  # このリポジトリ自身のスクリプト
    try:
        wait_for_server(server)
        import webview

        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", desktop.BROWSER_ARGUMENTS)
        window = webview.create_window(
            desktop.NAME,
            BASE + "/?lang=ja",
            width=WIDE[0],
            height=WIDE[1],
            x=60,
            y=40,
            min_size=desktop.SMALLEST,
            background_color="#161513",
            text_select=True,
            on_top=True,  # 撮っている間はランナー自身のものを上に出さない
        )
        desktop.follow_look(window)  # アプリと同じく: タイトルバーをページの見た目に
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as storage:
            webview.start(take, (window, folder), private_mode=False, storage_path=storage)
    finally:
        server.terminate()
    failed = folder / ".failed"
    said = failed.read_text(encoding="utf-8") if failed.exists() else "no picture was taken"
    failed.unlink(missing_ok=True)
    taken = len(list(folder.glob("*.png")))
    print(f"{taken} of {len(SHOTS)} pictures in {folder}", flush=True)
    return 1 if said or taken != len(SHOTS) else 0


if __name__ == "__main__":
    sys.exit(main())
