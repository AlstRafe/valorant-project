"""The owner's settings, in instance/settings.json: the HenrikDev API key for now.

The environment variable ``VAL_HENRIK_KEY`` wins over the file (for a test run without saving the key).
The file is written readable by its owner only; it never goes into Git (instance/ is ignored).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()


class Settings:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "settings.json"

    def load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

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
