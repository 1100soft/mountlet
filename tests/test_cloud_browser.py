from __future__ import annotations

import json
import os
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
    _default_offline_cache_root,
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

    def test_default_offline_cache_root_is_user_visible(self):
        with mock.patch("pathlib.Path.home", return_value=Path("/home/user")):
            self.assertEqual(_default_offline_cache_root(), Path("/home/user/Mountlet/offline"))

    def test_legacy_hidden_offline_cache_is_migrated(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            legacy = root / ".cache" / "mountlet" / "offline"
            legacy_file = legacy / "Docs" / "Reports" / "a.pdf"
            legacy_file.parent.mkdir(parents=True)
            legacy_file.write_text("offline", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=root):
                with mock.patch("mountlet.cloud_browser.app_cache_dir", return_value=root / ".cache" / "mountlet"):
                    backend = CloudBrowserBackend(state_path=root / "state.json")

            self.assertFalse(legacy.exists())
            self.assertEqual(backend.cache_root, root / "Mountlet" / "offline")
            self.assertTrue((backend.cache_root / "Docs" / "Reports" / "a.pdf").is_file())

    def test_legacy_visible_offline_cache_is_migrated(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            legacy = root / "Mountlet Offline"
            legacy_file = legacy / "Docs" / "Reports" / "a.pdf"
            legacy_file.parent.mkdir(parents=True)
            legacy_file.write_text("offline", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=root):
                with mock.patch("mountlet.cloud_browser.app_cache_dir", return_value=root / ".cache" / "mountlet"):
                    backend = CloudBrowserBackend(state_path=root / "state.json")

            self.assertFalse(legacy.exists())
            self.assertEqual(backend.cache_root, root / "Mountlet" / "offline")
            self.assertTrue((backend.cache_root / "Docs" / "Reports" / "a.pdf").is_file())

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

    def test_prepare_offline_open_repairs_existing_read_only_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            path = backend.offline_path("Docs", "Reports/a.pdf")
            path.parent.mkdir(parents=True)
            path.write_text("offline", encoding="utf-8")
            path.chmod(0o400)

            prepared = backend.prepare_offline_open("Docs", "Reports/a.pdf")

            self.assertEqual(prepared, path)
            self.assertTrue(prepared.stat().st_mode & 0o600)

    def test_offline_manifest_preserves_deep_file_ancestors(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/Deep/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("offline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    offline = backend.make_offline(_remote(), entry)

            self.assertTrue(offline.stat().st_mode & 0o600)

            with mock.patch.object(backend, "_rclone", side_effect=RuntimeError("rclone was not found")):
                root_entries = backend.list_entries(_remote(), "")
                reports_entries = backend.list_entries(_remote(), "Reports")
                deep_entries = backend.list_entries(_remote(), "Reports/Deep")

            self.assertEqual([entry.name for entry in root_entries], ["Reports"])
            self.assertEqual([entry.name for entry in reports_entries], ["Deep"])
            self.assertEqual([entry.name for entry in deep_entries], ["a.txt"])

            loaded = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with mock.patch.object(loaded, "_rclone", side_effect=RuntimeError("rclone was not found")):
                self.assertEqual([entry.name for entry in loaded.list_entries(_remote(), "Reports/Deep")], ["a.txt"])

            loaded.remove_offline("Docs", "Reports/Deep/a.txt")
            with mock.patch.object(loaded, "_rclone", side_effect=RuntimeError("rclone was not found")):
                self.assertIsNone(loaded._list_offline_entries("Docs", ""))

    def test_folder_with_saved_descendant_is_available_offline(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.pdf", "Reports/Deep/a.pdf", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("offline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    backend.make_offline(_remote(), entry)

            self.assertTrue(backend.has_offline_content("Docs", "Reports", is_dir=True))
            self.assertTrue(backend.has_offline_content("Docs", "Reports/Deep", is_dir=True))
            self.assertTrue(backend.has_offline_content("Docs", "Reports/Deep/a.pdf", is_dir=False))
            self.assertFalse(backend.has_offline_content("Docs", "Other", is_dir=True))

    def test_changed_offline_file_is_reported_against_mounted_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            mount = root / "mounted" / "Docs"
            mount.mkdir(parents=True)
            remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", str(mount))
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("snapshot", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    backend.make_offline(remote, entry)

            backend.offline_path("Docs", "Reports/a.txt").write_text("offline edit", encoding="utf-8")
            mounted = mount / "Reports" / "a.txt"
            mounted.parent.mkdir(parents=True)
            mounted.write_text("cloud edit", encoding="utf-8")

            conflicts = backend.changed_offline_files(remote)

        self.assertEqual([conflict.path for conflict in conflicts], ["Reports/a.txt"])

    def test_cloud_only_change_updates_offline_copy_without_conflict(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            mount = root / "mounted" / "Docs"
            mount.mkdir(parents=True)
            remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", str(mount))
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("snapshot", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    backend.make_offline(remote, entry)

            mounted = mount / "Reports" / "a.txt"
            mounted.parent.mkdir(parents=True)
            mounted.write_text("cloud edit", encoding="utf-8")

            conflicts = backend.changed_offline_files(remote)

            self.assertEqual(conflicts, [])
            offline = backend.offline_path("Docs", "Reports/a.txt")
            self.assertEqual(offline.read_text(encoding="utf-8"), "cloud edit")
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))

    def test_resolving_offline_conflict_with_newer_version_updates_both_paths(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            mount = root / "mounted" / "Docs"
            mount.mkdir(parents=True)
            remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", str(mount))
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("snapshot", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    backend.make_offline(remote, entry)

            offline = backend.offline_path("Docs", "Reports/a.txt")
            offline.write_text("offline edit", encoding="utf-8")
            mounted = mount / "Reports" / "a.txt"
            mounted.parent.mkdir(parents=True)
            mounted.write_text("cloud edit", encoding="utf-8")
            future = mounted.stat().st_mtime + 10
            os.utime(offline, (future, future))
            conflicts = backend.changed_offline_files(remote)

            backend.resolve_offline_conflict(conflicts[0], "newer")

            self.assertEqual(mounted.read_text(encoding="utf-8"), offline.read_text(encoding="utf-8"))
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))

    def test_mountlet_conflict_copy_can_replace_original(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            mount = root / "mounted" / "Docs"
            copy = mount / "Reports" / "a (Mountlet offline 20260627-120000).txt"
            original = mount / "Reports" / "a.txt"
            copy.parent.mkdir(parents=True)
            copy.write_text("kept copy", encoding="utf-8")
            original.write_text("original", encoding="utf-8")
            remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", str(mount))
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )

            replaced = backend.replace_original_with_conflict_copy(
                remote,
                "Reports/a (Mountlet offline 20260627-120000).txt",
            )

            self.assertEqual(replaced, "Reports/a.txt")
            self.assertEqual(original.read_text(encoding="utf-8"), "kept copy")
            self.assertFalse(copy.exists())

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

    def test_listing_error_clears_stale_visible_entries(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports"
        browser.entries = [BrowserEntry("old.txt", "Reports/old.txt", False)]
        browser._loads_pending = {("Docs", "Reports")}
        browser.tree = mock.Mock()
        browser.status = mock.Mock()
        browser._update_actions = mock.Mock()

        browser._listing_ready("Docs", "Reports", None, "token expired")

        self.assertEqual(browser.entries, [])
        browser.tree.clear.assert_called_once_with()
        browser.status.setText.assert_called_once_with("token expired")
        browser._update_actions.assert_called_once_with()

    def test_listing_error_keeps_cached_visible_entries(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports"
        entries = [BrowserEntry("old.txt", "Reports/old.txt", False)]
        browser._folder_cache = {("Docs", "Reports"): entries}
        browser._loads_pending = {("Docs", "Reports")}
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()

        browser._listing_ready("Docs", "Reports", None, "token expired")

        browser._display_entries.assert_called_once_with(entries)
        browser.status.setText.assert_called_once_with("Showing cached folder contents")

    def test_invalidate_clears_selected_remote_cache_and_refreshes(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._folder_cache = {
            ("Docs", ""): [BrowserEntry("old.txt", "old.txt", False)],
            ("Photos", ""): [BrowserEntry("photo.jpg", "photo.jpg", False)],
        }
        browser._loads_pending = {("Docs", ""), ("Photos", "")}
        browser.refresh = mock.Mock()

        browser.invalidate("Docs")

        self.assertEqual(browser._folder_cache, {("Photos", ""): [BrowserEntry("photo.jpg", "photo.jpg", False)]})
        self.assertEqual(browser._loads_pending, {("Photos", "")})
        browser.refresh.assert_called_once_with(force=True)

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

    def test_focus_selects_first_browser_item_when_none_is_selected(self):
        class Item:
            def __init__(self) -> None:
                self.selected = False

            def setSelected(self, selected: bool) -> None:
                self.selected = selected

        class Tree:
            def __init__(self) -> None:
                self.item = Item()
                self.current = None

            def topLevelItemCount(self) -> int:
                return 1

            def currentItem(self) -> object | None:
                return self.current

            def topLevelItem(self, index: int) -> object:
                self.assert_index = index
                return self.item

            def setCurrentItem(self, item: object) -> None:
                self.current = item

            def selectedItems(self) -> list[object]:
                return []

        browser = object.__new__(CompactCloudBrowser)
        browser.tree = Tree()

        browser._ensure_tree_selection()

        self.assertIs(browser.tree.current, browser.tree.item)
        self.assertTrue(browser.tree.item.selected)

    def test_focus_selects_current_browser_row_with_selection_model(self):
        class Index:
            pass

        class Selection:
            def __init__(self) -> None:
                self.calls: list[tuple[object, object]] = []

            def select(self, index: object, flags: object) -> None:
                self.calls.append((index, flags))

        class Tree:
            def __init__(self) -> None:
                self.item = object()
                self.index = Index()
                self.selection = Selection()
                self.current = None

            def topLevelItemCount(self) -> int:
                return 1

            def currentItem(self) -> object | None:
                return self.current

            def topLevelItem(self, index: int) -> object:
                self.assert_index = index
                return self.item

            def setCurrentItem(self, item: object) -> None:
                self.current = item

            def currentIndex(self) -> object:
                return self.index

            def selectionModel(self) -> Selection:
                return self.selection

        browser = object.__new__(CompactCloudBrowser)
        browser.tree = Tree()
        browser.qt = SimpleNamespace(
            QItemSelectionModel=SimpleNamespace(
                SelectionFlag=SimpleNamespace(ClearAndSelect=1, Rows=2),
            ),
        )

        browser._ensure_tree_selection()

        self.assertIs(browser.tree.current, browser.tree.item)
        self.assertEqual(browser.tree.selection.calls, [(browser.tree.index, 3)])

    def test_focus_selects_current_browser_row_when_qt_selection_flags_are_missing(self):
        class Item:
            def __init__(self) -> None:
                self.selected = False

            def setSelected(self, selected: bool) -> None:
                self.selected = selected

        class Selection:
            def select(self, _index: object, _flags: object) -> None:
                raise AssertionError("Selection flags should be unavailable")

        class Tree:
            def __init__(self) -> None:
                self.item = Item()
                self.selection = Selection()
                self.current = None

            def topLevelItemCount(self) -> int:
                return 1

            def currentItem(self) -> object | None:
                return self.current

            def topLevelItem(self, _index: int) -> Item:
                return self.item

            def setCurrentItem(self, item: object) -> None:
                self.current = item

            def selectedItems(self) -> list[object]:
                return []

            def selectionModel(self) -> Selection:
                return self.selection

        browser = object.__new__(CompactCloudBrowser)
        browser.tree = Tree()
        browser.qt = SimpleNamespace()

        browser._ensure_tree_selection()

        self.assertTrue(browser.tree.item.selected)

    def test_common_browser_navigation_shortcut_moves_current_item(self):
        class Tree:
            def __init__(self) -> None:
                self.items = [object(), object(), object()]
                self.current = self.items[0]

            def topLevelItemCount(self) -> int:
                return len(self.items)

            def currentItem(self) -> object | None:
                return self.current

            def topLevelItem(self, index: int) -> object:
                return self.items[index]

            def indexOfTopLevelItem(self, item: object) -> int:
                return self.items.index(item)

            def setCurrentItem(self, item: object) -> None:
                self.current = item

            def selectedItems(self) -> list[object]:
                return []

        browser = object.__new__(CompactCloudBrowser)
        browser.tree = Tree()
        browser._ensure_tree_selection = mock.Mock()

        browser._focus_relative_item(1)

        self.assertIs(browser.tree.current, browser.tree.items[1])
        browser._ensure_tree_selection.assert_called_once_with()

    def test_browser_copy_alternative_shortcut_uses_copy_action(self):
        class Event:
            def key(self) -> object:
                return "custom"

            def modifiers(self) -> object:
                return 0

            def accept(self) -> None:
                return

        key = SimpleNamespace(
            Key_C="c",
            Key_X="x",
            Key_V="v",
            Key_Delete="delete",
            Key_Escape="esc",
            Key_Left="left",
            Key_Right="right",
            Key_Return="return",
            Key_Enter="enter",
        )
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = SimpleNamespace(Qt=SimpleNamespace(Key=key, KeyboardModifier=SimpleNamespace(ControlModifier=1)))
        browser._edits_enabled = mock.Mock(return_value=True)
        browser.copy_selected = mock.Mock()

        def matches(_qt: object, _event: object, action: str) -> bool:
            return action == "browser_copy"

        with mock.patch("mountlet.cloud_browser_ui.matches_shortcut", side_effect=matches):
            self.assertTrue(browser._handle_key(Event()))

        browser.copy_selected.assert_called_once_with()

    def test_browser_direction_key_returns_only_toward_main_window(self):
        class Event:
            def __init__(self, key: object) -> None:
                self._key = key
                self.accepted = False

            def key(self) -> object:
                return self._key

            def modifiers(self) -> object:
                return 0

            def accept(self) -> None:
                self.accepted = True

        key = SimpleNamespace(
            Key_C="c",
            Key_X="x",
            Key_V="v",
            Key_Delete="delete",
            Key_Escape="esc",
            Key_Left="left",
            Key_Right="right",
            Key_Return="return",
            Key_Enter="enter",
        )
        qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=key,
                KeyboardModifier=SimpleNamespace(ControlModifier=1),
            )
        )
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = qt
        browser._side = "left"
        browser.copy_selected = mock.Mock()
        browser.cut_selected = mock.Mock()
        browser.paste = mock.Mock()
        browser.delete_selected = mock.Mock()
        browser.focus_main_window = mock.Mock()

        self.assertTrue(browser._handle_key(Event("right")))
        browser.focus_main_window.assert_called_once_with()

        browser.focus_main_window.reset_mock()
        event = Event("left")
        self.assertTrue(browser._handle_key(event))
        self.assertTrue(event.accepted)
        browser.focus_main_window.assert_not_called()

    def test_copy_is_blocked_when_integrated_edits_are_disabled(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._edits_enabled = mock.Mock(return_value=False)
        browser._edit_disabled = mock.Mock(return_value=True)
        browser.selected_transfer_items = mock.Mock()

        browser.copy_selected()

        browser._edit_disabled.assert_called_once_with()
        browser.selected_transfer_items.assert_not_called()

    def test_drop_is_blocked_when_integrated_edits_are_disabled(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._edits_enabled = mock.Mock(return_value=False)
        browser._edit_disabled = mock.Mock(return_value=True)
        browser._transfer = mock.Mock()

        browser.accept_drop(b"[]")

        browser._edit_disabled.assert_called_once_with()
        browser._transfer.assert_not_called()

    def test_drag_is_enabled_only_when_integrated_edits_are_enabled(self):
        class Tree:
            def __init__(self) -> None:
                self.drag_enabled = None

            def selectedItems(self) -> list[object]:
                return [object()]

            def setDragEnabled(self, enabled: bool) -> None:
                self.drag_enabled = enabled

        browser = object.__new__(CompactCloudBrowser)
        browser.tree = Tree()
        browser.offline_button = mock.Mock()
        browser._selected_entries = mock.Mock(return_value=[BrowserEntry("a.txt", "a.txt", False)])
        browser._edits_enabled = mock.Mock(return_value=False)

        browser._update_actions()

        self.assertFalse(browser.tree.drag_enabled)

        browser._edits_enabled.return_value = True
        browser._update_actions()

        self.assertTrue(browser.tree.drag_enabled)

    def test_offline_button_tracks_selected_item_state(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._operation_pending = False
        browser.tree = mock.Mock()
        browser.offline_button = mock.Mock()
        browser._edits_enabled = mock.Mock(return_value=False)
        browser._selected_entries = mock.Mock(return_value=[BrowserEntry("a.txt", "a.txt", False)])
        browser.backend = mock.Mock()
        browser.backend.is_offline.return_value = False
        browser.backend.offline_changed.return_value = False

        browser._update_actions()

        browser.offline_button.setEnabled.assert_called_with(True)
        browser.offline_button.setText.assert_called_with("")
        browser.offline_button.setBadgeVisible.assert_called_with(False)
        self.assertIn("Save", browser.offline_button.setToolTip.call_args.args[0])

        browser.backend.is_offline.return_value = True
        browser.backend.offline_changed.return_value = True
        browser._update_actions()

        browser.offline_button.setText.assert_called_with("")
        browser.offline_button.setBadgeColor.assert_called_with("#22c55e")
        browser.offline_button.setBadgeVisible.assert_called_with(True)
        self.assertIn("local changes", browser.offline_button.setToolTip.call_args.args[0])

    def test_offline_button_uses_standard_disabled_state_without_selection(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._operation_pending = False
        browser.tree = mock.Mock()
        browser.offline_button = mock.Mock()
        browser._edits_enabled = mock.Mock(return_value=False)
        browser._selected_entries = mock.Mock(return_value=[])
        browser.backend = mock.Mock()

        browser._update_actions()

        browser.offline_button.setEnabled.assert_called_with(False)
        browser.offline_button.setStyleSheet.assert_called_with("")

    def test_offline_button_explains_pending_operation_when_disabled(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._operation_pending = True
        browser.tree = mock.Mock()
        browser.offline_button = mock.Mock()
        browser._edits_enabled = mock.Mock(return_value=True)
        browser._selected_entries = mock.Mock(return_value=[BrowserEntry("a.txt", "a.txt", False)])
        browser.backend = mock.Mock()
        browser.backend.is_offline.return_value = False
        browser.backend.offline_changed.return_value = False

        browser._update_actions()

        browser.offline_button.setEnabled.assert_called_with(False)
        self.assertIn("Wait", browser.offline_button.setToolTip.call_args.args[0])

    def test_refresh_mount_state_repaints_visible_remote_entries(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.entries = [BrowserEntry("a.txt", "a.txt", False)]
        browser._display_entries = mock.Mock()

        browser.refresh_mount_state("Docs")

        browser._display_entries.assert_called_once_with([BrowserEntry("a.txt", "a.txt", False)])

    def test_open_file_prefers_mounted_path_over_offline_copy(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = SimpleNamespace(Qt=SimpleNamespace(ItemDataRole=SimpleNamespace(UserRole="user")))
        browser.remote = _remote()
        browser.backend = mock.Mock()
        browser.backend.offline_path.return_value = Path("/cache/Docs/a.ods")
        browser._open_local_file = mock.Mock()
        browser._notify = mock.Mock()
        item = mock.Mock()
        item.data.return_value = BrowserEntry("a.ods", "Reports/a.ods", False)

        with mock.patch.object(core, "is_mounted", return_value=True):
            browser._open_item(item)

        browser._open_local_file.assert_called_once_with(Path("/mnt/Docs/Reports/a.ods"))

    def test_open_folder_uses_offline_cache_when_remote_is_unmounted(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.backend = mock.Mock()
        browser.backend.offline_path.return_value = Path("/cache/Docs/Reports")
        browser.backend.prepare_offline_open.return_value = Path("/cache/Docs/Reports")
        browser._open_local_folder = mock.Mock(return_value=True)
        browser._notify = mock.Mock()

        with mock.patch.object(core, "is_mounted", return_value=False):
            with mock.patch.object(Path, "is_dir", return_value=True):
                browser._open_external_folder("Reports")

        browser._open_local_folder.assert_called_once_with(Path("/cache/Docs/Reports"))
        browser._notify.assert_not_called()

    def test_open_folder_button_keeps_standard_color_for_offline_snapshot(self):
        with tempfile.TemporaryDirectory() as tempdir:
            offline = Path(tempdir) / "Docs" / "Reports"
            offline.mkdir(parents=True)
            browser = object.__new__(CompactCloudBrowser)
            browser.remote = _remote()
            browser.path = "Reports"
            browser.backend = mock.Mock()
            browser.backend.offline_path.return_value = offline
            browser.open_folder_button = mock.Mock()
            browser._file_manager_name = mock.Mock(return_value="Explorer")

            with mock.patch.object(core, "is_mounted", return_value=False):
                browser._update_open_folder_button()

        browser.open_folder_button.setStyleSheet.assert_called_once_with("")
        self.assertIn("offline snapshot", browser.open_folder_button.setToolTip.call_args.args[0])

    def test_item_foreground_does_not_require_qbrush_on_qt_namespace(self):
        item = mock.Mock()
        tree = mock.Mock()
        tree.columnCount.return_value = 2
        browser = object.__new__(CompactCloudBrowser)
        browser.tree = tree
        browser.qt = SimpleNamespace(QColor=lambda color: f"color:{color}")

        browser._set_item_foreground(item, "#facc15")

        self.assertEqual(item.setForeground.call_args_list, [mock.call(0, "color:#facc15"), mock.call(1, "color:#facc15")])

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
