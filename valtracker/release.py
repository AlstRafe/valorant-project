"""PC アプリの最新ビルドはどれか。

サイトが答える（/app/latest）。動いているクローンの、アプリのビルド元を最後に変えたコミット。
（これを書いたときリポジトリは非公開で、サインインしていない人には GitHub のリリースが 404 だった。
2026-11-13 から公開だが、今もサイトに聞く: 答えが小さく、API の制限も無い。）
desktop.yml は各ビルドに同じ答えを刻み（``python -m valtracker.release <file>`` が valtracker/BUILD を書く）、
アプリが 2 つを比べる（desktop.look_for_updates）。"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from valtracker.paths import ROOT

#: アプリのビルド元: desktop.yml の ``paths``。どれも変えないコミットでは新しいアプリはビルドされない。
PATHS = ("valtracker", "desktop", "requirements*.txt", ".github/workflows/desktop.yml")
#: 最新ビルドのダウンロード先（持ち主はブラウザーで GitHub にサインインしている）。
PAGE = "https://github.com/AlstRafe/valorant-project/releases/tag/desktop"
#: コミットからこの時間が過ぎて初めて新しいビルドとして知らせる（desktop.yml のビルドと公開に 10 分ほどかかる）。
BUILT_AFTER = 20 * 60
#: サイトがクローンを読み直す最短の間隔。
KEPT = 300.0
_HASH = re.compile(r"[0-9a-f]{40}")
_kept: dict = {"at": 0.0, "found": None}
_lock = threading.Lock()


def source(folder: Path = ROOT) -> dict | None:
    """``folder`` のクローンでアプリを最後に変えたコミット: {"commit", "time": 作った時刻（1970 年からの秒）}。
    クローンか git が無ければ None（ビルドしたアプリ、ファイルのコピー）。"""
    try:
        done = subprocess.run(  # 決まった引数
            ["git", "-C", str(folder), "log", "-1", "--format=%H %ct", "--", *PATHS],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse(done.stdout)


def parse(text: str) -> dict | None:
    """ "<commit> <time>"（git の答え、valtracker/BUILD）を {"commit", "time"} に。その形でなければ None。"""
    parts = text.split()
    if len(parts) != 2 or not _HASH.fullmatch(parts[0]) or not parts[1].isdigit():
        return None
    return {"commit": parts[0], "time": int(parts[1])}


def latest(folder: Path = ROOT, now: float | None = None) -> dict | None:
    """source() を KEPT 秒に 1 回まで読み直したもの: サイトが /app/latest で答える内容。"""
    now = time.monotonic() if now is None else now
    with _lock:
        if _kept["found"] is None or now - _kept["at"] >= KEPT:
            _kept["found"], _kept["at"] = source(folder), now
        return _kept["found"]


def day(when: int) -> str:
    """この PC の時計でのビルドの時刻: 2026-11-13 09:41。"""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(when))


def read_build(file: Path) -> dict | None:
    """ビルドしたアプリのスタンプ（valtracker/BUILD）: {"commit", "time", "date"}。ソースから動かしているなら None。"""
    try:
        found = parse(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found | {"date": day(found["time"])} if found else None


def newer(build: dict | None, told: object, now: float) -> dict | None:
    """サイトが ``told``（/app/latest）で示したものがこれより新しいビルドなら: {"commit", "date", "url": PAGE,
    "ready": desktop.yml が公開を済ませているはずか}。これが最新、ビルドしたアプリでない、答えがその形でないなら None。
    リンクはいつも PAGE（答えの中身は開かない）。"""
    if not build or not isinstance(told, dict):
        return None
    commit, when = told.get("commit"), told.get("time")
    if (
        not isinstance(commit, str)
        or not _HASH.fullmatch(commit)
        or isinstance(when, bool)
        or not isinstance(when, int)
    ):
        return None
    if commit == build["commit"] or when <= build["time"]:
        return None
    return {"commit": commit, "date": day(when), "url": PAGE, "ready": now - when >= BUILT_AFTER}


if __name__ == "__main__":
    # desktop.yml: 作っているビルドのスタンプ
    found = source()
    if not found or len(sys.argv) != 2:
        sys.exit("No commit found for the app's files (a clone with its history is needed).")
    Path(sys.argv[1]).write_text(f"{found['commit']} {found['time']}", encoding="ascii")
    print(f"{found['commit']} {found['time']} ({day(found['time'])})")
