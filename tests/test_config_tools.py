from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.config_tools import import_config, path_config, shared, verify_config
from mountlet.platform_services.linux import LinuxPlatformServices


class ConfigToolTests(unittest.TestCase):
    def setUp(self) -> None:
        platform = LinuxPlatformServices()
        patcher = mock.patch.object(shared, "get_platform", return_value=platform)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_remotes_returns_config_sections(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            config_path.write_text("[One]\ntype = drive\n\n[Two]\ntype = s3\n", encoding="utf-8")

            self.assertEqual(shared.read_remotes(config_path), ["One", "Two"])

    def test_resolve_remote_selection_accepts_names_without_colon(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            config_path.write_text("[One]\ntype = drive\n", encoding="utf-8")

            selected, missing = shared.resolve_remote_selection(config_path, ["One:", "Missing"])

            self.assertEqual(selected, ["One"])
            self.assertEqual(missing, ["Missing"])

    def test_verify_parser_does_not_auto_reconnect_by_default(self):
        args = verify_config.build_parser().parse_args([])

        self.assertFalse(args.auto_reconnect)
        self.assertTrue(args.reconnect_auto_confirm)

    def test_import_parser_does_not_auto_reconnect_by_default(self):
        args = import_config.build_parser().parse_args(["--config", "rclone.conf"])

        self.assertFalse(args.verify_auto_reconnect)
        self.assertTrue(args.reconnect_auto_confirm)

    def test_import_parser_requires_explicit_config(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                import_config.build_parser().parse_args([])

    def test_app_paths_follow_xdg_directories(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                self.assertEqual(
                    shared.app_config_file(),
                    Path(tempdir) / "config" / "mountlet" / "config.toml",
                )
                self.assertEqual(
                    shared.app_mounts_file(),
                    Path(tempdir) / "config" / "mountlet" / "mounts.toml",
                )
                self.assertEqual(shared.app_state_dir(), Path(tempdir) / "state" / "mountlet")
                self.assertEqual(shared.app_cache_dir(), Path(tempdir) / "cache" / "mountlet")

    def test_default_rclone_config_honors_rclone_config_environment(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "custom" / "rclone.conf"

            with mock.patch.dict("os.environ", {"RCLONE_CONFIG": str(config_path)}, clear=False):
                self.assertEqual(shared.default_config_path(), config_path)

    def test_path_config_can_ensure_app_directories(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env = {
                "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(path_config.main(["--ensure", "--app-config"]), 0)
                self.assertTrue((Path(tempdir) / "config" / "mountlet").is_dir())
                self.assertTrue((Path(tempdir) / "config" / "mountlet" / "config.toml").is_file())
                self.assertTrue((Path(tempdir) / "config" / "mountlet" / "mounts.toml").is_file())
                self.assertTrue((Path(tempdir) / "state" / "mountlet").is_dir())
                self.assertTrue((Path(tempdir) / "cache" / "mountlet").is_dir())


if __name__ == "__main__":
    unittest.main()
