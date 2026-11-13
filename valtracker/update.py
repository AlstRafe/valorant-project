"""The PC app updates itself: the owner presses 更新 in the app, and the newest build is downloaded, checked, put
in place of the running one, and opened again.

How, step by step:

1. the site is asked which build is the newest (release.py: one small answer);
2. the release's zip is downloaded from GitHub into the app's data folder (``update/``), with its progress told
   to the page;
3. the zip is checked before anything of it is used: it must come from GitHub over https, hold the app and nothing
   that would land outside its folder, and its stamp (valtracker/BUILD) must be the build the site named: a file
   that is not that build is thrown away;
4. the new build is unpacked beside the zip and started with ``--install-over <the app's folder>``; the running app
   closes;
5. that new copy waits for the old app to end, puts its own files in the app's folder (the old ones are set aside
   first and put back when anything fails), starts the app from there and ends. The app then removes ``update/``.

Only the built app on Windows can do this (the source has nothing to replace), and only what the build itself
holds is replaced (VALTracker.exe and _internal): nothing else in the folder is touched, the settings and saved
lists are in another folder.

Never in VALORANT's way: nothing is downloaded by itself (only when the owner presses the button), and not during
agent select or a match (a download takes the line the game is using); the new build is put in place then only when
the owner presses again.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from valtracker import release

#: The newest build's zip (desktop.yml puts it on the release "desktop" at every change of the app).
ASSET = "https://github.com/AlstRafe/valorant-project/releases/download/desktop/VALTracker-windows.zip"
#: Where GitHub serves a release's file from, after its redirect.
HOSTS = ("github.com", "githubusercontent.com")
#: The zip's one folder, the app in it, and its stamp.
TOP, EXE, STAMP = "VALTracker", "VALTracker.exe", "_internal/valtracker/BUILD"
#: Nothing larger is taken (the zip is some 40 MB, unpacked some 100 MB).
MAX_ZIP, MAX_UNPACKED = 400 * 1024 * 1024, 1024 * 1024 * 1024
#: The data folder's part the update uses, and what the new copy is started with.
FOLDER, INSTALL, UPDATED = "update", "--install-over", "--updated"
#: How long the new copy waits for the old app to end, and for a file another program still holds (seconds).
WAIT_FOR_APP, WAIT_FOR_FILE = 60.0, 20.0
#: Where the game is when nothing is downloaded or put in place (localclient.moment).
PLAYING = ("found", "pregame", "ingame")


class UpdateError(Exception):
    """Why the update stopped: ``kind`` is one of notbuilt (not a built app on Windows), unwritable (the app's
    folder cannot be written), playing (agent select or a match), nonewer (this is the newest), notready (the
    newest build is still being made), network, badfile (the download is not the build the site named), nostart
    (the new build could not be started: this app goes on as it is)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


def ask_site(site: str) -> object:
    from valtracker.desktop import _latest

    return _latest(site)


def fetch(url: str, target: Path, told, opener=urllib.request.urlopen) -> None:
    """Downloads ``url`` to ``target`` (https, from GitHub), telling ``told(done, total)`` as it goes."""
    if not url.startswith("https://"):
        raise UpdateError("network", "not https")
    request = urllib.request.Request(url, headers={"User-Agent": "VALTracker-desktop"})
    try:
        with opener(request, timeout=30) as answer:  # noqa: S310 - https, checked above
            came_from = urlsplit(answer.geturl())
            host = (came_from.hostname or "").lower()
            if came_from.scheme != "https" or not any(host == name or host.endswith("." + name) for name in HOSTS):
                raise UpdateError("network", "not from GitHub")
            total = int(answer.headers.get("Content-Length") or 0)
            if total > MAX_ZIP:
                raise UpdateError("badfile", "too large")
            done = 0
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as file:
                while True:
                    part = answer.read(256 * 1024)
                    if not part:
                        break
                    done += len(part)
                    if done > MAX_ZIP:
                        raise UpdateError("badfile", "too large")
                    file.write(part)
                    told(done, total)
            if total and done != total:
                raise UpdateError("network", "cut off")
    except (OSError, ValueError) as error:
        raise UpdateError("network", type(error).__name__) from error


