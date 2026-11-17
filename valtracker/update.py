"""PC アプリの自動更新: 持ち主がアプリで「更新」を押すと、最新ビルドをダウンロードし、確かめ、動いているものと入れ替え、
また開く。

手順:

1. 最新ビルドはどれかサイトに聞く（release.py: 小さな答え 1 つ）
2. リリースの zip を GitHub からアプリのデータフォルダー（``update/``）にダウンロードし、進み具合をページに伝える
3. 使う前に zip を確かめる: GitHub から https で来たもので、アプリが入っていて、フォルダーの外に出るものが無く、
  スタンプ（valtracker/BUILD）がサイトの示したビルドであること。そのビルドでないファイルは捨てる
4. 新しいビルドを zip の横に展開し、``--install-over <アプリのフォルダー>`` で起動する。動いているアプリは閉じる
5. その新しいコピーは古いアプリが終わるのを待ち、自分のファイルをアプリのフォルダーに置き（古いものは先によけておき、
  何か失敗したら戻す）、そこからアプリを起動して終わる。アプリはそのあと ``update/`` を消す。

できるのは Windows のビルドしたアプリだけ（ソースには入れ替えるものが無い）。
入れ替えるのはビルドに入っているもの（VALTracker.exe と _internal）だけ: フォルダーのほかのものには触らず、
設定と保存した一覧は別のフォルダーにある。

VALORANT の邪魔はしない: 勝手にはダウンロードしない（持ち主がボタンを押したときだけ）。
エージェント選択と試合の間もしない（ダウンロードはゲームの回線を使う）。その場合、
新しいビルドへの入れ替えは持ち主がもう一度押したときだけ。"""

from __future__ import annotations

import contextlib
import ctypes
import http.client
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

#: 最新ビルドの zip（desktop.yml がアプリが変わるたびにリリース "desktop" に置く）。
ASSET = "https://github.com/AlstRafe/valorant-project/releases/download/desktop/VALTracker-windows.zip"
#: リダイレクトのあとに GitHub がリリースのファイルを配る場所。
HOSTS = ("github.com", "githubusercontent.com")
#: zip の中のフォルダー 1 つ、その中のアプリ、スタンプ。
TOP, EXE, STAMP = "VALTracker", "VALTracker.exe", "_internal/valtracker/BUILD"
#: これより大きいものは受け取らない（zip は 40 MB ほど、展開して 100 MB ほど）。
MAX_ZIP, MAX_UNPACKED = 400 * 1024 * 1024, 1024 * 1024 * 1024
#: 更新が使うデータフォルダーの部分と、新しいコピーを起動する引数。
FOLDER, INSTALL, UPDATED = "update", "--install-over", "--updated"
#: 新しいコピーが古いアプリの終わりを待つ秒数と、ほかのプログラムがまだ握っているファイルを待つ秒数。
WAIT_FOR_APP, WAIT_FOR_FILE = 60.0, 20.0
#: 何もダウンロード・入れ替えしないゲームの状態（localclient.moment）。
PLAYING = ("found", "pregame", "ingame")


class UpdateError(Exception):
    """更新が止まった理由: ``kind`` は notbuilt（Windows のビルドしたアプリでない）、
    unwritable（アプリのフォルダーに書けない）、playing（エージェント選択か試合中）、nonewer（これが最新）、
    notready（最新ビルドはまだ作っている途中）、network、badfile（ダウンロードしたものがサイトの示したビルドでない）、
    nostart（新しいビルドを起動できなかった: このアプリはそのまま続ける）のどれか。"""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.kind, self.detail = kind, detail


def ask_site(site: str) -> object:
    from valtracker.desktop import _latest

    return _latest(site)


def fetch(url: str, target: Path, told, opener=urllib.request.urlopen) -> None:
    """``url`` を ``target`` にダウンロードする（https、GitHub から）。進み具合を ``told(done, total)`` で伝える。"""
    if not url.startswith("https://"):
        raise UpdateError("network", "not https")
    request = urllib.request.Request(url, headers={"User-Agent": "VALTracker-desktop"})
    try:
        with opener(request, timeout=30) as answer:  # https（上で確かめた）
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
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise UpdateError("network", type(error).__name__) from error


def unpack(archive: Path, folder: Path, commit: str) -> Path:
    """zip を確かめて ``folder`` に展開する: 新しいアプリのフォルダー（``folder``/VALTracker）。アプリでない、
    外に出るパスがある、ビルド ``commit`` でないなら UpdateError（badfile）。"""
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
            file.extractall(folder)
    except (zipfile.BadZipFile, OSError, KeyError) as error:
        raise UpdateError("badfile", type(error).__name__) from error
    return folder / TOP


def writable(folder: Path) -> bool:
    """``folder``（アプリ自身のもの）にファイルを作れるか。"""
    probe = folder / ".valtracker-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def _start(command: list[str]) -> None:
    """このアプリより長く生きるアプリ自身のプログラムを起動する。"""
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS、CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, close_fds=True, creationflags=flags, cwd=str(Path(command[0]).parent))


def _fresh(state: str) -> dict:
    """進み具合の無い状態。"""
    return {"state": state, "done": 0, "total": 0, "problem": "", "detail": ""}


