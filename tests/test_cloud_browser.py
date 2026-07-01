from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mountlet import core
from mountlet.cloud_browser import (
    BrowserEntry,
    CloudBrowserBackend,
    RCLONE_CACHE_SYNC_TIMEOUT_SECONDS,
    RCLONE_FILE_OPERATION_TIMEOUT_SECONDS,
    RCLONE_OFFLINE_FILE_DOWNLOAD_TIMEOUT_SECONDS,
    RCLONE_FOLDER_DOWNLOAD_TIMEOUT_SECONDS,
    OfflineContentState,
    TransferItem,
    _default_offline_cache_root,
    join_browser_path,
    normalize_browser_path,
    parent_browser_path,
    remote_target,
)
from mountlet.cloud_browser_ui import (
    CHILD_FOLDER_PREFETCH_LIMIT,
    OFFLINE_JOB_CONCURRENCY,
    CompactCloudBrowser,
    cascade_position,
)


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

    def test_rclone_file_operation_uses_timeout(self):
        response = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with mock.patch("mountlet.cloud_browser.subprocess.run", return_value=response) as run:
                backend._run_operation("rclone", "copyto", "Docs:/a.txt", str(Path(tempdir) / "a.txt"))

        self.assertEqual(run.call_args.kwargs["timeout"], RCLONE_FILE_OPERATION_TIMEOUT_SECONDS)

    def test_rclone_file_operation_timeout_reports_operation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            with mock.patch(
                "mountlet.cloud_browser.subprocess.run",
                side_effect=subprocess.TimeoutExpired("rclone", RCLONE_FILE_OPERATION_TIMEOUT_SECONDS),
            ):
                with self.assertRaisesRegex(RuntimeError, "copyto timed out"):
                    backend._run_operation("rclone", "copyto", "Docs:/a.txt", str(Path(tempdir) / "a.txt"))

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

    def test_managed_file_paths_lists_cached_files_by_remote(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            local = backend.offline_path("Docs", "Reports/a.txt")
            local.parent.mkdir(parents=True)
            local.write_text("cached", encoding="utf-8")
            backend._offline_records = {
                "Docs": {
                    "Reports": {"is_dir": True},
                    "Reports/a.txt": {"is_dir": False},
                }
            }

            self.assertEqual(backend.managed_file_paths(), {"Docs": [local]})
            self.assertEqual(backend.managed_file_paths("Docs"), {"Docs": [local]})
            self.assertEqual(backend.managed_record_paths("Docs"), ["Reports/a.txt"])
            self.assertEqual(backend.managed_record_paths_under("Docs", "Reports"), ["Reports/a.txt"])
            self.assertEqual(backend.managed_file_paths_under("Docs", "Reports"), [local])
            self.assertEqual(backend.managed_file_paths_under("Docs", ""), [local])
            self.assertEqual(backend.remote_name_for_offline_path(local), "Docs")

    def test_changed_managed_remote_names_uses_local_metadata(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            local = backend.offline_path("Docs", "Reports/a.txt")
            local.parent.mkdir(parents=True)
            local.write_text("cached", encoding="utf-8")
            stat_result = local.stat()
            backend._offline_records = {
                "Docs": {
                    "Reports/a.txt": {
                        "is_dir": False,
                        "local_size": stat_result.st_size,
                        "local_mtime_ns": stat_result.st_mtime_ns,
                    },
                },
                "Photos": {
                    "image.jpg": {"is_dir": False, "local_size": 1, "local_mtime_ns": 1},
                },
            }

            local.write_text("edited", encoding="utf-8")

            self.assertEqual(backend.changed_managed_remote_names(), ["Docs"])
            self.assertEqual(backend.changed_managed_paths("Docs"), ["Reports/a.txt"])

    def test_changed_managed_files_skips_clean_files_without_remote_polling(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    backend.cache_file(remote, entry)

            diagnostics: list[str] = []
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    conflicts = backend.changed_managed_files(remote, diagnostics=diagnostics)

            self.assertEqual(conflicts, [])
            run.assert_not_called()
            self.assertIn("candidate_paths: 0", diagnostics)

    def test_offline_manifest_preserves_deep_file_ancestors(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/Deep/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
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

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("offline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    backend.make_offline(_remote(), entry)

            self.assertTrue(backend.has_offline_content("Docs", "Reports", is_dir=True))
            self.assertTrue(backend.is_partially_offline("Docs", "Reports", is_dir=True))
            self.assertFalse(backend.is_offline("Docs", "Reports", is_dir=True))
            self.assertTrue(backend.has_offline_content("Docs", "Reports/Deep", is_dir=True))
            self.assertTrue(backend.is_partially_offline("Docs", "Reports/Deep", is_dir=True))
            self.assertFalse(backend.is_offline("Docs", "Reports/Deep", is_dir=True))
            self.assertTrue(backend.has_offline_content("Docs", "Reports/Deep/a.pdf", is_dir=False))
            self.assertFalse(backend.has_offline_content("Docs", "Other", is_dir=True))

    def test_removed_child_of_offline_folder_is_not_inherited_as_offline(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("Reports", "Reports", True, 0, "2026-01-02 03:04")

            def copy_folder(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[2])
                (destination / "Deep").mkdir(parents=True, exist_ok=True)
                (destination / "Deep" / "a.pdf").write_text("offline", encoding="utf-8")
                (destination / "Deep" / "b.pdf").write_text("offline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_folder):
                    backend.make_offline(_remote(), entry)

            backend.remove_offline("Docs", "Reports/Deep/a.pdf")

            self.assertFalse(backend.is_offline("Docs", "Reports/Deep/a.pdf", is_dir=False))
            self.assertFalse(backend.has_offline_content("Docs", "Reports/Deep/a.pdf", is_dir=False))
            self.assertTrue(backend.is_offline("Docs", "Reports/Deep/b.pdf", is_dir=False))
            self.assertFalse(backend.is_offline("Docs", "Reports", is_dir=True))
            self.assertTrue(backend.is_partially_offline("Docs", "Reports", is_dir=True))

    def test_parent_folder_reports_protected_child_folder_content(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            backend._offline_records = {
                "Docs": {
                    "Reports": {"is_dir": True, "protected": True, "complete": False},
                    "Reports/Empty": {"is_dir": True, "protected": True, "complete": True},
                }
            }

            self.assertTrue(backend.has_protected_content("Docs", "Reports", is_dir=True))
            self.assertTrue(backend.is_partially_offline("Docs", "Reports", is_dir=True))

    def test_offline_folder_download_uses_long_timeout(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("Reports", "Reports", True, 0, "2026-01-02 03:04")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    backend.make_offline(_remote(), entry)

            self.assertEqual(run.call_args.kwargs["timeout"], RCLONE_FOLDER_DOWNLOAD_TIMEOUT_SECONDS)

    def test_offline_file_download_uses_longer_offline_timeout(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.bin", "Reports/a.bin", False, 0, "2026-01-02 03:04")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation") as run:
                    backend.make_offline(_remote(), entry)

            self.assertEqual(run.call_args.kwargs["timeout"], RCLONE_OFFLINE_FILE_DOWNLOAD_TIMEOUT_SECONDS)

    def test_legacy_ancestor_folder_records_are_loaded_as_partial(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manifest = root / "offline.json"
            cache = root / "cache"
            local = cache / "Docs" / "Reports" / "Deep" / "a.pdf"
            local.parent.mkdir(parents=True)
            local.write_text("offline", encoding="utf-8")
            stat_result = local.stat()
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "remotes": {
                            "Docs": {
                                "Reports": {"is_dir": True, "protected": True, "modified": ""},
                                "Reports/Deep": {"is_dir": True, "protected": True, "modified": ""},
                                "Reports/Deep/a.pdf": {
                                    "is_dir": False,
                                    "protected": True,
                                    "local_size": stat_result.st_size,
                                    "local_mtime_ns": stat_result.st_mtime_ns,
                                    "local_sha256": "",
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=cache,
                manifest_path=manifest,
            )

            self.assertTrue(backend.is_partially_offline("Docs", "Reports", is_dir=True))
            self.assertFalse(backend.is_offline("Docs", "Reports", is_dir=True))

    def test_cached_file_is_not_available_offline_and_can_be_freed(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("cached", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    cached = backend.cache_file(_remote(), entry)

            self.assertTrue(cached.is_file())
            self.assertTrue(backend.is_cached("Docs", "Reports/a.txt"))
            self.assertFalse(backend.is_offline("Docs", "Reports/a.txt"))

            removed = backend.free_all_resolved_cache()

            self.assertEqual(removed, 1)
            self.assertFalse(cached.exists())

    def test_free_cache_preserves_available_offline_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            backend = CloudBrowserBackend(
                state_path=Path(tempdir) / "state.json",
                cache_root=Path(tempdir) / "cache",
            )
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("offline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    offline = backend.make_offline(_remote(), entry)

            removed = backend.free_all_resolved_cache()

            self.assertEqual(removed, 0)
            self.assertTrue(offline.exists())
            self.assertTrue(backend.is_offline("Docs", "Reports/a.txt"))

    def test_changed_cached_file_uploads_when_cloud_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    cached = backend.cache_file(remote, entry)

            cached.write_text("local edit", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def reconcile_copy(_binary: str, *arguments: str, **_kwargs: object) -> None:
                calls.append(arguments)
                if arguments[0] == "copyto" and str(arguments[1]).startswith("Docs:"):
                    destination = Path(arguments[2])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=reconcile_copy):
                    conflicts = backend.changed_managed_files(remote)

            self.assertEqual(conflicts, [])
            self.assertIn(("copyto", str(cached), "Docs:/Reports/a.txt"), calls)
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))

    def test_changed_cached_file_uses_remote_metadata_before_downloading_cloud_copy(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    cached = backend.cache_file(remote, entry)

            record = backend._offline_records["Docs"]["Reports/a.txt"]
            record["remote_size"] = 7
            record["remote_modtime"] = "2026-01-02T03:04:00Z"
            cached.write_text("local edit", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def upload_only(_binary: str, *arguments: str, **_kwargs: object) -> None:
                calls.append(arguments)
                self.assertEqual(arguments[0], "copyto")
                self.assertEqual(str(arguments[1]), str(cached))

            metadata = {"Size": 7, "ModTime": "2026-01-02T03:04:00Z"}
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_remote_file_metadata", return_value=metadata) as stat:
                    with mock.patch.object(backend, "_run_operation", side_effect=upload_only):
                        conflicts = backend.changed_managed_files(remote)

            self.assertEqual(conflicts, [])
            self.assertEqual(calls, [("copyto", str(cached), "Docs:/Reports/a.txt")])
            self.assertFalse(backend.remote_current_path("Docs", "Reports/a.txt").exists())
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))
            stat.assert_called_once()

    def test_changed_cached_file_uses_cache_sync_timeout(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    cached = backend.cache_file(remote, entry)

            cached.write_text("local edit", encoding="utf-8")

            def reconcile_copy(_binary: str, *arguments: str, **_kwargs: object) -> None:
                if arguments[0] == "copyto" and str(arguments[1]).startswith("Docs:"):
                    destination = Path(arguments[2])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=reconcile_copy) as run:
                    backend.changed_managed_files(remote)

            sync_calls = [call for call in run.call_args_list if call.kwargs.get("timeout") == RCLONE_CACHE_SYNC_TIMEOUT_SECONDS]
            self.assertEqual(len(sync_calls), 2)

    def test_remote_poll_initializes_metadata_without_downloading_clean_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    backend.cache_file(remote, entry)

            metadata = {"Size": 7, "ModTime": "2026-01-02T03:04:00Z"}
            diagnostics: list[str] = []
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_remote_file_metadata", return_value=metadata):
                    with mock.patch.object(backend, "_run_operation") as run:
                        conflicts = backend.changed_managed_files(
                            remote,
                            diagnostics=diagnostics,
                            include_remote_checks=True,
                        )

            self.assertEqual(conflicts, [])
            run.assert_not_called()
            record = backend._offline_records["Docs"]["Reports/a.txt"]
            self.assertEqual(record["remote_size"], 7)
            self.assertEqual(record["remote_modtime"], "2026-01-02T03:04:00Z")
            self.assertIn("  decision: initialized remote metadata baseline", diagnostics)

    def test_metadata_only_local_save_refreshes_record_without_dirty_state(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    cached = backend.cache_file(remote, entry)

            before = cached.stat().st_mtime_ns
            os.utime(cached, ns=(before + 1_000_000_000, before + 1_000_000_000))

            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))
            self.assertEqual(
                backend._offline_records["Docs"]["Reports/a.txt"]["local_mtime_ns"],
                cached.stat().st_mtime_ns,
            )

    def test_changed_managed_files_tolerates_manifest_changes_during_scan(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def initial_copy(_binary: str, *_arguments: str) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=initial_copy):
                    cached = backend.cache_file(remote, entry)

            cached.write_text("local edit", encoding="utf-8")

            def mutate_manifest(_binary: str, *arguments: str, **_kwargs: object) -> None:
                backend._offline_records.setdefault("Docs", {})["Reports/b.txt"] = {
                    "is_dir": False,
                    "local_size": 1,
                    "local_mtime_ns": 1,
                    "local_sha256": "old",
                }
                if arguments[0] == "copyto" and str(arguments[1]).startswith("Docs:"):
                    destination = Path(arguments[2])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=mutate_manifest):
                    conflicts = backend.changed_managed_files(remote)

            self.assertEqual(conflicts, [])
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))

    def test_cloud_change_updates_unchanged_cached_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "state.json",
                cache_root=root / "cache",
            )
            remote = _remote()
            entry = BrowserEntry("a.txt", "Reports/a.txt", False, 7, "2026-01-02 03:04")

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("baseline", encoding="utf-8")

            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_run_operation", side_effect=copy_file):
                    cached = backend.cache_file(remote, entry)

            record = backend._offline_records["Docs"]["Reports/a.txt"]
            record["remote_size"] = 7
            record["remote_modtime"] = "2026-01-02T03:04:00Z"

            def remote_changed(_binary: str, *_arguments: str, **_kwargs: object) -> None:
                destination = Path(_arguments[-1])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("cloud edit", encoding="utf-8")

            metadata = {"Size": 10, "ModTime": "2026-01-03T03:04:00Z"}
            with mock.patch.object(backend, "_rclone", return_value="rclone"):
                with mock.patch.object(backend, "_remote_file_metadata", return_value=metadata):
                    with mock.patch.object(backend, "_run_operation", side_effect=remote_changed):
                        conflicts = backend.changed_managed_files(remote, include_remote_checks=True)

            self.assertEqual(conflicts, [])
            self.assertEqual(cached.read_text(encoding="utf-8"), "cloud edit")
            self.assertFalse(backend.offline_changed("Docs", "Reports/a.txt"))

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

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
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

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
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

            def copy_file(_binary: str, *_arguments: str, **_kwargs: object) -> None:
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

    def test_browser_cascade_uses_minimum_size_before_window_is_laid_out(self):
        position = cascade_position((800, 100, 300, 400), 700, (0, 0, 1200, 800), (0, 0))

        self.assertEqual(position, (252, 460))

    def test_browser_backend_renames_remembered_paths_and_offline_cache(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            backend = CloudBrowserBackend(
                state_path=root / "browser.json",
                cache_root=root / "offline",
                manifest_path=root / "offline.json",
            )
            backend.remember_path("Old__Drive", "Reports")
            old_cache = backend.offline_path("Old__Drive", "a.txt")
            old_cache.parent.mkdir(parents=True)
            old_cache.write_text("snapshot", encoding="utf-8")
            backend._offline_records = {
                "Old__Drive": {
                    "a.txt": {
                        "is_dir": False,
                        "size": 8,
                        "modified": "",
                        "cached_at": "",
                        "local_size": 8,
                        "local_mtime_ns": 1,
                        "local_sha256": "hash",
                    }
                }
            }

            backend.rename_remote("Old__Drive", "New__Drive")

            self.assertEqual(backend.current_path("New__Drive"), "Reports")
            self.assertFalse((root / "offline" / "Old__Drive").exists())
            self.assertEqual(backend.offline_path("New__Drive", "a.txt").read_text(encoding="utf-8"), "snapshot")
            self.assertIn("New__Drive", json.loads((root / "offline.json").read_text(encoding="utf-8"))["remotes"])

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

    def test_prefetch_related_folders_fetches_parent_and_children(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser.path = "Reports/Deep"
        browser._folder_cache = {}
        browser._loads_pending = set()
        browser._load_folder = mock.Mock()

        browser._prefetch_related_folders([BrowserEntry("Child", "Reports/Deep/Child", True)])

        browser._load_folder.assert_has_calls(
            [
                mock.call(browser.remote, "Reports"),
                mock.call(browser.remote, "Reports/Deep/Child"),
            ]
        )

    def test_display_entries_preserves_current_item_by_path(self):
        class Item:
            def __init__(self, values: list[str]) -> None:
                self.values = values
                self.entry = None
                self.selected = False

            def setData(self, _column: int, _role: object, value: object) -> None:
                self.entry = value

            def data(self, _column: int, _role: object) -> object:
                return self.entry

            def setIcon(self, _column: int, _icon: object) -> None:
                pass

            def setToolTip(self, _column: int, _text: str) -> None:
                pass

            def setSelected(self, selected: bool) -> None:
                self.selected = selected

            def setText(self, _column: int, _text: str) -> None:
                pass

        class Tree:
            def __init__(self, current: Item) -> None:
                self.current = current
                self.items: list[Item] = []
                self.scroll = mock.Mock()
                self.scroll.value.return_value = 37

            def currentItem(self) -> Item:
                return self.current

            def selectedItems(self) -> list[Item]:
                return [item for item in self.items if item.selected] or [self.current]

            def indexOfTopLevelItem(self, _item: Item) -> int:
                return 1

            def clear(self) -> None:
                self.items = []

            def addTopLevelItem(self, item: Item) -> None:
                self.items.append(item)

            def topLevelItemCount(self) -> int:
                return len(self.items)

            def setCurrentItem(self, item: Item) -> None:
                self.current = item

            def verticalScrollBar(self) -> mock.Mock:
                return self.scroll

        previous = Item(["", "b.txt", "", ""])
        previous.setData(0, 1, BrowserEntry("b.txt", "Reports/b.txt", False))
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = SimpleNamespace(
            QTreeWidgetItem=Item,
            Qt=SimpleNamespace(ItemDataRole=SimpleNamespace(UserRole=1)),
            QStyle=SimpleNamespace(
                StandardPixmap=SimpleNamespace(SP_DirIcon=1, SP_FileIcon=2, SP_DialogSaveButton=3)
            ),
            QTimer=SimpleNamespace(singleShot=lambda *_args: None),
        )
        browser.tree = Tree(previous)
        browser.window = SimpleNamespace(style=lambda: SimpleNamespace(standardIcon=lambda _icon: object()))
        browser.backend = mock.Mock()
        browser.backend.is_offline.return_value = False
        browser.backend.is_partially_offline.return_value = False
        browser.backend.is_cached.return_value = False
        browser.backend.has_offline_content.return_value = False
        browser.backend.has_cached_content.return_value = False
        browser.remote = _remote()
        browser.status = mock.Mock()
        browser.has_focus = mock.Mock(return_value=False)
        browser._offline_icon = mock.Mock(return_value=None)
        browser._update_actions = mock.Mock()
        browser._update_open_folder_button = mock.Mock()

        browser._display_entries(
            [
                BrowserEntry("a.txt", "Reports/a.txt", False),
                BrowserEntry("b.txt", "Reports/b.txt", False),
            ]
        )

        self.assertEqual(browser.tree.current.data(0, 1).path, "Reports/b.txt")
        self.assertTrue(browser.tree.current.selected)
        browser.tree.scroll.setValue.assert_called_once_with(37)

    def test_display_entries_selects_folder_returned_from_parent_navigation(self):
        class Item:
            def __init__(self, values: list[str]) -> None:
                self.values = values
                self.entry = None
                self.selected = False

            def setData(self, _column: int, _role: object, value: object) -> None:
                self.entry = value

            def data(self, _column: int, _role: object) -> object:
                return self.entry

            def setIcon(self, _column: int, _icon: object) -> None:
                pass

            def setToolTip(self, _column: int, _text: str) -> None:
                pass

            def setSelected(self, selected: bool) -> None:
                self.selected = selected

        class Tree:
            def __init__(self) -> None:
                self.current = None
                self.items: list[Item] = []
                self.scroll = mock.Mock()
                self.scroll.value.return_value = 0
                self.scrolled_to = None

            def currentItem(self) -> object | None:
                return self.current

            def selectedItems(self) -> list[Item]:
                return [item for item in self.items if item.selected]

            def clear(self) -> None:
                self.items = []

            def addTopLevelItem(self, item: Item) -> None:
                self.items.append(item)

            def topLevelItemCount(self) -> int:
                return len(self.items)

            def setCurrentItem(self, item: Item) -> None:
                self.current = item

            def scrollToItem(self, item: Item) -> None:
                self.scrolled_to = item

            def verticalScrollBar(self) -> mock.Mock:
                return self.scroll

        browser = object.__new__(CompactCloudBrowser)
        browser.qt = SimpleNamespace(
            QTreeWidgetItem=Item,
            Qt=SimpleNamespace(ItemDataRole=SimpleNamespace(UserRole=1)),
            QStyle=SimpleNamespace(
                StandardPixmap=SimpleNamespace(SP_DirIcon=1, SP_FileIcon=2, SP_DialogSaveButton=3)
            ),
            QTimer=SimpleNamespace(singleShot=lambda *_args: None),
        )
        browser.tree = Tree()
        browser.window = SimpleNamespace(style=lambda: SimpleNamespace(standardIcon=lambda _icon: object()))
        browser.backend = mock.Mock()
        browser.backend.is_offline.return_value = False
        browser.backend.is_partially_offline.return_value = False
        browser.backend.is_cached.return_value = False
        browser.backend.has_offline_content.return_value = False
        browser.backend.has_cached_content.return_value = False
        browser.remote = _remote()
        browser.status = mock.Mock()
        browser.has_focus = mock.Mock(return_value=False)
        browser._offline_icon = mock.Mock(return_value=None)
        browser._update_actions = mock.Mock()
        browser._update_open_folder_button = mock.Mock()
        browser._pending_select_path = "Reports/Deep"

        browser._display_entries(
            [
                BrowserEntry("Other", "Reports/Other", True),
                BrowserEntry("Deep", "Reports/Deep", True),
            ]
        )

        self.assertEqual(browser.tree.current.data(0, 1).path, "Reports/Deep")
        self.assertIs(browser.tree.scrolled_to, browser.tree.current)
        browser.tree.scroll.setValue.assert_not_called()
        self.assertEqual(browser._pending_select_path, "")

    def test_working_status_applies_to_parent_and_child_paths(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._working_paths = {
            ("Docs", "Reports/Deep/a.txt"): "sync",
            ("Docs", "Downloads"): "download",
        }

        self.assertEqual(browser._working_kind_for_entry("Docs", "Reports", is_dir=True), "sync")
        self.assertEqual(browser._working_kind_for_entry("Docs", "Reports/Deep", is_dir=True), "sync")
        self.assertEqual(browser._working_kind_for_entry("Docs", "Reports/Deep/a.txt", is_dir=False), "sync")
        self.assertEqual(browser._working_kind_for_entry("Docs", "Downloads/b.txt", is_dir=False), "")
        self.assertEqual(browser._working_kind_for_entry("Docs", "Other", is_dir=True), "")

    def test_visible_entries_under_downloading_folder_get_exact_download_state(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._working_paths = {("Docs", "Reports"): "download"}
        browser.backend = mock.Mock()
        browser.backend.is_cached.return_value = False

        browser._refresh_visible_download_state(
            "Docs",
            [
                BrowserEntry("a.txt", "Reports/a.txt", False),
                BrowserEntry("Done.txt", "Reports/Done.txt", False),
            ],
        )

        self.assertEqual(browser._working_paths[("Docs", "Reports/a.txt")], "download")
        self.assertEqual(browser._working_paths[("Docs", "Reports/Done.txt")], "download")

    def test_visible_download_state_clears_completed_file(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._working_paths = {
            ("Docs", "Reports"): "download",
            ("Docs", "Reports/a.txt"): "download",
        }
        browser.backend = mock.Mock()
        browser.backend.is_cached.return_value = True

        browser._refresh_visible_download_state("Docs", [BrowserEntry("a.txt", "Reports/a.txt", False)])

        self.assertNotIn(("Docs", "Reports/a.txt"), browser._working_paths)
        self.assertEqual(browser._working_paths[("Docs", "Reports")], "download")

    def test_known_download_paths_include_cached_descendants(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._folder_cache = {
            ("Docs", "Reports"): [
                BrowserEntry("a.txt", "Reports/a.txt", False),
                BrowserEntry("Deep", "Reports/Deep", True),
            ],
            ("Docs", "Reports/Deep"): [BrowserEntry("b.txt", "Reports/Deep/b.txt", False)],
            ("Docs", "Other"): [BrowserEntry("c.txt", "Other/c.txt", False)],
        }

        paths = browser._known_download_paths_for_entry("Docs", BrowserEntry("Reports", "Reports", True))

        self.assertEqual(paths, ["Reports", "Reports/a.txt", "Reports/Deep", "Reports/Deep/b.txt"])

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

    def test_close_button_suppresses_hover_reopen_until_explicit_selection(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._embedded = True
        browser.root = mock.Mock()
        browser.root.isVisible.return_value = False
        browser._layout_changed = mock.Mock()
        browser.backend = mock.Mock()
        browser.backend.current_path.return_value = ""
        browser.remote = None
        browser.refresh = mock.Mock()
        browser.main_window = mock.Mock()
        browser.tree = mock.Mock()
        browser.qt = SimpleNamespace(Qt=SimpleNamespace(FocusReason=SimpleNamespace(ShortcutFocusReason="shortcut")))
        browser._ensure_tree_selection = mock.Mock()
        browser._update_focus_style = mock.Mock()
        browser._update_main_focus_style = mock.Mock()

        browser.hide_until_selected()
        browser.show_remote(_remote(), mock.Mock(), show_browser=True, focus_browser=False)

        browser.root.hide.assert_called_once_with()
        browser.root.show.assert_not_called()
        self.assertTrue(browser._closed_until_selected)

        browser.show_remote(_remote(), mock.Mock(), show_browser=True, focus_browser=True)

        self.assertEqual(browser.root.show.call_count, 2)
        self.assertFalse(browser._closed_until_selected)

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

    def test_open_item_ignores_deleted_qt_item(self):
        class DeletedItem:
            def data(self, _column: int, _role: object) -> object:
                raise RuntimeError("Internal C++ object already deleted")

        browser = object.__new__(CompactCloudBrowser)
        browser.qt = SimpleNamespace(Qt=SimpleNamespace(ItemDataRole=SimpleNamespace(UserRole="user")))
        browser._open_entry = mock.Mock()

        browser._open_item(DeletedItem())

        browser._open_entry.assert_not_called()

    def test_download_operation_disables_only_affected_entry(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._working_paths = {("Docs", "Reports"): "download"}

        self.assertTrue(browser._entry_has_operation("Docs", "Reports", is_dir=True, kind="download"))
        self.assertFalse(browser._entry_has_operation("Docs", "Reports/a.txt", is_dir=False, kind="download"))
        self.assertFalse(browser._entry_has_operation("Docs", "Other/b.txt", is_dir=False, kind="download"))

    def test_queued_offline_job_marks_path_before_worker_slot_opens(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._offline_jobs_running = OFFLINE_JOB_CONCURRENCY
        browser._offline_job_queue = []
        browser._working_paths = {}
        browser._working_timer = None
        browser.entries = []
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._update_actions = mock.Mock()

        browser._queue_offline_job(
            "Downloading for offline use…",
            lambda: None,
            working_paths=["Reports"],
            working_kind="download",
        )

        self.assertEqual(browser._working_paths, {("Docs", "Reports"): "download"})
        self.assertEqual(len(browser._offline_job_queue), 1)
        browser.status.setText.assert_called_once_with("Queued offline file work…")

    def test_remove_offline_job_waits_for_overlapping_download(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._offline_jobs_running = 0
        browser._offline_job_queue = []
        browser._working_paths = {("Docs", "Reports"): "download"}
        browser._working_timer = None
        browser.entries = []
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._update_actions = mock.Mock()

        browser._queue_remove_offline_job("Docs", "Reports/Deep")

        self.assertEqual(len(browser._offline_job_queue), 1)
        self.assertEqual(browser._offline_jobs_running, 0)
        self.assertEqual(browser._working_paths, {("Docs", "Reports"): "download"})

    def test_unrelated_remove_offline_job_can_run_while_download_continues(self):
        started_threads = []

        class Thread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._offline_jobs_running = 0
        browser._offline_job_queue = []
        browser._working_paths = {("Docs", "Reports"): "download"}
        browser._working_timer = None
        browser.entries = []
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._update_actions = mock.Mock()
        browser._bridge = SimpleNamespace(offline_job_finished=SimpleNamespace(emit=mock.Mock()))
        browser.backend = mock.Mock()

        with mock.patch("mountlet.cloud_browser_ui.threading.Thread", Thread):
            browser._queue_remove_offline_job("Docs", "Other")

        self.assertEqual(browser._offline_jobs_running, 0)
        self.assertEqual(browser._offline_job_queue, [])
        self.assertEqual(browser._working_paths[("Docs", "Reports")], "download")
        self.assertEqual(browser._working_paths[("Docs", "Other")], "remove")
        browser.backend.remove_offline.assert_not_called()
        self.assertEqual(len(started_threads), 1)

    def test_unrelated_remove_offline_job_runs_when_download_workers_are_full(self):
        started_threads = []

        class Thread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._offline_jobs_running = OFFLINE_JOB_CONCURRENCY
        browser._offline_job_queue = []
        browser._working_paths = {("Docs", "Reports"): "download"}
        browser._working_timer = None
        browser.entries = []
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._update_actions = mock.Mock()
        browser._bridge = SimpleNamespace(offline_job_finished=SimpleNamespace(emit=mock.Mock()))
        browser.backend = mock.Mock()

        with mock.patch("mountlet.cloud_browser_ui.threading.Thread", Thread):
            browser._queue_remove_offline_job("Docs", "Other")

        self.assertEqual(browser._offline_jobs_running, OFFLINE_JOB_CONCURRENCY)
        self.assertEqual(browser._offline_job_queue, [])
        self.assertEqual(browser._working_paths[("Docs", "Other")], "remove")
        browser.backend.remove_offline.assert_not_called()
        self.assertEqual(len(started_threads), 1)

    def test_duplicate_remove_offline_job_is_ignored(self):
        browser = object.__new__(CompactCloudBrowser)
        browser.remote = _remote()
        browser._offline_jobs_running = 0
        browser._offline_job_queue = [("Docs", "Removing local copies…", lambda: None, ["Reports"], "remove")]
        browser._working_paths = {("Docs", "Reports"): "download"}
        browser._working_timer = None
        browser.entries = []
        browser.status = mock.Mock()
        browser._display_entries = mock.Mock()
        browser._update_actions = mock.Mock()

        browser._queue_remove_offline_job("Docs", "Reports/Deep")

        self.assertEqual(len(browser._offline_job_queue), 1)

    def test_open_local_file_uses_external_opener_without_tracking_state(self):
        browser = object.__new__(CompactCloudBrowser)
        browser._open_file = mock.Mock(return_value=True)

        browser._open_local_file(Path("/cache/Docs/a.txt"))

        browser._open_file.assert_called_once_with(Path("/cache/Docs/a.txt"))

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