def unpack(archive: Path, folder: Path, commit: str) -> Path:
    """Checks the zip and unpacks it into ``folder``: the new app's folder (``folder``/VALTracker). UpdateError
    (badfile) when it is not the app, holds a path that would land elsewhere, or is not the build ``commit``."""
    try:
        with zipfile.ZipFile(archive) as file:
            names = [info.filename.replace("\\", "/") for info in file.infolist()]
            size = sum(info.file_size for info in file.infolist())
            for name in names:
                parts = name.split("/")
                if parts[0] != TOP or ".." in parts or name.startswith("/") or ":" in name:
                    raise UpdateError("badfile", "a path outside the app")
            if f"{TOP}/{EXE}" not in names or f"{TOP}/{STAMP}" not in names:
                raise UpdateError("badfile", "not the app")
            if size > MAX_UNPACKED:
                raise UpdateError("badfile", "too large")
            stamp = release.parse(file.read(f"{TOP}/{STAMP}").decode("ascii", "replace"))
            if not stamp or stamp["commit"] != commit:
                raise UpdateError("notready" if stamp else "badfile", "another build")
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            file.extractall(folder)  # noqa: S202 - every path was checked above
    except (zipfile.BadZipFile, OSError, KeyError) as error:
        raise UpdateError("badfile", type(error).__name__) from error
    return folder / TOP


def writable(folder: Path) -> bool:
    """Whether files can be made in ``folder`` (the app's own)."""
    probe = folder / ".valtracker-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def _start(command: list[str]) -> None:
    """Starts a program of the app's own that outlives this one."""
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, close_fds=True, creationflags=flags, cwd=str(Path(command[0]).parent))  # noqa: S603


class Updater:
    """The update's state for the pages, and its steps. ``app``: the Flask app (its build, the site to ask, the
    data folder); ``home``: the running app's folder (None: not a built app); ``moment``: () -> where the game is
    (localclient.moment's words, "" when not known); ``close``: () -> closes the app; the rest are the tests'."""

    def __init__(
        self, app, home: Path | None, moment, close, ask=ask_site, fetch=fetch, start=_start, pid=None, pause=1.0
    ):
        self.app, self.home, self.moment, self.close = app, home, moment, close
        self.ask, self.fetch, self.start, self.pid = ask, fetch, start, pid or os.getpid()
        #: How long the page is given to read "installing" before the window goes (seconds).
        self.pause = pause
        self.folder = Path(app.instance_path) / FOLDER
        self._lock = threading.Lock()
        #: state: idle, downloading, checking, ready (downloaded, waiting for the match to end), installing, failed
        self._state: dict = {"state": "idle", "done": 0, "total": 0, "problem": "", "detail": ""}
        self._new: Path | None = None

    @property
    def possible(self) -> bool:
        return self.home is not None

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, state: str, **more) -> None:
        with self._lock:
            self._state = {"state": state, "done": 0, "total": 0, "problem": "", "detail": ""} | more

    def look(self) -> dict | None:
        """Asks the site now which build is the newest (the settings page's button): the newer one
        ({"commit", "date", "url", "ready"}) or None. UpdateError (network) when the site cannot be asked."""
        try:
            told = self.ask(self.app.config["VAL_PUBLIC"])
        except (OSError, ValueError) as error:
            raise UpdateError("network", type(error).__name__) from error
        found = release.newer(self.app.config.get("VAL_BUILD"), told, time.time())
        self.app.config["VAL_UPDATE_LOOKED"] = time.time()
        # a build still being made is not offered yet (desktop.look_for_updates tells it once it is there)
        self.app.config["VAL_UPDATE"] = found if found and found["ready"] else None
        return found

    def press(self) -> dict:
        """The owner's button: starts the download, or (downloaded during a match) puts the new build in place.
        The state after it."""
        with self._lock:
            now = self._state["state"]
            busy = now in ("downloading", "checking", "installing")
            if not busy:
                self._state = {"state": "checking", "done": 0, "total": 0, "problem": "", "detail": ""}
        if not busy:
            target = self._install if now == "ready" and self._new is not None else self._run
            threading.Thread(target=self._guarded, args=(target,), name="update", daemon=True).start()
        return self.state()

    def _guarded(self, step) -> None:
        try:
            step()
        except UpdateError as error:
            self._set("failed", problem=error.kind, detail=error.detail)
        except Exception as error:  # noqa: BLE001 - whatever stops it is told, the app goes on
            self._set("failed", problem="network", detail=type(error).__name__)

    def _playing(self) -> bool:
        return self.moment() in PLAYING

    def _run(self) -> None:
        if self.home is None:
            raise UpdateError("notbuilt")
        if not writable(self.home):
            raise UpdateError("unwritable")
        if self._playing():
            raise UpdateError("playing")
        found = self.look()
        if not found:
            raise UpdateError("nonewer")
        if not found["ready"]:
            raise UpdateError("notready")
        shutil.rmtree(self.folder, ignore_errors=True)
        archive = self.folder / "VALTracker-windows.zip"
        self._set("downloading")
        self.fetch(ASSET, archive, lambda done, total: self._set("downloading", done=done, total=total))
        self._set("checking")
        self._new = unpack(archive, self.folder / "new", found["commit"])
        try:
            archive.unlink()
        except OSError:
            pass
        if self._playing():
            self._set("ready")  # a match began meanwhile: put in place when the owner presses again
            return
        self._install()

    def _install(self) -> None:
        if self.home is None or self._new is None or not (self._new / EXE).is_file():
            raise UpdateError("badfile", "nothing downloaded")
        if self._playing():
            self._set("ready")
            return
        self._set("installing")
        try:
            self.start([str(self._new / EXE), INSTALL, str(self.home), "--wait-pid", str(self.pid)])
        except OSError as error:
            raise UpdateError("nostart", type(error).__name__) from error
        time.sleep(self.pause)  # the page reads "installing" once more before the window goes
        self.close()


