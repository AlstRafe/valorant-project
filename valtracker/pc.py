"""PC アプリがウィンドウの外で Windows にすること（サイトにはどれもできない）:

- 持ち主がほかのプログラムにいる間にマッチが成立したら知らせる: タスクバーのアプリのボタンを光らせ、
  オンにしていれば短い音を鳴らす（``Alert``）
- オンにしていれば Windows の起動時に最小化して起動する（``Autostart``: レジストリの持ち主自身の ``Run`` キーの値 1 つ。
  設定ページから書いて消す）

アプリのほかの部分と同じく、VALORANT の邪魔を決してしないように作る:

- ゲームのウィンドウが前面にある間は何もしない: マッチ成立はゲーム自身が知らせるし、
  注意を引くタスクバーのボタンはボーダーレスウィンドウのゲームの上にタスクバーを出してしまうことがある
- ゲームが前面に来たら、遅くとも LIT 秒後にはボタンの光を消す
- アプリのウィンドウを前に出さず、ゲームの上に何も描かない
- ゲームが前面かどうかは、Windows 自身が持つ前面ウィンドウのタイトルとクラスで判断する: ゲームにメッセージを送らず、
  プロセスも開かない
- 音はアプリ自身のもの（アプリのフォルダーに 1 回作る短いファイル）で、Windows がゲームの音と並べて鳴らす"""

from __future__ import annotations

import contextlib
import ctypes
import math
import struct
import sys
import threading
import time
import wave
from pathlib import Path

#: Run キーの値の名前と、キー。
RUN_NAME = "VAL Tracker"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: Windows の起動時にアプリを始める引数: 最小化（タスクバー）、何の前にも出ない。
MINIMISED = "--minimized"
#: ゲームのウィンドウ: タイトル（Windows は後ろに空白を付けて出す）と、
#: クラスに含まれるもの（Unreal Engine の VALORANTUnrealWindow）。
GAME_TITLE, GAME_CLASS = "VALORANT", "UnrealWindow"
#: タスクバーのボタンを光らせておく最長の秒数と、その間に前面のウィンドウを見る間隔。
LIT, LOOK = 12.0, 0.5
#: アプリのフォルダーの音のファイル。1 回作って残すので、音を変えたら名前を変え、前の名前のものは消す。
SOUND = "match-found-2.wav"
OLD_SOUNDS = ("match-found.wav",)
#: 設定のキー（settings.json）と、持ち主が選ぶまでの値。
DEFAULTS = {"alert_flash": True, "alert_sound": False}


def chosen(settings) -> dict:
    """通知についての持ち主の選択: ``{"alert_flash", "alert_sound"}``。"""
    saved = settings.load()
    return {key: saved[key] if isinstance(saved.get(key), bool) else default for key, default in DEFAULTS.items()}


def is_game(title: str, kind: str) -> bool:
    """このタイトルとクラスのウィンドウが VALORANT のものか。ゲームの名前のウィンドウはどれも数え（迷ったら何もしない）
    、タイトルがゲームの名前で始まる Unreal Engine のウィンドウも数える。"""
    title = title.strip().upper()
    return title == GAME_TITLE or (GAME_CLASS in kind and title.startswith(GAME_TITLE))


