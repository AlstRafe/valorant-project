"""VALTracker.exe's start (PyInstaller, .github/workflows/desktop.yml): the desktop app, valtracker/desktop.py.

A start that fails is written to crash.log in the app's folder and told in a message box (the .exe has no console),
or, for the build's --check, ends with 1 instead of a box nobody can close.
"""

import os
import sys
import traceback
from pathlib import Path


def _failed() -> int:
    folder = Path(os.environ.get("VAL_DESKTOP_HOME") or Path(os.environ.get("LOCALAPPDATA") or ".") / "VAL Tracker")
    log = folder / "crash.log"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        pass
    if "--check" in sys.argv:
        sys.stderr and sys.stderr.write(traceback.format_exc())
        return 1
    if sys.platform == "win32":
        import ctypes

        words = f"VAL Tracker を開けませんでした。\n{log}\nに理由を書きました。"
        ctypes.windll.user32.MessageBoxW(None, words, "VAL Tracker", 0x10)  # MB_ICONERROR
    return 1


try:
    from valtracker.desktop import main

    code = main()
except Exception:  # noqa: BLE001 - anything that stops the start is told
    code = _failed()
sys.exit(code)
