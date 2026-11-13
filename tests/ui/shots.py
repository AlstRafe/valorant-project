"""Screenshots of the PC app for docs/screenshots: its real window (valtracker/desktop.py's WebView2 window, so this
runs on Windows) on the made-up owner's pages with the game's real names and pictures (serve.py --desktop --real).

    python tests/ui/shots.py docs/screenshots

.github/workflows/screenshots.yml runs it on a Windows runner and commits the pictures. The players, matches and
numbers on them are made up (tests/fixtures, tests/ui/played.py); nothing is read from anyone's account.
"""

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

from valtracker import desktop  # noqa: E402

PORT = 5123
BASE = f"http://127.0.0.1:{PORT}"
#: The window as the app opens it the first time, and a narrow one beside the game on a second screen.
WIDE, NARROW = desktop.SIZE, (430, 860)
#: After a page has loaded and its pictures are there: its own scripts have run (charts, times, the game's state).
SETTLE = 1.5
#: The made-up player whose record HenrikDev's answers hold (serve.py --real), and the last match they played.
PLAYER, MATCH = "/p/Tester/JP1", "/m/ap/aaaaaaaa-0000-4000-8000-000000000001?me=Tester%23JP1"
#: What is done on a page before its picture. Pressing the third agent in agent select (the window's button:
#: /live/act, as the owner would), so that the button that locks the pick shows.
PICK = "[...document.querySelectorAll('.pick-grid .pick:not([disabled])')].slice(0, 3).pop().click()"
#: The first round's kills opened, and the first agent's page from the list of agents.
ROUND = "document.querySelector('details.round-feed').open = true"
AGENT = "document.querySelector('.agent-grid a').click()"


def at(section: str) -> str:
    """The page scrolled so that the heading ``section`` (its id) is under the window's header."""
    return (
        f"(function(){{var e=document.getElementById({json.dumps(section)});"
        "scrollTo(0,e.getBoundingClientRect().top+scrollY-76)})()"
    )


#: name, address, the window's size, the page's look, what is done on the page before the picture (JavaScript;
#: "go:" before it when it leads to another page). In this order: every page after /live in a match must be /live
#: too, since the app goes to /live by itself while a match is on.
SHOTS = (
    ("01-live-game-closed", "/live", WIDE, "dark", ""),
    ("02-live-menus-queue", "/live?phase=menus", WIDE, "dark", ""),
    ("03-live-matchmaking", "/live?phase=queue", WIDE, "dark", ""),
    ("04-live-agent-select", "/live?phase=pregame", WIDE, "dark", PICK),
    ("05-live-in-match", "/live?phase=ingame", WIDE, "dark", ""),
    ("06-live-result-after-match", "/live?phase=menus", WIDE, "dark", ""),
    ("07-player", f"{PLAYER}?full=1", WIDE, "dark", ""),
    ("08-player-numbers", f"{PLAYER}?full=1", WIDE, "dark", at("sum-title")),
    ("09-player-matches", f"{PLAYER}?full=1", WIDE, "dark", at("m-title")),
    ("10-player-agents-maps", f"{PLAYER}?full=1", WIDE, "dark", at("ag-title")),
    ("11-player-kill-map", f"{PLAYER}?full=1", WIDE, "dark", at("heat-title")),
    ("12-player-lifetime", f"{PLAYER}?span=all&full=1", WIDE, "dark", ""),
    ("13-match", MATCH, WIDE, "dark", ""),
    ("14-match-kill-map", MATCH, WIDE, "dark", at("duel-title")),
    ("15-match-rounds", MATCH, WIDE, "dark", ROUND + ";" + at("feed-title")),
    ("16-shop", "/shop", WIDE, "dark", ""),
    ("17-collection", "/collection", WIDE, "dark", ""),
    ("18-progress", "/progress", WIDE, "dark", ""),
    ("19-agents", "/agents", WIDE, "dark", ""),
    ("20-agent", "/agents", WIDE, "dark", "go:" + AGENT),
    ("21-tools", "/tools", WIDE, "dark", ""),
    ("22-crosshair", "/crosshair", WIDE, "dark", ""),
    ("23-servers", "/servers", WIDE, "dark", ""),
    ("24-ranks", "/ranks", WIDE, "dark", ""),
    ("25-settings-update-notice", "/settings?update=1", WIDE, "dark", ""),
    ("26-narrow-window-in-match", "/live?phase=ingame", NARROW, "dark", ""),
    ("27-light-in-match", "/live?phase=ingame", WIDE, "light", ""),
)
#: The page that was left is marked; one that has arrived has no mark and is whole.
LEAVE = "window.__left = true;"
ARRIVED = "!window.__left && document.readyState === 'complete'"
#: The pictures in the window that are not there yet (a picture further down is fetched when scrolled to), and
#: those that never came.
SEEN = (
    "[...document.images].filter(function(i){var b=i.getBoundingClientRect();"
    "return b.width>0&&b.bottom>0&&b.top<innerHeight&&b.right>0&&b.left<innerWidth})"
)
WAITING = f"{SEEN}.filter(function(i){{return !i.complete}}).length"
MISSING = f"{SEEN}.filter(function(i){{return i.complete&&!i.naturalWidth}}).map(function(i){{return i.src}})"


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


def ask(window, script: str):
    """What ``script`` gives on the page as it is now; None while the window is between two pages."""
    try:
        return window.run_js(script)
    except Exception:  # noqa: BLE001 - no page to ask
        return None


def until(window, script: str, wanted, seconds: float = 40.0) -> bool:
    """Whether ``script`` gave ``wanted`` within ``seconds``."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ask(window, script) == wanted:
            return True
        time.sleep(0.25)
    return False


def leave(window, how: str) -> bool:
    """The window goes where ``how`` (JavaScript on the page it shows) leads; whether the new page has arrived.
    The page itself is told to go: WebView2 does not load again the address it already shows."""
    ask(window, f"(function(){{{LEAVE}{how}}})()")
    return until(window, ARRIVED, True, 60.0)


def show(window, path: str, look: str) -> bool:
    """``path`` loaded afresh in the look asked for (the header's button keeps it in localStorage "theme": set on
    the page that is left, read by the next as it starts)."""
    kept = f"localStorage.setItem('theme',{json.dumps(look)});"
    return leave(window, f"try{{{kept}}}catch(e){{}}location.replace({json.dumps(BASE + path)});")


def take(window, folder: Path) -> None:
    """Every picture of SHOTS, then the window closes. Any failure is said and ends the run with exit 1."""
    from PIL import ImageGrab

    failed = []
    try:
        if not window.events.loaded.wait(90):
            raise RuntimeError("the first page did not load")
        time.sleep(3)  # the window is on the screen
        found = handle()
        if not found:
            raise RuntimeError("the app's window was not found")
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
            if not until(window, f"({ARRIVED}) && {WAITING} === 0", True, 20.0):  # a page that loaded itself again
                failed.append(f"{name}: the page was not whole when its picture was taken")
            for address in ask(window, MISSING) or []:
                failed.append(f"{name}: no picture at {address}")
            picture = ImageGrab.grab(bbox=bounds(found), all_screens=True)
            picture.save(folder / f"{name}.png", optimize=True)
            shown = ask(window, "location.pathname+location.search")
            print(f"{name}.png {picture.size[0]}x{picture.size[1]} {shown}", flush=True)
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
        desktop.follow_look(window)  # as the app does: the title bar in the page's look
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
