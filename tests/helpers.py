"""What the tests share: the outside APIs' answers from tests/fixtures/, and an app that uses them."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from valtracker import create_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeFetch:
    """Answers like HenrikDev and valorant-api.com would, from the fixtures; ``fail`` makes everything fail
    with that status; every asked URL is in ``asked``."""

    def __init__(self):
        self.asked: list[tuple[str, dict]] = []
        self.fail = None
        self.henrik_status = None

    def __call__(self, url: str, headers: dict) -> tuple[int, bytes]:
        self.asked.append((url, headers))
        if self.fail is not None:
            return self.fail, b""
        if url.startswith("https://api.henrikdev.xyz/"):
            if self.henrik_status:
                return self.henrik_status, json.dumps({"status": self.henrik_status, "errors": []}).encode()
            if "/v2/account/" in url:
                return 200, fixture("henrik_account.json")
            if "/v3/mmr/" in url:
                return 200, fixture("henrik_mmr.json")
            if "/v4/matches/" in url:
                return 200, fixture("henrik_matches.json")
            if "/v4/match/" in url:
                matches = json.loads(fixture("henrik_matches.json"))["data"]
                wanted = url.rsplit("/", 1)[-1]
                for match in matches:
                    if match["metadata"]["match_id"] == wanted:
                        return 200, json.dumps({"status": 200, "data": match}).encode()
                return 404, b"{}"
        if url.startswith("https://valorant-api.com/v1/agents"):
            return 200, fixture("content_agents.json")
        if url.startswith("https://valorant-api.com/v1/maps"):
            return 200, fixture("content_maps.json")
        if url.startswith("https://valorant-api.com/v1/competitivetiers"):
            return 200, fixture("content_tiers.json")
        if url.startswith("https://media.valorant-api.com/"):
            return 200, PNG
        return 404, b""

    def henrik_calls(self) -> list[str]:
        return [url for url, _ in self.asked if url.startswith("https://api.henrikdev.xyz/")]


def make_app(key: str = "HDEV-test-key", mount: str = ""):
    """(app, fetch, instance folder) with a fresh instance/ in a temporary folder."""
    folder = tempfile.TemporaryDirectory()
    fetch = FakeFetch()
    app = create_app(mount=mount, instance=Path(folder.name), fetch=fetch)
    app.config["TESTING"] = True
    if key:
        app.config["VAL_SETTINGS"].save(henrik_key=key)
    app._test_folder = folder  # removed with the app
    return app, fetch, Path(folder.name)
