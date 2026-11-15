"""VALTracker.exe の入口（PyInstaller、.github/workflows/desktop.yml）。本体は valtracker/desktop.py。

起動に失敗したら、アプリのフォルダーの crash.log に書いてメッセージボックスで知らせる（.exe にはコンソールが無い）。
ビルドの --check のときは、誰も閉じられないボックスを出さずに 1 で終わる。"""

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
except Exception:  # 起動を止めたものは何でも知らせる
    code = _failed()
sys.exit(code)
