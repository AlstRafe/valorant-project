"""Where things are: the repository, and instance/ (this machine's data, never in Git)."""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
INSTANCE = ROOT / "instance"


def deployed_version() -> str:
    """The commit checked out (the VM resets the clone to master's): Home Page's server restarts into new code
    when this changes. Read from .git by hand, without starting git every few seconds."""
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
