from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import settings
from mountlet.platform_services.linux import LinuxPlatformServices


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        platform = LinuxPlatformServices()
        patchers = (
            mock.patch.object(settings, "get_platform", return_value=platform),
            mock.patch("mountlet.config_tools.shared.get_platform", return_value=platform),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_load_app_settings_reads_app_and_tray_defaults(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "config.toml"
            path.write_text(
                """
[app]
mount_base = "~/Mounts"
auto_mount = true
auto_mount_delay = 3.5
start_at_login = true
integrated_file_edits = true

[tray]
file_manager = "org.example.Files.desktop"
open_folder_behavior = "new_window"
focus_file_manager = false

[shortcuts]
browser_parent = "Alt+Up, Backspace"
browser_root = "Ctrl+Home"
""".strip(),
                encoding="utf-8",
            )

            config = settings.load_app_settings(path)

        self.assertTrue(config.mount_base.endswith("/Mounts"))
        self.assertTrue(config.auto_mount)
        self.assertEqual(config.auto_mount_delay, 3.5)
        self.assertTrue(config.start_at_login)
        self.assertTrue(config.integrated_file_edits)
        self.assertEqual(config.file_manager, "org.example.Files.desktop")
        self.assertEqual(config.open_folder_behavior, "new_window")
        self.assertFalse(config.focus_file_manager)
        self.assertEqual(config.shortcuts["browser_parent"], ("Alt+Up", "Backspace"))
        self.assertEqual(config.shortcuts["browser_root"], ("Ctrl+Home",))
        self.assertEqual(config.shortcuts["remote_enter_browser"], settings.DEFAULT_SHORTCUTS["remote_enter_browser"])
        self.assertEqual(config.shortcuts["common_previous"], ())
        self.assertEqual(config.shortcuts["common_next"], ())
        self.assertEqual(config.shortcuts["remote_move_up"], ("Shift+Up",))
        self.assertEqual(config.shortcuts["remote_move_down"], ("Shift+Down",))

    def test_load_mount_settings_reads_per_remote_overrides(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mounts.toml"
            path.write_text(
                """
[remotes."Work__Drive"]
mount_path = "~/Cloud/Work"
remote_path = "bucket/prefix"
mount_flags = "--read-only --dir-cache-time 10m"
auto_mount = true
enabled = false
order = 2
""".strip(),
                encoding="utf-8",
            )

            remotes = settings.load_mount_settings(path)

        remote = remotes["Work__Drive"]
        self.assertTrue(remote.mount_path.endswith("/Cloud/Work"))
        self.assertEqual(remote.remote_path, "bucket/prefix")
        self.assertEqual(remote.mount_flags, ["--read-only", "--dir-cache-time", "10m"])
        self.assertTrue(remote.auto_mount)
        self.assertFalse(remote.enabled)
        self.assertEqual(remote.order, 2)

    def test_load_app_settings_filters_now_immutable_shortcuts(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "config.toml"
            path.write_text(
                """
[shortcuts]
common_previous = "Up, PageUp"
common_next = "Down, PageDown"
remote_enter_browser = "Return, Space, Left, Right"
browser_copy = "Ctrl+C, Alt+C"
browser_delete = "Delete, Alt+Delete"
remote_move_up = "Shift+Up"
remote_move_down = "Shift+Down"
""".strip(),
                encoding="utf-8",
            )

            config = settings.load_app_settings(path)

        self.assertEqual(config.shortcuts["common_previous"], ("PageUp",))
        self.assertEqual(config.shortcuts["common_next"], ("PageDown",))
        self.assertEqual(config.shortcuts["remote_enter_browser"], ("Space",))
        self.assertEqual(config.shortcuts["browser_copy"], ("Alt+C",))
        self.assertEqual(config.shortcuts["browser_delete"], ("Alt+Delete",))
        self.assertEqual(config.shortcuts["remote_move_up"], ("Shift+Up",))
        self.assertEqual(config.shortcuts["remote_move_down"], ("Shift+Down",))

    def test_load_app_settings_migrates_legacy_remote_navigation_shortcuts(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "config.toml"
            path.write_text(
                """
[shortcuts]
remote_previous = "W, PageUp"
remote_next = "S, PageDown"
""".strip(),
                encoding="utf-8",
            )

            config = settings.load_app_settings(path)

        self.assertEqual(config.shortcuts["common_previous"], ("W", "PageUp"))
        self.assertEqual(config.shortcuts["common_next"], ("S", "PageDown"))

    def test_ensure_default_config_files_creates_app_and_mount_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            env = {
                "XDG_CONFIG_HOME": str(config_dir),
                "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                settings.ensure_default_config_files()

            app_config = config_dir / "mountlet" / "config.toml"
            mounts_config = config_dir / "mountlet" / "mounts.toml"
            self.assertTrue(app_config.exists())
            self.assertTrue(mounts_config.exists())

    def test_ensure_default_config_files_copies_legacy_config_once(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config"
            legacy_dir = config_dir / "cloud-mount-manager"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "config.toml").write_text("[app]\nauto_mount = true\n", encoding="utf-8")
            (legacy_dir / "mounts.toml").write_text(
                '[remotes."Docs"]\nauto_mount = true\n',
                encoding="utf-8",
            )
            env = {
                "XDG_CONFIG_HOME": str(config_dir),
                "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                settings.ensure_default_config_files()

            app_config = config_dir / "mountlet" / "config.toml"
            mounts_config = config_dir / "mountlet" / "mounts.toml"
            self.assertIn("auto_mount = true", app_config.read_text(encoding="utf-8"))
            self.assertIn('remotes."Docs"', mounts_config.read_text(encoding="utf-8"))

    def test_save_app_settings_round_trips_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "config.toml"
            settings.save_app_settings(
                settings.AppSettings(
                    mount_base="~/Mounts",
                    auto_mount=True,
                    auto_mount_delay=4.25,
                    start_at_login=True,
                    integrated_file_edits=True,
                    file_manager="org.example.Files.desktop",
                    open_folder_behavior="new_window",
                    focus_file_manager=False,
                    config_sync_remote="Docs__Drive",
                    config_sync_path="Mountlet/shared.mountlet",
                    shortcuts={**settings.DEFAULT_SHORTCUTS, "browser_parent": ("Alt+Up", "Backspace")},
                ),
                path,
            )

            loaded = settings.load_app_settings(path)

        self.assertTrue(loaded.mount_base.endswith("/Mounts"))
        self.assertTrue(loaded.auto_mount)
        self.assertEqual(loaded.auto_mount_delay, 4.25)
        self.assertTrue(loaded.start_at_login)
        self.assertTrue(loaded.integrated_file_edits)
        self.assertEqual(loaded.file_manager, "org.example.Files.desktop")
        self.assertEqual(loaded.open_folder_behavior, "new_window")
        self.assertFalse(loaded.focus_file_manager)
        self.assertEqual(loaded.config_sync_remote, "Docs__Drive")
        self.assertEqual(loaded.config_sync_path, "Mountlet/shared.mountlet")
        self.assertEqual(loaded.shortcuts["browser_parent"], ("Alt+Up", "Backspace"))

    def test_set_start_at_login_writes_and_removes_desktop_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mountlet.desktop"
            settings.set_start_at_login(True, path)

            self.assertIn("Exec=mountlet tray", path.read_text(encoding="utf-8"))

            settings.set_start_at_login(False, path)

            self.assertFalse(path.exists())

    def test_save_mount_settings_round_trips_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mounts.toml"
            settings.save_mount_settings(
                {
                    "Docs": settings.MountSettings(
                        mount_path="~/Docs",
                        remote_path="bucket/docs",
                        mount_flags=["--read-only", "--dir-cache-time", "10m"],
                        auto_mount=True,
                        enabled=False,
                        order=3,
                    )
                },
                path,
            )

            loaded = settings.load_mount_settings(path)["Docs"]

        self.assertTrue(loaded.mount_path.endswith("/Docs"))
        self.assertEqual(loaded.remote_path, "bucket/docs")
        self.assertEqual(loaded.mount_flags, ["--read-only", "--dir-cache-time", "10m"])
        self.assertTrue(loaded.auto_mount)
        self.assertFalse(loaded.enabled)
        self.assertEqual(loaded.order, 3)

    def test_save_mount_settings_omits_unset_auto_mount(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mounts.toml"
            settings.save_mount_settings(
                {
                    "Docs": settings.MountSettings(
                        mount_path="docs",
                        auto_mount=None,
                        order=0,
                    )
                },
                path,
            )

            text = path.read_text(encoding="utf-8")
            loaded = settings.load_mount_settings(path)["Docs"]

        self.assertNotIn("auto_mount", text)
        self.assertIsNone(loaded.auto_mount)
        self.assertEqual(loaded.order, 0)


if __name__ == "__main__":
    unittest.main()
