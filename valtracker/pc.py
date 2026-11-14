"""What the PC app does on Windows beyond its window (the site cannot do any of it):

- it tells the owner that a match is found while they are in another program: the app's taskbar button lights up
  and, when the owner turned it on, a short sound plays (``Alert``);
- it starts with Windows, minimised, when the owner turned that on (``Autostart``: one value under the owner's own
  ``Run`` key in the registry, written and removed from the settings page).

Made, like the rest of the app, never to get in VALORANT's way:

- nothing is done while the game's window is the one in front: the game tells of the match itself, and a taskbar
  button that asks for attention can bring the taskbar over a game in a borderless window;
- the button's light is taken off again when the game comes to the front, and after LIT seconds at the latest;
- the app's window is never brought to the front and nothing is drawn over the game;
- whether the game is in front is read from the title and the class of the window in front, which Windows keeps
  itself: no message is sent to the game and its process is not opened;
- the sound is the app's own (a short file made once in the app's folder), played by Windows beside the game's
  sound.
"""

from __future__ import annotations

import ctypes
import math
import struct
import sys
import threading
import time
import wave
from pathlib import Path

#: The value's name under the Run key, and the key.
RUN_NAME = "VAL Tracker"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: What the app is started with at Windows' start: minimised (the taskbar), nothing in front of anything.
MINIMISED = "--minimized"
#: The game's window: its title (Windows shows it with spaces after it), and what its class holds (Unreal
#: Engine's: VALORANTUnrealWindow).
GAME_TITLE, GAME_CLASS = "VALORANT", "UnrealWindow"
#: How long the taskbar button stays lit at most (seconds), and how often the window in front is looked at
#: meanwhile.
LIT, LOOK = 12.0, 0.5
#: The sound's file in the app's folder. It is made once and kept, so a changed sound takes a new name, and the
#: names it had before are removed.
SOUND = "match-found-2.wav"
OLD_SOUNDS = ("match-found.wav",)
#: The settings' keys (settings.json) and what they are while the owner has not chosen.
DEFAULTS = {"alert_flash": True, "alert_sound": False}


def chosen(settings) -> dict:
    """The owner's choices for the alert: ``{"alert_flash", "alert_sound"}``."""
    saved = settings.load()
    return {key: saved[key] if isinstance(saved.get(key), bool) else default for key, default in DEFAULTS.items()}


def is_game(title: str, kind: str) -> bool:
    """Whether a window with this title and class is VALORANT's. Any window named as the game counts (when in
    doubt, nothing is done), and so does an Unreal Engine window whose title begins with the game's name."""
    title = title.strip().upper()
    return title == GAME_TITLE or (GAME_CLASS in kind and title.startswith(GAME_TITLE))


def chime(path: Path) -> Path:
    """The alert's sound, made once: two soft bell notes that ring out (about 1.3 s), quiet enough beside a game.

    It begins and ends in silence. The first one (0.45 s) stopped while its second note was still heard, which
    sounds cut off; and some sound cards drop the first and last moments of what they are given."""
    if path.exists():
        return path
    rate = 22050
    lead, ring, tail = 0.04, 1.15, 0.12  # silence, the notes, silence (seconds)
    notes = ((880.0, 0.0), (1318.5, 0.17))  # pitch, and when it is struck
    attack, fade = 0.006, 0.3  # a note's rise, and the end brought down to nothing
    frames = []
    for index in range(int(rate * (lead + ring + tail))):
        at, value = index / rate - lead, 0.0
        if 0 <= at < ring:
            for pitch, start in notes:
                since = at - start
                if since < 0:
                    continue
                turn = 2 * math.pi * pitch * since
                tone = math.sin(turn) + 0.25 * math.sin(2 * turn)  # a little of the octave above: a bell, not a beep
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
        try:
            (path.parent / name).unlink()
        except OSError:
            pass
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
    """The few things asked of Windows (user32, winsound): the tests give their own."""

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
        """The window in front: (its handle, its title, its class). Windows keeps a window's title and class
        itself: reading them sends nothing to the program the window belongs to."""
        handle = self.user.GetForegroundWindow() or 0
        title, kind = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(128)
        if handle:
            self.user.GetWindowTextW(handle, title, 64)
            self.user.GetClassNameW(handle, kind, 128)
        return int(handle), title.value, kind.value

    def flash(self, handle: int, on: bool) -> None:
        """The taskbar button of the window lit (until it comes to the front), or not lit any more."""
        # FLASHW_TRAY | FLASHW_TIMERNOFG: the button only, until the window is in front; FLASHW_STOP
        what = _Flash(ctypes.sizeof(_Flash), handle, 0x2 | 0xC if on else 0, 3 if on else 0, 0)
        self.user.FlashWindowEx(ctypes.byref(what))

    def play(self, path: Path) -> None:
        import winsound

        # the file, without waiting for its end, and nothing at all when it cannot be played
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


