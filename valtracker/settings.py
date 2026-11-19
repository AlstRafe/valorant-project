"""持ち主の設定（instance/settings.json）: HenrikDev の API キー（riot.py のサインインは riot.json）。

環境変数 ``VAL_HENRIK_KEY`` があればファイルより優先する（キーを保存せずに試す用）。
ファイルは持ち主だけが読めるように書き、Git には入れない（instance/ は無視される）。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from valtracker.util import as_dict

_lock = threading.Lock()


class Settings:
    def __init__(self, folder: Path, name: str = "settings.json"):
        self.path = Path(folder) / name

    def load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return as_dict(value)

    def save(self, **changes) -> None:
        with _lock:
            value = {**self.load(), **changes}
            value = {key: item for key, item in value.items() if item not in ("", None)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)

    def henrik_key(self) -> str:
        return os.environ.get("VAL_HENRIK_KEY", "").strip() or str(self.load().get("henrik_key", "")).strip()
