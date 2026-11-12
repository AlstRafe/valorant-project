"""VAL Tracker as a Windows app: ``python -m valtracker.desktop``, or VALTracker.exe (built by
.github/workflows/desktop.yml from desktop/VALTracker.py).

The site's own pages, served on this PC only (127.0.0.1, a free port) in a window of the app's own (WebView2: the
Edge engine Windows already has), signed in through the Riot Client on this PC (localclient.py): nothing to paste,
and /live switches the moment the client says the match is found.

Made never to get in VALORANT's way:

- it never touches the game: nothing reads or writes its process or memory, nothing is drawn over it (no
  overlay), no keys or clicks are sent. It talks to the Riot Client's local API and to Riot's servers, as the
  site does;
- it runs below normal priority, so the game gets the processor first (the window's processes too: they inherit
  it, and the few Chromium raises itself are put back), in Windows 11's efficiency mode, and Windows takes its
  memory back first;
- the window draws without the graphics card and without animations: the GPU stays the game's;
- it waits for the Riot Client's events instead of asking every second, and a hidden or minimised window asks
  nothing at all;
- it never brings itself to the front (that would take a full-screen game out of focus);
- the site's background work (looking at more players for the busy-hour estimates) is off.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from valtracker import NAME, create_app
from valtracker.localclient import LocalClient

#: The window's engine is told: no graphics card, no animations (the site's prefers-reduced-motion rules).
BROWSER_ARGUMENTS = "--disable-gpu --force-prefers-reduced-motion --disable-features=ElasticOverscroll"
#: The window's first size and its smallest (the site's phone layout below 760 px).
SIZE, SMALLEST = (1180, 860), (400, 560)
#: The page the window opens on.
FIRST_PAGE = "/live"
#: The server's threads: the pages, their pictures, and the pages waiting for the client's next event.
THREADS = 8
#: Held while the app runs: a second start finds it and says so instead of starting twice.
MUTEX = "Local\\VALTracker.Desktop"
ALREADY_OPEN = (
    "VAL Tracker はもう開いています（タスクバーにあります）。\nVAL Tracker is already open (see the taskbar)."
)
IN_BROWSER = (
    "VAL Tracker をブラウザーで開きました（アプリのウィンドウには Microsoft Edge WebView2 Runtime が要ります）。\n"
    "OK を押すと VAL Tracker は終わります。"
)
_held = []  # the mutex's handle, for the app's life


def data_folder() -> Path:
    """Settings, saved lists and pictures: %LOCALAPPDATA%\\VAL Tracker (VAL_DESKTOP_HOME names another)."""
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
    """Makes this process (and the window's, which inherit the priority) give way to the game: below normal
    priority, efficiency mode (Windows 11: slower cores, lower clocks), low memory priority. What was set."""
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

    # ProcessPowerThrottling (4): PROCESS_POWER_THROTTLING_EXECUTION_SPEED on = EcoQoS
    throttling = Throttling(1, 1, 1)
    if kernel.SetProcessInformation(me, 4, ctypes.byref(throttling), ctypes.sizeof(throttling)):
        done.append("efficiency mode")
    # ProcessMemoryPriority (0): MEMORY_PRIORITY_BELOW_NORMAL (4)
    memory = Memory(4)
    if kernel.SetProcessInformation(me, 0, ctypes.byref(memory), ctypes.sizeof(memory)):
        done.append("memory priority: below normal")
    return done


#: Priority classes at or under below normal (BELOW_NORMAL, IDLE): left as they are.
_LOW = (0x00004000, 0x00000040)
#: When (seconds after the window opens) the window's processes are put back below normal: as they start, then
#: every KEEP_LOW seconds.
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
    """The processes started (at any depth) by ``root``, from (process, parent) pairs."""
    found, grew = set(), True
    while grew:
        more = {child for child, parent in pairs if (parent == root or parent in found) and child not in found}
        more.discard(root)
        found |= more
        grew = bool(more)
    return found


def lower_window() -> int:
    """Puts the window's processes (WebView2's, all started by this app) back below normal priority: they inherit
    it, but Chromium raises a few itself (its GPU process above normal). How many were lowered (Windows only)."""
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
        handle = kernel.OpenProcess(0x0200 | 0x1000, False, pid)  # SET_INFORMATION, QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            if kernel.GetPriorityClass(handle) not in _LOW and kernel.SetPriorityClass(handle, 0x00004000):
                lowered += 1
        finally:
            kernel.CloseHandle(handle)
    return lowered


def keep_window_low(stop: threading.Event) -> None:
    """lower_window as the window's processes start, then every KEEP_LOW seconds (a new renderer, or one Chromium
    raised again), until ``stop``. One small list of processes a time."""

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
    """Whether no other VAL Tracker app is running (Windows; elsewhere always)."""
    if sys.platform != "win32":
        return True
    kernel = _kernel()
    handle = kernel.CreateMutexW(None, False, MUTEX)
    _held.append(handle)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def tell(words: str) -> None:
    """A message box (Windows), else a line on the console."""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, words, NAME, 0x40)  # MB_ICONINFORMATION
    else:
        print(words, flush=True)


class OnlyHere:
    """Answers only requests addressed to this PC's own address: a web page whose name was pointed at 127.0.0.1
    (DNS rebinding) gets nothing from the owner's pages."""

    def __init__(self, app, hosts: set[str]):
        self.app, self.hosts = app, hosts

    def __call__(self, environ, start_response):
        if environ.get("HTTP_HOST", "") not in self.hosts:
            start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"Forbidden"]
        return self.app(environ, start_response)