def running_home() -> Path | None:
    """The built app's folder on Windows, None for the source or elsewhere."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def wait_for(pid: int, seconds: float = WAIT_FOR_APP) -> bool:
    """Waits until the program ``pid`` has ended (Windows): whether it has."""
    if sys.platform != "win32" or pid <= 0:
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True  # already gone
    try:
        return kernel.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def _retry(step, seconds: float = WAIT_FOR_FILE, sleep=time.sleep):
    """``step()``, again for a while when a file is still held (a virus scanner, the old app's last moments)."""
    until = time.monotonic() + seconds
    while True:
        try:
            return step()
        except PermissionError:
            if time.monotonic() >= until:
                raise
            sleep(0.5)


def replace(source: Path, target: Path, sleep=time.sleep) -> list[str]:
    """Puts everything of ``source`` (the new build's folder) into ``target`` (the app's folder): each of the
    target's own entries of the same name is set aside first (``name.old``) and removed at the end; when anything
    fails, what was set aside is put back and the error is raised. Nothing else in ``target`` is touched. The
    names put in place."""
    names = sorted(entry.name for entry in source.iterdir())
    if EXE not in names:
        raise OSError("not the app")
    aside: list[str] = []
    placed: list[str] = []

    def remove(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    try:
        for name in names:
            old, kept = target / name, target / (name + ".old")
            _retry(lambda kept=kept: remove(kept), sleep=sleep)
            if old.exists():
                _retry(lambda old=old, kept=kept: old.rename(kept), sleep=sleep)
                aside.append(name)
            placed.append(name)
            if (source / name).is_dir():
                shutil.copytree(source / name, old)
            else:
                shutil.copy2(source / name, old)
    except OSError:
        for name in placed:
            try:
                remove(target / name)
            except OSError:
                pass
        for name in aside:
            try:
                (target / (name + ".old")).rename(target / name)
            except OSError:
                pass
        raise
    for name in aside:
        try:
            remove(target / (name + ".old"))
        except OSError:
            pass  # still held: the app removes it at its next start (clean)
    return names


def install_over(target: Path, pid: int, source: Path | None = None, start=_start, log=print) -> int:
    """What the new copy does when started with ``--install-over``: waits for the old app, puts itself in its
    place, starts the app there. 0 when the app was started (the new one, or the old one again after a failure)."""
    source = source or Path(sys.executable).resolve().parent
    target = Path(target)
    if not wait_for(pid):
        log("update: the app did not end; nothing replaced")
        return 1
    if not (target / EXE).is_file() or source == target:
        log("update: not the app's folder; nothing replaced")
        return 1
    try:
        names = replace(source, target)
        log(f"update: replaced {', '.join(names)}")
        # VAL_UPDATE_THEN: more arguments for the app opened after it (the build's own test: --check)
        start([str(target / EXE), UPDATED, *os.environ.get("VAL_UPDATE_THEN", "").split()])
    except OSError as error:
        log(f"update: failed ({type(error).__name__}: {error}); the old build is back")
        start([str(target / EXE)])
    return 0


def clean(folder: Path, home: Path | None, wait: float = 5.0) -> None:
    """After a start: removes what an update left (the data folder's ``update/``, a ``.old`` beside the app), a
    little later (the copy that put the new build in place is still ending)."""

    def run():
        time.sleep(wait)
        shutil.rmtree(folder / FOLDER, ignore_errors=True)
        if home is not None:
            for name in (EXE + ".old", "_internal.old"):
                path = home / name
                try:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    pass

    threading.Thread(target=run, name="update-clean", daemon=True).start()
