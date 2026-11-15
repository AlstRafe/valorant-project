"""ファイルの場所: リポジトリと instance/（このマシンのデータ。Git には入れない）。"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
INSTANCE = ROOT / "instance"


def deployed_version() -> str:
    """チェックアウトしているコミット（VM は master に合わせてリセットする）。
    これが変わると Home Page のサーバーが新しいコードで再起動する。数秒ごとに git を起動しないよう、.git を直接読む。"""
    git = ROOT / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    except OSError:
        pass
    return ""