def serve(app, port: int = 0):
    """Starts the server in a thread: (the server, its address)."""
    from waitress.server import create_server

    hosts: set[str] = set()
    app.wsgi_app = OnlyHere(app.wsgi_app, hosts)
    server = create_server(app, host="127.0.0.1", port=port, threads=THREADS, ident=NAME)
    port = server.effective_port
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=server.run, name="server", daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def light_windows() -> bool:
    """Whether Windows is set to light apps (the window's colour before the page has drawn)."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except (ImportError, OSError):
        return False


def window(url: str, folder: Path, close_after: float = 0) -> bool:
    """Shows ``url`` in the app's window until it is closed: False when there is no window to be had (no
    pywebview, or Windows without WebView2), and the default browser is used instead."""
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", BROWSER_ARGUMENTS)
    try:
        import webview

        if sys.platform == "win32":
            from webview.platforms import winforms

            if winforms.renderer != "edgechromium":  # Internet Explorer's engine: the pages need a modern one
                return False
    except Exception:  # noqa: BLE001 - no pywebview, or its .NET part could not start
        return False
    shown = webview.create_window(
        NAME,
        url,
        width=SIZE[0],
        height=SIZE[1],
        min_size=SMALLEST,
        background_color="#f6f4ef" if light_windows() else "#161513",  # the site's paper
        text_select=True,
    )

    def close_later():
        time.sleep(close_after)
        shown.destroy()

    # cookies (language, the session) and the page's own lists are kept in the app's folder
    webview.start(close_later if close_after else None, private_mode=False, storage_path=str(folder / "window"))
    return True


def check(base: str) -> int:
    """The build's test (--check): the pages answer, a foreign address is refused. 0 when all is well."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    failed = 0
    for path, wanted, host in (
        ("/", 200, None),
        ("/live", 200, None),
        ("/live/state", 200, None),
        ("/live/next?after=-1", 200, None),
        ("/settings", 200, None),
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
    """Without a console (the .exe), what would be printed goes to desktop.log in the app's folder."""
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        log = open(folder / "desktop.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115 - open for the app's life
        sys.stdout = sys.stderr = log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="VALTracker", description="VAL Tracker を PC のアプリとして開く")
    parser.add_argument("--port", type=int, default=0, help="the server's port (0: any free one)")
    parser.add_argument("--browser", action="store_true", help="open in the default browser, not a window")
    parser.add_argument("--check", action="store_true", help="start, check that the pages answer, stop")
    parser.add_argument("--close-after", type=float, default=0, help="close the window after this many seconds")
    args = parser.parse_args(argv)
    if not args.check and not first():
        tell(ALREADY_OPEN)
        return 0
    folder = data_folder()
    folder.mkdir(parents=True, exist_ok=True)
    _output(folder)  # after the check above: the running app's log is not emptied
    print(f"{NAME}: {', '.join(calm()) or 'no Windows priorities (not Windows)'}", flush=True)
    os.environ.setdefault("VAL_COLLECT_SECONDS", "0")  # the busy-hour collector: the site's work, not the PC's
    local = LocalClient()
    server, base = serve(create_app(instance=folder, local=local), args.port)
    local.start()
    stopping = threading.Event()
    print(f"{NAME}: {base}/ (data: {folder})", flush=True)
    try:
        if args.check:
            return check(base)
        keep_window_low(stopping)
        if args.browser or not window(base + FIRST_PAGE, folder, args.close_after):
            webbrowser.open(base + FIRST_PAGE)
            if getattr(sys, "frozen", False):  # no console to stop it from: the box's OK does
                tell(IN_BROWSER)
                return 0
            print("Ctrl+C で終わります。", flush=True)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        local.stop()
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
