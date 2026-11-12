"""Screenshots of the PC app for docs/screenshots: its real window (valtracker/desktop.py's WebView2 window, so this
runs on Windows) on the made-up owner's pages with the game's real names and pictures (serve.py --desktop --real).

    python tests/ui/shots.py docs/screenshots

.github/workflows/screenshots.yml runs it on a Windows runner and commits the pictures. The players, matches and
numbers on them are made up (tests/fixtures); nothing is read from anyone's account.
"""

import ctypes
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

from valtracker import desktop  # noqa: E402

PORT = 5123
BASE = f"http://127.0.0.1:{PORT}"
#: The window as the app opens it the first time, and a narrow one beside the game on a second screen.
WIDE, NARROW = desktop.SIZE, (430, 860)
#: After a page has loaded and its pictures are there: the page's own look for the game's state has settled.
SETTLE = 2.0
#: The made-up player whose record HenrikDev's fixtures hold, and a match they played (henrik_matches.json).
PLAYER, MATCH = "Tester/JP1", "aaaaaaaa-0000-4000-8000-000000000001"
#: Pressing the third agent in agent select (the window's button: /live/act, as the owner would), so that the
#: button that locks the pick shows.
PICK = "document.querySelectorAll('.pick-grid .pick:not([disabled])')[2]?.click()"
TO_MATCHES = "document.getElementById('m-title')?.scrollIntoView()"
#: name, address, the window's size, light or dark, what is done on the page before the picture (JavaScript).
SHOTS = (
    ("01-live-game-closed", "/live", WIDE, "dark", ""),
    ("02-live-menus-queue", "/live?phase=menus", WIDE, "dark", ""),
    ("03-live-matchmaking", "/live?phase=queue", WIDE, "dark", ""),
    ("04-live-agent-select", "/live?phase=pregame", WIDE, "dark", PICK),
    ("05-live-in-match", "/live?phase=ingame", WIDE, "dark", ""),
    ("06-live-result-after-match", "/live?phase=menus", WIDE, "dark", ""),
    ("07-player", f"/p/{PLAYER}?full=1", WIDE, "dark", ""),
    ("08-player-matches", f"/p/{PLAYER}?full=1", WIDE, "dark", TO_MATCHES),
    ("09-match", f"/m/ap/{MATCH}?me=Tester%23JP1", WIDE, "dark", ""),
    ("10-shop", "/shop", WIDE, "dark", ""),
    ("11-collection", "/collection", WIDE, "dark", ""),
    ("12-progress", "/progress", WIDE, "dark", ""),
    ("13-tools", "/tools", WIDE, "dark", ""),
    ("14-settings-update-notice", "/settings?update=1", WIDE, "dark", ""),
    ("15-narrow-window-in-match", "/live?phase=ingame", NARROW, "dark", ""),
    ("16-light-in-match", "/live?phase=ingame", WIDE, "light", ""),
)
LOADED = "document.readyState === 'complete' && [...document.images].every((image) => image.complete)"


def handle() -> int:
    """The app's window (its title and window class, as desktop.show_running finds it), 0 when there is none."""
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
    """The window as it is seen on the screen (without the invisible border Windows gives it for resizing)."""
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


def take(window, folder: Path) -> None:
    """Every picture of SHOTS, then the window closes. Any failure is said and ends the run with exit 1."""
    from PIL import ImageGrab

    failed = []
    try:
        time.sleep(4)  # the window is on the screen
        found = handle()
        if not found:
            raise RuntimeError("the app's window was not found")
        size = WIDE
        for name, path, wanted, theme, script in SHOTS:
            if wanted != size:
                window.resize(*wanted)
                size = wanted
                time.sleep(1)
            for _ in range(2):  # the look (dark or light) is the page's own setting: set, then loaded again with it
                window.events.loaded.clear()
                window.load_url(BASE + path)
                window.events.loaded.wait(60)
                kept = "null" if theme == "light" else "'dark'"
                changed = window.evaluate_js(
                    f"(() => {{ const now = localStorage.getItem('theme'); if (now === {kept}) return false;"
                    f" if ({kept} === null) localStorage.removeItem('theme'); else localStorage.setItem('theme', {kept});"
                    " return true; })()"
                )
                if not changed:
                    break
            for _ in range(40):
                if window.evaluate_js(LOADED):
                    break
                time.sleep(0.5)
            else:
                failed.append(f"{name}: some pictures did not load")
            time.sleep(SETTLE)
            if script:
                window.evaluate_js(script)
                time.sleep(SETTLE + 1.5)
            picture = ImageGrab.grab(bbox=bounds(found), all_screens=True)
            picture.save(folder / f"{name}.png", optimize=True)
            print(f"{name}.png {picture.size[0]}x{picture.size[1]} {path}", flush=True)
    except Exception as error:  # noqa: BLE001 - said below; the window must close whatever happened
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
    server = subprocess.Popen(serve, cwd=ROOT)  # noqa: S603 - this repository's own script
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
            on_top=True,  # nothing of the runner's own over it while the pictures are taken
        )
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
