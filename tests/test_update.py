"""The PC app updating itself (update.py): the download, the checks before anything of it is used, the new build
put in place of the old one, and the pages' button."""

import io
import re
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import make_app
from test_desktop import FakeClient

from valtracker import update
from valtracker.localclient import LocalClient
from valtracker.update import UpdateError, Updater

#: The running build (a month old), and the newest one as the site names it (two hours old: built and released).
OLD = {"commit": "a" * 40, "time": int(time.time()) - 30 * 86400, "date": "2026-10-14 10:13"}
NEW = "b" * 40
NEWER = {"commit": NEW, "time": int(time.time()) - 7200}


def build_zip(commit: str = NEW, extra: dict | None = None, top: str = "VALTracker") -> bytes:
    """A release's zip: the app, its stamp, one more file of the build."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as file:
        file.writestr(f"{top}/VALTracker.exe", b"new exe")
        file.writestr(f"{top}/_internal/valtracker/BUILD", f"{commit} 1790100000")
        file.writestr(f"{top}/_internal/python312.dll", b"new dll")
        for name, body in (extra or {}).items():
            file.writestr(name, body)
    return data.getvalue()


class Answer:
    """urlopen's answer, made up."""

    def __init__(self, body: bytes, url: str = "https://release-assets.githubusercontent.com/x.zip", length=None):
        self.body, self.url = io.BytesIO(body), url
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body.read(size)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def fetch(self, answer, url=update.ASSET):
        told = []
        update.fetch(
            url, self.folder / "u" / "app.zip", lambda done, total: told.append((done, total)), lambda *a, **k: answer
        )
        return told

    def test_the_file_and_its_progress(self):
        body = b"x" * 600_000
        told = self.fetch(Answer(body))
        self.assertEqual((self.folder / "u" / "app.zip").read_bytes(), body)
        self.assertEqual(told[-1], (600_000, 600_000))
        self.assertGreater(len(told), 1)

    def test_only_https_and_only_from_github(self):
        for url, answer in (
            ("http://github.com/x.zip", Answer(b"x")),
            (update.ASSET, Answer(b"x", "https://evil.example/x.zip")),
            (update.ASSET, Answer(b"x", "https://notgithubusercontent.com/x.zip")),
            (update.ASSET, Answer(b"x", "http://objects.githubusercontent.com/x.zip")),
        ):
            with self.subTest(url=url, came_from=answer.url), self.assertRaises(UpdateError) as failed:
                self.fetch(answer, url)
            self.assertEqual(failed.exception.kind, "network")

    def test_a_download_cut_off_or_too_large(self):
        with self.assertRaises(UpdateError) as failed:
            self.fetch(Answer(b"x" * 10, length=20))
        self.assertEqual((failed.exception.kind, failed.exception.detail), ("network", "cut off"))
        with self.assertRaises(UpdateError) as failed:
            self.fetch(Answer(b"x", length=update.MAX_ZIP + 1))
        self.assertEqual(failed.exception.kind, "badfile")

    def test_no_network(self):
        def refused(*a, **k):
            raise OSError("no route")

        with self.assertRaises(UpdateError) as failed:
            update.fetch(update.ASSET, self.folder / "app.zip", lambda done, total: None, refused)
        self.assertEqual(failed.exception.kind, "network")


class UnpackTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def unpack(self, body: bytes, commit: str = NEW) -> Path:
        archive = self.folder / "app.zip"
        archive.write_bytes(body)
        return update.unpack(archive, self.folder / "new", commit)

    def test_the_build_the_site_named(self):
        home = self.unpack(build_zip())
        self.assertEqual(home, self.folder / "new" / "VALTracker")
        self.assertEqual((home / "VALTracker.exe").read_bytes(), b"new exe")
        self.assertTrue((home / "_internal" / "python312.dll").is_file())

    def test_another_build_is_not_used(self):
        with self.assertRaises(UpdateError) as failed:
            self.unpack(build_zip("c" * 40))
        self.assertEqual(failed.exception.kind, "notready")  # the release still holds the build before
        self.assertFalse((self.folder / "new").exists())

    def test_what_is_not_the_app_is_not_unpacked(self):
        for name, body in (
            ("a path outside", build_zip(extra={"VALTracker/../../evil.exe": b"x"})),
            ("another folder", build_zip(extra={"Other/file.txt": b"x"})),
            ("a drive", build_zip(extra={"VALTracker/C:/evil.exe": b"x"})),
            ("no app", build_zip(top="Something")),
            ("no stamp", build_zip("not a stamp")),
            ("not a zip", b"<html>Not Found</html>"),
        ):
            with self.subTest(name), self.assertRaises(UpdateError) as failed:
                self.unpack(body)
            self.assertEqual(failed.exception.kind, "badfile")
            self.assertFalse((self.folder / "new").exists())
        self.assertFalse((self.folder.parent / "evil.exe").exists())


class ReplaceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.app, self.new = self.folder / "Apps" / "VALTracker", self.folder / "data" / "update" / "new" / "VALTracker"
        for home, word in ((self.app, b"old"), (self.new, b"new")):
            (home / "_internal" / "valtracker").mkdir(parents=True)
            (home / "VALTracker.exe").write_bytes(word + b" exe")
            (home / "_internal" / "python312.dll").write_bytes(word + b" dll")
        (self.app / "_internal" / "only-in-old.pyd").write_bytes(b"stale")
        (self.app / "notes.txt").write_text("the owner's own", encoding="utf-8")

    def test_the_new_build_takes_the_old_ones_place(self):
        self.assertEqual(update.replace(self.new, self.app), ["VALTracker.exe", "_internal"])
        self.assertEqual((self.app / "VALTracker.exe").read_bytes(), b"new exe")
        self.assertEqual((self.app / "_internal" / "python312.dll").read_bytes(), b"new dll")
        self.assertFalse((self.app / "_internal" / "only-in-old.pyd").exists())  # nothing stale of the old build
        self.assertEqual((self.app / "notes.txt").read_text(encoding="utf-8"), "the owner's own")
        self.assertEqual(sorted(path.name for path in self.app.iterdir()), ["VALTracker.exe", "_internal", "notes.txt"])

    def test_a_failure_puts_the_old_build_back(self):
        with mock.patch("shutil.copytree", side_effect=OSError("disk full")), self.assertRaises(OSError):
            update.replace(self.new, self.app)
        self.assertEqual((self.app / "VALTracker.exe").read_bytes(), b"old exe")
        self.assertEqual((self.app / "_internal" / "python312.dll").read_bytes(), b"old dll")
        self.assertEqual(sorted(path.name for path in self.app.iterdir()), ["VALTracker.exe", "_internal", "notes.txt"])

    def test_a_file_still_held_is_tried_again(self):
        held = {"left": 2}
        rename = Path.rename

        def slow(path, target):
            if path.name == "VALTracker.exe" and held["left"]:
                held["left"] -= 1
                raise PermissionError("in use")
            return rename(path, target)

        with mock.patch.object(Path, "rename", slow):
            update.replace(self.new, self.app, sleep=lambda seconds: None)
        self.assertEqual((self.app / "VALTracker.exe").read_bytes(), b"new exe")

    def test_what_is_left_from_an_update_before_is_cleared(self):
        (self.app / "VALTracker.exe.old").write_bytes(b"older")
        (self.app / "_internal.old").mkdir()
        update.replace(self.new, self.app)
        self.assertFalse((self.app / "VALTracker.exe.old").exists())
        self.assertFalse((self.app / "_internal.old").exists())

    def test_the_new_copy_puts_itself_in_place_and_opens_the_app(self):
        started, said = [], []
        code = update.install_over(self.app, 0, self.new, started.append, said.append)
        self.assertEqual(code, 0)
        self.assertEqual(started, [[str(self.app / "VALTracker.exe"), "--updated"]])
        self.assertEqual((self.app / "VALTracker.exe").read_bytes(), b"new exe")
        self.assertIn("replaced VALTracker.exe, _internal", said[0])

    def test_after_a_failure_the_old_app_is_opened_again(self):
        started, said = [], []
        with mock.patch("shutil.copytree", side_effect=OSError("disk full")):
            code = update.install_over(self.app, 0, self.new, started.append, said.append)
        self.assertEqual((code, started), (0, [[str(self.app / "VALTracker.exe")]]))
        self.assertEqual((self.app / "VALTracker.exe").read_bytes(), b"old exe")
        self.assertIn("the old build is back", said[0])

    def test_a_folder_that_is_not_the_apps_is_left_alone(self):
        started = []
        other = self.folder / "Documents"
        other.mkdir()
        (other / "report.txt").write_text("mine", encoding="utf-8")
        self.assertEqual(update.install_over(other, 0, self.new, started.append, lambda words: None), 1)
        self.assertEqual(update.install_over(self.new, 0, self.new, started.append, lambda words: None), 1)
        self.assertEqual(started, [])
        self.assertEqual([path.name for path in other.iterdir()], ["report.txt"])

    def test_the_leftovers_are_removed_at_the_next_start(self):
        data = self.folder / "data"
        (self.app / "_internal.old").mkdir()
        (self.app / "VALTracker.exe.old").write_bytes(b"x")
        update.clean(data, self.app, wait=0)
        for thread in threading.enumerate():
            if thread.name == "update-clean":
                thread.join(5)
        self.assertFalse((data / "update").exists())
        self.assertEqual(sorted(path.name for path in self.app.iterdir()), ["VALTracker.exe", "_internal", "notes.txt"])