class Alert:
    """Tells the owner a match is found (``__call__``: localclient.LocalClient.listen's listener). ``handle``:
    () -> the app's window (0 while there is none); ``windows``: Windows (None elsewhere: nothing is done)."""

    def __init__(self, settings, folder: Path, handle, windows=None, wait=None):
        self.settings, self.folder, self.handle, self.windows = settings, Path(folder), handle, windows
        self.wait = wait or threading.Event().wait
        self._lit = 0  # the light's turn: a newer one ends the watch of the one before

    def __call__(self, moment: str = "found", always: bool = False) -> dict:
        """What was done: ``{"flash", "sound"}``. ``always``: the settings page's 試す (whatever is in front)."""
        done = {"flash": False, "sound": False}
        if self.windows is None:
            return done
        choice = chosen(self.settings)
        try:
            front, title, kind = self.windows.front()
            if is_game(title, kind) and not always:
                return done  # the owner is looking at the game, which tells of the match itself
            mine = int(self.handle() or 0)
            if choice["alert_flash"] and mine and (front != mine or always):
                self.windows.flash(mine, True)
                done["flash"] = True
                self._lit += 1
                threading.Thread(target=self._put_out, args=(mine, self._lit), name="alert", daemon=True).start()
            if choice["alert_sound"]:
                self.windows.play(chime(self.folder / SOUND))
                done["sound"] = True
        except Exception as error:  # noqa: BLE001 - an alert that cannot be given must never end the listening
            print(f"alert: {type(error).__name__}", flush=True)
        return done

    def _put_out(self, mine: int, turn: int) -> None:
        """The light goes out when the game or the app comes to the front, and after LIT seconds at the latest."""
        until = time.monotonic() + LIT
        while time.monotonic() < until and turn == self._lit:
            if self.wait(LOOK):
                break
            try:
                front, title, kind = self.windows.front()
            except Exception:  # noqa: BLE001 - see __call__
                break
            if front == mine or is_game(title, kind):
                break
        if turn == self._lit:
            try:
                self.windows.flash(mine, False)
            except Exception:  # noqa: BLE001 - see __call__
                pass


class Autostart:
    """The app at Windows' start (the owner's own Run key). ``command``: what is started (None when the app is
    not a built one: the source has no file to start); ``registry``: winreg (the tests' own)."""

    def __init__(self, command: str | None = None, registry=None):
        self.command, self.registry = command, registry

    @classmethod
    def here(cls) -> Autostart:
        """For the app as it runs: the built .exe on Windows, else nothing to turn on."""
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return cls()
        import winreg

        return cls(f'"{sys.executable}" {MINIMISED}', winreg)

    @property
    def possible(self) -> bool:
        return self.command is not None and self.registry is not None

    def on(self) -> bool:
        """Whether Windows starts this app (this file, where it is now)."""
        if not self.possible:
            return False
        try:
            with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(self.registry.QueryValueEx(key, RUN_NAME)[0]).casefold() == self.command.casefold()
        except OSError:
            return False

    def set(self, on: bool) -> bool:
        """Turns it on or off; whether it is now as asked."""
        if not self.possible:
            return not on
        registry = self.registry
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                if on:
                    registry.SetValueEx(key, RUN_NAME, 0, registry.REG_SZ, self.command)
                else:
                    try:
                        registry.DeleteValue(key, RUN_NAME)
                    except FileNotFoundError:
                        pass
        except OSError:
            return False
        return self.on() == on


def windows() -> Windows | None:
    """Windows' part, or None where there is none."""
    if sys.platform != "win32":
        return None
    try:
        return Windows()
    except (OSError, AttributeError):
        return None
