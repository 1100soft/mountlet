from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import settings


class SettingsTests(unittest.TestCase):
    def test_load_app_settings_reads_app_and_tray_defaults(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "config.toml"
            path.write_text(
                """
[app]
mount_base = "~/Mounts"
auto_mount = true
auto_mount_delay = 3.5

[tray]
open_folder_behavior = "new_window"
focus_file_manager = false
""".strip(),
                encoding="utf-8",
            )

            config = settings.load_app_settings(path)

        self.assertTrue(config.mount_base.endswith("/Mounts"))
        self.assertTrue(config.auto_mount)
        self.assertEqual(config.auto_mount_delay, 3.5)
        self.assertEqual(config.open_folder_behavior, "new_window")
        self.assertFalse(config.focus_file_manager)

    def test_load_mount_settings_reads_per_remote_overrides(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mounts.toml"
            path.write_text(
                """
[remotes."Work__Drive"]
mount_path = "~/Cloud/Work"
mount_flags = "--read-only --dir-cache-time 10m"
auto_mount = true
enabled = false
""".strip(),
                encoding="utf-8",
            )

            remotes = settings.load_mount_settings(path)

        remote = remotes["Work__Drive"]
        self.assertTrue(remote.mount_path.endswith("/Cloud/Work"))
        self.assertEqual(remote.mount_flags, ["--read-only", "--dir-cache-time", "10m"])
        self.assertTrue(remote.auto_mount)
        self.assertFalse(remote.enabled)

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


if __name__ == "__main__":
    unittest.main()