class UpdaterTest(unittest.TestCase):
    """The steps as the app runs them, and the pages' button."""

    def setUp(self):
        self.env = mock.patch.dict("os.environ", {"VAL_COLLECT_SECONDS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        lockfile = self.folder / "lockfile"
        lockfile.write_text("Riot Client:1234:5555:secret:https", encoding="utf-8")
        self.local = LocalClient(lockfile, self.folder / "none.log", ask=FakeClient())
        self.app, _, self.data = make_app(local=self.local)
        self.addCleanup(self.app._test_folder.cleanup)
        self.app.config["VAL_BUILD"] = OLD
        self.home = self.folder / "Apps" / "VALTracker"
        self.home.mkdir(parents=True)
        (self.home / "VALTracker.exe").write_bytes(b"old exe")
        self.told, self.where = NEWER, ""
        self.zip = build_zip()
        self.started, self.closed, self.fetched = [], [], []
        self.updater = self.app.config["VAL_UPDATER"] = Updater(
            self.app,
            self.home,
            lambda: self.where,
            lambda: self.closed.append(True),
            ask=lambda site: self.told,
            fetch=self.fetch,
            start=self.started.append,
            pid=4321,
            pause=0,
        )
        self.client = self.app.test_client()
        self.client.set_cookie("lang", "ja")

    def fetch(self, url, target, told):
        self.fetched.append(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.zip)
        told(len(self.zip) // 2, len(self.zip))
        self.during()
        told(len(self.zip), len(self.zip))

    def during(self):
        """While the file comes."""

    def press(self) -> dict:
        self.updater.press()
        for thread in threading.enumerate():
            if thread.name == "update":
                thread.join(10)
        return self.updater.state()

    def test_one_press_downloads_checks_and_hands_over_to_the_new_build(self):
        state = self.press()
        self.assertEqual(state["state"], "installing")
        self.assertEqual(self.fetched, [update.ASSET])
        new = self.data / "update" / "new" / "VALTracker" / "VALTracker.exe"
        self.assertEqual(self.started, [[str(new), "--install-over", str(self.home), "--wait-pid", "4321"]])
        self.assertEqual(new.read_bytes(), b"new exe")
        self.assertEqual(self.closed, [True])
        self.assertFalse((self.data / "update" / "VALTracker-windows.zip").exists())

    def stopped(self, kind: str):
        state = self.press()
        self.assertEqual((state["state"], state["problem"]), ("failed", kind))
        self.assertEqual((self.started, self.closed), ([], []))

    def test_nothing_during_agent_select_or_a_match(self):
        for where in ("pregame", "ingame", "found"):
            self.where = where
            self.stopped("playing")
        self.assertEqual(self.fetched, [])

    def test_this_is_the_newest(self):
        self.told = {"commit": OLD["commit"], "time": OLD["time"]}
        self.stopped("nonewer")

    def test_the_newest_build_is_still_being_made(self):
        self.told = {"commit": NEW, "time": int(time.time()) - 60}
        state = self.press()
        self.assertEqual((state["state"], state["problem"], self.fetched), ("failed", "notready", []))

    def test_the_release_still_holds_the_build_before(self):
        self.zip = build_zip("c" * 40)
        self.stopped("notready")

    def test_a_file_that_is_not_the_app(self):
        self.zip = b"<html>rate limited</html>"
        self.stopped("badfile")

    def test_the_site_cannot_be_asked(self):
        def no_site(site):
            raise OSError("down")

        self.updater.ask = no_site
        self.stopped("network")

    def test_a_folder_that_cannot_be_written(self):
        with mock.patch.object(update, "writable", return_value=False):
            self.stopped("unwritable")

    def test_a_new_build_that_cannot_be_started_leaves_the_app_open(self):
        def refused(command):
            raise OSError("blocked")

        self.updater.start = refused
        state = self.press()
        self.assertEqual((state["state"], state["problem"], self.closed), ("failed", "nostart", []))

    def test_from_the_source_there_is_nothing_to_replace(self):
        self.updater.home = None
        self.assertFalse(self.updater.possible)
        self.stopped("notbuilt")

    def test_a_match_that_began_meanwhile_waits_for_another_press(self):
        self.during = lambda: setattr(self, "where", "pregame")
        self.assertEqual(self.press()["state"], "ready")
        self.assertEqual((self.started, self.closed), ([], []))
        self.assertEqual(self.press()["state"], "ready")  # still playing: nothing yet
        self.where = "menus"
        self.assertEqual(self.press()["state"], "installing")
        self.assertEqual(len(self.fetched), 1)  # not downloaded again
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.closed, [True])

    def test_the_pages_button(self):
        self.app.config["VAL_UPDATE"] = {"commit": NEW, "date": "2026-11-13 21:00", "url": "https://x", "ready": True}
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('action="/app/update"', page)
        self.assertIn("いますぐ更新", page)
        self.assertIn('data-update="/app/update/state"', page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        answer = self.client.post("/app/update", data={"csrf": token}, headers={"Accept": "application/json"})
        for thread in threading.enumerate():
            if thread.name == "update":
                thread.join(10)
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.get_json()["possible"])
        state = self.client.get("/app/update/state").get_json()
        self.assertEqual((state["state"], state["busy"]), ("installing", True))
        self.assertIn("更新中です", state["words"])

    def test_the_page_says_why_it_stopped(self):
        self.where = "ingame"
        self.app.config["VAL_UPDATE"] = {"commit": NEW, "date": "2026-11-13 21:00", "url": "https://x", "ready": True}
        self.stopped("playing")
        state = self.client.get("/app/update/state").get_json()
        self.assertEqual((state["failed"], state["busy"]), (True, False))
        self.assertIn("試合中は更新できません", state["words"])
        self.assertIn("試合中は更新できません", self.client.get("/").get_data(as_text=True))

    def test_the_download_is_told_in_percent(self):
        self.updater._set("downloading", done=30, total=120)
        self.assertEqual(self.client.get("/app/update/state").get_json()["words"], "ダウンロード中… 25%")

    def test_without_a_script_the_button_goes_to_the_settings(self):
        token = re.search(r'name="csrf" value="([^"]+)"', self.client.get("/settings").get_data(as_text=True)).group(1)
        self.where = "ingame"
        answer = self.client.post("/app/update", data={"csrf": token})
        self.assertEqual((answer.status_code, answer.headers["Location"]), (303, "/settings#app"))

    def test_looking_for_a_newer_build_from_the_settings(self):
        token = re.search(r'name="csrf" value="([^"]+)"', self.client.get("/settings").get_data(as_text=True)).group(1)
        page = self.client.post("/settings", data={"csrf": token, "do": "look"}).get_data(as_text=True)
        self.assertIn("アップデートがあります。上の「いますぐ更新」から更新できます。", page)
        self.assertEqual(self.app.config["VAL_UPDATE"]["commit"], NEW)
        self.told = {"commit": OLD["commit"], "time": OLD["time"]}
        page = self.client.post("/settings", data={"csrf": token, "do": "look"}).get_data(as_text=True)
        self.assertIn("最新バージョンです。", page)
        self.assertIsNone(self.app.config["VAL_UPDATE"])

    def test_the_app_says_once_that_it_was_updated(self):
        self.app.config["VAL_UPDATED"] = True
        self.assertIn("アップデートしました。", self.client.get("/").get_data(as_text=True))
        self.assertNotIn("アップデートしました。", self.client.get("/").get_data(as_text=True))

    def test_the_source_and_the_site_have_no_button(self):
        self.updater.home = None
        self.app.config["VAL_UPDATE"] = {"commit": NEW, "date": "2026-11-13 21:00", "url": "https://x", "ready": True}
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('action="/app/update"', page)
        self.assertIn("アプリを閉じてから、今のフォルダーに上書き", page)
        site, _, _ = make_app()
        self.addCleanup(site._test_folder.cleanup)
        client = site.test_client()
        self.assertEqual(client.get("/app/update/state").status_code, 404)
        token = re.search(r'name="csrf" value="([^"]+)"', client.get("/settings").get_data(as_text=True)).group(1)
        self.assertEqual(client.post("/app/update", data={"csrf": token}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