class Updater:
    """ページ用の更新の状態と、その手順。``app``: Flask アプリ（ビルド、聞くサイト、データフォルダー）。
    ``home``: 動いているアプリのフォルダー（None: ビルドしたアプリでない）。
    ``moment``: () -> ゲームの状態（localclient.moment の文、不明なら ""）。``close``: () -> アプリを閉じる。
    残りはテスト用。"""

    def __init__(
        self, app, home: Path | None, moment, close, ask=ask_site, fetch=fetch, start=_start, pid=None, pause=1.0
    ):
        self.app, self.home, self.moment, self.close = app, home, moment, close
        self.ask, self.fetch, self.start, self.pid = ask, fetch, start, pid or os.getpid()
        #: ウィンドウが消える前にページが「インストール中」を読む時間（秒）。
        self.pause = pause
        self.folder = Path(app.instance_path) / FOLDER
        self._lock = threading.Lock()
        #: state: idle、downloading、checking、ready（ダウンロード済み、試合が終わるのを待っている）、installing、failed
        self._state: dict = _fresh("idle")
        self._new: Path | None = None

    @property
    def possible(self) -> bool:
        return self.home is not None

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, state: str, **more) -> None:
        with self._lock:
            self._state = _fresh(state) | more

    def look(self) -> dict | None:
        """最新ビルドを今サイトに聞く（設定ページのボタン）: 新しいもの（{"commit", "date", "url", "ready"}）か None。
        サイトに聞けなければ UpdateError（network）。"""
        try:
            told = self.ask(self.app.config["VAL_PUBLIC"])
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise UpdateError("network", type(error).__name__) from error
        found = release.newer(self.app.config.get("VAL_BUILD"), told, time.time())
        self.app.config["VAL_UPDATE_LOOKED"] = time.time()
        # 作っている途中のビルドはまだ出さない（できたら desktop.look_for_updates が知らせる）
        self.app.config["VAL_UPDATE"] = found if found and found["ready"] else None
        return found

    def press(self) -> dict:
        """持ち主のボタン: ダウンロードを始めるか、（試合中にダウンロードしたものなら）新しいビルドに入れ替える。
        その後の状態を返す。"""
        with self._lock:
            now = self._state["state"]
            busy = now in ("downloading", "checking", "installing")
            if not busy:
                self._state = _fresh("checking")
        if not busy:
            target = self._install if now == "ready" and self._new is not None else self._run
            threading.Thread(target=self._guarded, args=(target,), name="update", daemon=True).start()
        return self.state()

    def _guarded(self, step) -> None:
        try:
            step()
        except UpdateError as error:
            self._set("failed", problem=error.kind, detail=error.detail)
        except Exception as error:  # 何が止めても知らせて、アプリは続ける
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
        with contextlib.suppress(OSError):
            archive.unlink()
        self._install()

    def _install(self) -> None:
        if self.home is None or self._new is None or not (self._new / EXE).is_file():
            raise UpdateError("badfile", "nothing downloaded")
        if self._playing():
            self._set("ready")  # その間に試合が始まった: 持ち主がもう一度押したら入れ替える
            return
        self._set("installing")
        try:
            self.start([str(self._new / EXE), INSTALL, str(self.home), "--wait-pid", str(self.pid)])
        except OSError as error:
            raise UpdateError("nostart", type(error).__name__) from error
        time.sleep(self.pause)  # ウィンドウが消える前に、ページがもう一度「インストール中」を読む
        self.close()


def running_home() -> Path | None:
    """Windows でのビルドしたアプリのフォルダー。ソースやほかの OS では None。"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def wait_for(pid: int, seconds: float = WAIT_FOR_APP) -> bool:
    """プログラム ``pid`` が終わるまで待つ（Windows）: 終わったか。"""
    if sys.platform != "win32" or pid <= 0:
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True  # もう終わっている
    try:
        return kernel.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        kernel.CloseHandle(handle)


def _retry(step, seconds: float = WAIT_FOR_FILE, sleep=time.sleep):
    """``step()``。ファイルがまだ握られていれば（ウイルススキャナー、古いアプリの最後の瞬間）しばらく繰り返す。"""
    until = time.monotonic() + seconds
    while True:
        try:
            return step()
        except PermissionError:
            if time.monotonic() >= until:
                raise
            sleep(0.5)


def replace(source: Path, target: Path, sleep=time.sleep) -> list[str]:
    """``source``（新しいビルドのフォルダー）の中身を全部 ``target``（アプリのフォルダー）
    に置く: target の同じ名前のものは先によけ（``name.old``）、最後に消す。何か失敗したら、
    よけたものを戻してエラーを上げる。``target`` のほかのものには触らない。置いた名前を返す。"""
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
            with contextlib.suppress(OSError):
                remove(target / name)
        for name in aside:
            with contextlib.suppress(OSError):
                (target / (name + ".old")).rename(target / name)
        raise
    for name in aside:
        try:
            remove(target / (name + ".old"))
        except OSError:
            pass  # まだ握られている: 次の起動でアプリが消す（clean）
    return names


def install_over(target: Path, pid: int, source: Path | None = None, start=_start, log=print) -> int:
    """``--install-over`` で起動した新しいコピーがすること: 古いアプリを待ち、その場所に自分を置き、
    そこでアプリを起動する。アプリを起動できたら 0（新しいもの、または失敗して古いものをもう一度）。"""
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
        # VAL_UPDATE_THEN: そのあと開くアプリに渡す追加の引数（ビルド自身のテスト: --check）
        start([str(target / EXE), UPDATED, *os.environ.get("VAL_UPDATE_THEN", "").split()])
    except OSError as error:
        log(f"update: failed ({type(error).__name__}: {error}); the old build is back")
        try:
            start([str(target / EXE)])
        except OSError as again:  # 古いアプリも起動できない: 例外で落ちずにログに残す
            log(f"update: could not start the app ({type(again).__name__}: {again})")
            return 1
    return 0


def clean(folder: Path, home: Path | None, wait: float = 5.0) -> None:
    """起動のあと: 更新が残したもの（データフォルダーの ``update/``、アプリの横の ``.old``）
    を少しあとに消す（新しいビルドを置いたコピーがまだ終わる途中）。"""

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