def chime(path: Path) -> Path:
    """1 回作る通知音: 余韻のある柔らかいベルの音 2 つ（約 1.3 秒）。ゲームの横で鳴っても控えめな音量。

    無音で始まり無音で終わる。最初のもの（0.45 秒）は 2 つ目の音がまだ聞こえているうちに止まり、切れたように聞こえた。
    また、渡された音の最初と最後を落とすサウンドカードもある。"""
    if path.exists():
        return path
    rate = 22050
    lead, ring, tail = 0.04, 1.15, 0.12  # 無音、音、無音（秒）
    notes = ((880.0, 0.0), (1318.5, 0.17))  # 音の高さと、鳴らす時刻
    attack, fade = 0.006, 0.3  # 音の立ち上がりと、最後はゼロまで下げる
    frames = []
    for index in range(int(rate * (lead + ring + tail))):
        at, value = index / rate - lead, 0.0
        if 0 <= at < ring:
            for pitch, start in notes:
                since = at - start
                if since < 0:
                    continue
                turn = 2 * math.pi * pitch * since
                tone = math.sin(turn) + 0.25 * math.sin(2 * turn)  # 1 オクターブ上を少し: ビープではなくベルに
                value += min(1.0, since / attack) * tone * math.exp(-5.5 * since)
            left = ring - at
            if left < fade:
                value *= 0.5 - 0.5 * math.cos(math.pi * left / fade)
        frames.append(struct.pack("<h", int(max(-1.0, min(1.0, value * 0.22)) * 32767)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(b"".join(frames))
    for name in OLD_SOUNDS:
        with contextlib.suppress(OSError):
            (path.parent / name).unlink()
    return path


class _Flash(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


class Windows:
    """Windows に頼むわずかなこと（user32、winsound）: テストは自前のものを渡す。"""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        self.user.GetWindowTextW.argtypes = self.user.GetClassNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        self.user.FlashWindowEx.argtypes = [ctypes.c_void_p]

    def front(self) -> tuple[int, str, str]:
        """前面のウィンドウ: （ハンドル, タイトル, クラス）。ウィンドウのタイトルとクラスは Windows 自身が持つので、
        読んでもウィンドウのプログラムには何も送らない。"""
        handle = self.user.GetForegroundWindow() or 0
        title, kind = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        if handle:
            self.user.GetWindowTextW(handle, title, 64)
            self.user.GetClassNameW(handle, kind, 128)
        return int(handle), title.value, kind.value

    def flash(self, handle: int, on: bool) -> None:
        """ウィンドウのタスクバーのボタンを光らせる（前面に来るまで）か、光を消す。"""
        # FLASHW_TRAY | FLASHW_TIMERNOFG: ボタンだけ、ウィンドウが前面に来るまで。FLASHW_STOP
        what = _Flash(ctypes.sizeof(_Flash), handle, 0x2 | 0xC if on else 0, 3 if on else 0, 0)
        self.user.FlashWindowEx(ctypes.byref(what))

    def play(self, path: Path) -> None:
        import winsound

        # ファイルを鳴らす。終わりを待たず、鳴らせなければ何もしない
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class Alert:
    """マッチ成立を持ち主に知らせる（``__call__``: localclient.LocalClient.listen の通知先）。
    ``handle``: () -> アプリのウィンドウ（無い間は 0）。``windows``: Windows（ほかの OS では None で、何もしない）。"""

    def __init__(self, settings, folder: Path, handle, windows=None, wait=None):
        self.settings, self.folder, self.handle, self.windows = settings, Path(folder), handle, windows
        self.wait = wait or threading.Event().wait
        self._lit = 0  # 光らせた回の番号: 新しい回が来たら前の回の見張りは終わる

    def __call__(self, moment: str = "found", always: bool = False) -> dict:
        """したこと: ``{"flash", "sound"}``。``always``: 設定ページの「試す」（前面に何があっても）。"""
        done = {"flash": False, "sound": False}
        if self.windows is None:
            return done
        choice = chosen(self.settings)
        try:
            front, title, kind = self.windows.front()
            if is_game(title, kind) and not always:
                return done  # 持ち主はゲームを見ている。マッチ成立はゲーム自身が知らせる
            mine = int(self.handle() or 0)
            if choice["alert_flash"] and mine and (front != mine or always):
                self.windows.flash(mine, True)
                done["flash"] = True
                self._lit += 1
                threading.Thread(target=self._put_out, args=(mine, self._lit), name="alert", daemon=True).start()
            if choice["alert_sound"]:
                self.windows.play(chime(self.folder / SOUND))
                done["sound"] = True
        except Exception as error:  # 通知できなくても、待ち受けを決して終わらせない
            print(f"alert: {type(error).__name__}", flush=True)
        return done

    def _put_out(self, mine: int, turn: int) -> None:
        """ゲームかアプリが前面に来たら、遅くとも LIT 秒後には光を消す。"""
        until = time.monotonic() + LIT
        while time.monotonic() < until and turn == self._lit:
            if self.wait(LOOK):
                break
            try:
                front, title, kind = self.windows.front()
            except Exception:  # __call__ を参照
                break
            if front == mine or is_game(title, kind):
                break
        if turn == self._lit:
            with contextlib.suppress(Exception):  # __call__ を参照
                self.windows.flash(mine, False)


class Autostart:
    """Windows 起動時のアプリ（持ち主自身の Run キー）。``command``: 起動するもの（ビルドしたアプリでなければ None。
    ソースには起動するファイルが無い）。``registry``: winreg（テストは自前のもの）。"""

    def __init__(self, command: str | None = None, registry=None):
        self.command, self.registry = command, registry

    @classmethod
    def here(cls) -> Autostart:
        """動いているアプリ用: Windows ならビルドした .exe、ほかではオンにするものが無い。"""
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return cls()
        import winreg

        return cls(f'"{sys.executable}" {MINIMISED}', winreg)

    @property
    def possible(self) -> bool:
        return self.command is not None and self.registry is not None

    def on(self) -> bool:
        """Windows がこのアプリ（今の場所のこのファイル）を起動するか。"""
        if not self.possible:
            return False
        try:
            with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(self.registry.QueryValueEx(key, RUN_NAME)[0]).casefold() == self.command.casefold()
        except OSError:
            return False

    def set(self, on: bool) -> bool:
        """オン・オフを切り替える。頼んだとおりになったか。"""
        if not self.possible:
            return not on
        registry = self.registry
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                if on:
                    registry.SetValueEx(key, RUN_NAME, 0, registry.REG_SZ, self.command)
                else:
                    with contextlib.suppress(FileNotFoundError):
                        registry.DeleteValue(key, RUN_NAME)
        except OSError:
            return False
        return self.on() == on


def windows() -> Windows | None:
    """Windows 側。無ければ None。"""
    if sys.platform != "win32":
        return None
    try:
        return Windows()
    except (OSError, AttributeError):
        return None
