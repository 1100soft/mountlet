from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mountlet import core
from mountlet.cloud_browser import (
    BrowserEntry,
    CloudBrowserBackend,
    TransferItem,
    join_browser_path,
    normalize_browser_path,
    parent_browser_path,
    remote_target,
)
from mountlet.cloud_browser_ui import CHILD_FOLDER_PREFETCH_LIMIT, CompactCloudBrowser, cascade_position


def _remote(name: str = "Docs") -> core.RemoteInfo:
    return core.RemoteInfo(name, name, "Drive", "drive", f"/mnt/{name}")


class CloudBrowserTests(unittest.TestCase):
    def test_paths_are_remote_relative_and_cannot_escape(self):
        self.assertEqual(normalize_browser_path("/Projects/../Photos"), "Photos")
        self.assertEqual(join_browser_path("Photos", "2026"), "Photos/2026")
        self.assertEqual(parent_browser_path("Photos/2026"), "Photos")
        self.assertEqual(remote_target(_remote(), "Photos/2026"), "Docs:/Photos/2026")

    def test_current_path_is_persisted_per_remote(self):
        with tempfile.TemporaryDirectory() as tempdir:
            state = Path(tempdir) / "browser.json"
            cache = Path(tempdir) / "cache"
            backend = CloudBrowserBackend(state_path=state, cache_root=cache)
            backend.remember_path("Docs", "Projects/Current")

            loaded = CloudBrowserBackend(state_path=state, cache_root=cache)

        self.assertEqual(loaded.current_path("Docs"), "Projects/Current")

    def test_listing_maps_rclone_json_to_sorted_entries(self):
        response = SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                [
                    {"Name": "z.txt", "Size": 12, "IsDir": False, "ModTime": "2026-01-02T03:04:00Z"},
                    {"Name": "Folder", "Size": -1, "IsDir": True},
                ]
            ),
        )
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch("mountlet.cloud_browser.subprocess.run", return_value=response) as run:
                    entries = backend.list_entries(_remote(), "Projects")

        self.assertEqual([entry.name for entry in entries], ["Folder", "z.txt"])
        self.assertEqual(entries[1].path, "Projects/z.txt")
        self.assertIn("lsjson", run.call_args.args[0])

    def test_transfer_uses_copyto_for_file_between_remotes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            source = _remote("Source")
            destination = _remote("Target")
            item = TransferItem("Source", "Reports/a.txt", "a.txt", False)
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    backend.transfer([item], {"Source": source}, destination, "Inbox", move=False)

        run.assert_called_once_with("rclone", "copyto", "Source:/Reports/a.txt", "Target:/Inbox/a.txt")

    def test_delete_uses_deletefile_and_purge(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entries = [
                BrowserEntry("a.txt", "Reports/a.txt", False),
                BrowserEntry("Old", "Reports/Old", True),
            ]
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    backend.delete_entries(_remote(), entries)

        self.assertEqual(
            run.call_args_list,
            [
                mock.call("rclone", "deletefile", "Docs:/Reports/a.txt"),
                mock.call("rclone", "purge", "Docs:/Reports/Old"),
            ],
        )

    def test_create_folder_uses_remote_current_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    backend.create_folder(_remote(), "Reports", "New")

        run.assert_called_once_with("rclone", "mkdir", "Docs:/Reports/New")

    def test_create_folder_rejects_nested_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with self.assertRaisesRegex(RuntimeError, "single folder name"):
                backend.create_folder(_remote(), "", "one/two")

    def test_offline_file_is_indicated_by_managed_copy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            path = backend.offline_path("Docs", "Reports/a.txt")
            path.parent.mkdir(parents=True)
            path.write_text("offline", encoding="utf-8")

            self.assertTrue(backend.is_offline("Docs", "Reports/a.txt"))
            backend.remove_offline("Docs", "Reports/a.txt")
            self.assertFalse(backend.is_offline("Docs", "Reports/a.txt"))

    def test_completed_offline_folder_can_be_browsed_without_rclone(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            directory = backend.offline_path("Docs", "Reports")
            directory.mkdir(parents=True)
            (directory / "a.txt").write_text("offline", encoding="utf-8")
            (directory / ".mountlet-offline").touch()
            with mock.patch.object(backend, "_rclone", side_effect=RuntimeError("rclone was not found")):
                entries = backend.list_entries(_remote(), "Reports")

        self.assertEqual([entry.name for entry in entries], ["a.txt"])

    def test_browser_cascades_to_side_with_room_and_clamps_height(self):
        right = cascade_position((100, 100, 300, 400), 180, (0, 0, 1200, 800), (500, 390))
        left = cascade_position((800, 100, 300, 400), 700, (0, 0, 1200, 800), (500, 390))

        self.assertEqual(right, (408, 180))
        self.assertEqual(left, (292, 410))

    def test_browser_entry_is_immutable_transfer_metadata(self):
        entry = BrowserEntry("a.txt", "Folder/a.txt", False, 42)

        self.assertEqual(entry.size, 42)

    def test_background_listing_is_cached_even_when_remote_is_not_selected(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._folder_cache = {}
        browser._loads_pending = {("Docs", "Reports")}
        browser.remote = None
        entries = [BrowserEntry("a.txt", "Reports/a.txt", False)]

        browser._listing_ready("Docs", "Reports", entries, "")

        self.assertEqual(browser._folder_cache[("Docs", "Reports")], entries)
        self.assertNotIn(("Docs", "Reports"), browser._loads_pending)

    def test_cached_folder_refresh_avoids_background_request(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports"
        entries = [BrowserEntry("a.txt", "Reports/a.txt", False)]
        browser._folder_cache = {("Docs", "Reports"): entries}
        browser._loads_pending = set()
        browser.title = mock.Mock()
        browser.path_field = mock.Mock()
        browser.up_button = mock.Mock()
        browser.root_button = mock.Mock()
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._load_folder = mock.Mock()

        browser.refresh(force=False)

        browser._display_entries.assert_called_once_with(entries)
        browser._load_folder.assert_not_called()

    def test_pending_folder_refresh_shows_loading_message(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports"
        browser._folder_cache = {}
        browser._loads_pending = {("Docs", "Reports")}
        browser.title = mock.Mock()
        browser.path_field = mock.Mock()
        browser.up_button = mock.Mock()
        browser.root_button = mock.Mock()
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._load_folder = mock.Mock()

        browser.refresh(force=False)

        browser.status.setText.assert_called_once_with("Loading…")
        browser._display_entries.assert_not_called()
        browser._load_folder.assert_not_called()

    def test_go_root_remembers_remote_root(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports/Current"
        browser.backend = mock.Mock()
        browser.refresh = mock.Mock()

        browser.go_root()

        self.assertEqual(browser.path, "")
        browser.backend.remember_path.assert_called_once_with("Docs", "")
        browser.refresh.assert_called_once_with()

    def test_prefetch_child_folders_schedules_uncached_displayed_folders(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._folder_cache = {("Docs", "Projects/Cached"): []}
        browser._loads_pending = {("Docs", "Projects/Pending")}
        browser._load_folder = mock.Mock()
        entries = [
            BrowserEntry("Cached", "Projects/Cached", True),
            BrowserEntry("Pending", "Projects/Pending", True),
            BrowserEntry("Ready", "Projects/Ready", True),
            BrowserEntry("a.txt", "Projects/a.txt", False),
        ]

        browser._prefetch_child_folders(entries)

        browser._load_folder.assert_called_once_with(browser.remote, "Projects/Ready")

    def test_prefetch_child_folders_is_bounded(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._folder_cache = {}
        browser._loads_pending = set()
        browser._load_folder = mock.Mock()
        entries = [BrowserEntry(f"Folder{i}", f"Folder{i}", True) for i in range(CHILD_FOLDER_PREFETCH_LIMIT + 5)]

        browser._prefetch_child_folders(entries)

        self.assertEqual(browser._load_folder.call_count, CHILD_FOLDER_PREFETCH_LIMIT)


if __name__ == "__main__":
    unittest.main()
