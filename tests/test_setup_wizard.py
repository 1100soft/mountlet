from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.config_tools import setup_wizard
from mountlet.platform_services.linux import LinuxPlatformServices


class SetupWizardTests(unittest.TestCase):
    def setUp(self) -> None:
        platform = LinuxPlatformServices()
        patchers = (
            mock.patch.object(setup_wizard, "get_platform", return_value=platform),
            mock.patch("mountlet.config_tools.shared.get_platform", return_value=platform),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_windows_command_hint_uses_running_virtualenv_launcher(self):
        launcher = r"C:\Users\Example User\AppData\Local\Mountlet\preview\Scripts\mountlet.exe"
        platform = mock.Mock(system_name="Windows")

        with mock.patch.object(setup_wizard.shutil, "which", return_value=None):
            with mock.patch.object(setup_wizard.sys, "argv", [launcher, "tray"]):
                with mock.patch.object(setup_wizard, "get_platform", return_value=platform):
                    command = setup_wizard._mountlet_command()

        self.assertEqual(command, f"& '{launcher}'")

    def test_command_hint_prefers_global_command(self):
        with mock.patch.object(setup_wizard.shutil, "which", return_value=r"C:\Users\erich\.local\bin\mountlet"):
            command = setup_wizard._mountlet_command()

        self.assertEqual(command, "mountlet")

    def test_prerequisites_report_existing_tools(self):
        with mock.patch.object(setup_wizard, "find_rclone", return_value="/usr/bin/rclone"):
            with mock.patch.object(setup_wizard, "_fuse_available", return_value=True):
                prerequisites = setup_wizard.check_prerequisites()

        self.assertTrue(all(item.ready for item in prerequisites))
        self.assertEqual([item.label for item in prerequisites], ["rclone", "FUSE"])

    def test_prerequisites_include_install_help_when_missing(self):
        with mock.patch.object(setup_wizard, "find_rclone", return_value=None):
            with mock.patch.object(setup_wizard, "_fuse_available", return_value=False):
                prerequisites = setup_wizard.check_prerequisites()

        self.assertFalse(any(item.ready for item in prerequisites))
        self.assertTrue(all(item.help_url.startswith("https://") for item in prerequisites))

    def test_setup_succeeds_when_requirements_and_remotes_exist(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            config_path.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
            args = argparse.Namespace(configure_rclone=False, skip_verify=True)

            with mock.patch.object(setup_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(setup_wizard, "_fuse_available", return_value=True):
                    with mock.patch.object(setup_wizard, "default_config_path", return_value=config_path):
                        with mock.patch.object(setup_wizard.core, "BASE_MOUNT_DIR", str(Path(tempdir) / "mounts")):
                            with mock.patch.dict(
                                "os.environ",
                                {
                                    "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                                    "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                                    "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
                                },
                                clear=False,
                            ):
                                with contextlib.redirect_stdout(io.StringIO()) as output:
                                    result = setup_wizard.setup_command(args)

            self.assertEqual(result, 0)
            self.assertIn("Ready. Open the menu", output.getvalue())

    def test_setup_reports_missing_remotes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "missing-rclone.conf"
            args = argparse.Namespace(configure_rclone=False, skip_verify=True)

            with mock.patch.object(setup_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(setup_wizard, "_fuse_available", return_value=True):
                    with mock.patch.object(setup_wizard, "default_config_path", return_value=config_path):
                        with mock.patch.object(setup_wizard.core, "BASE_MOUNT_DIR", str(Path(tempdir) / "mounts")):
                            with mock.patch.dict(
                                "os.environ",
                                {
                                    "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                                    "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                                    "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
                                },
                                clear=False,
                            ):
                                with contextlib.redirect_stdout(io.StringIO()) as output:
                                    result = setup_wizard.setup_command(args)

            self.assertEqual(result, 0)
            self.assertTrue(config_path.exists())
            self.assertIn("Add cloud storage with the + button", output.getvalue())

    def test_menu_readiness_allows_an_empty_rclone_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone" / "rclone.conf"

            with mock.patch.object(setup_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(setup_wizard, "_fuse_available", return_value=True):
                    with mock.patch.object(setup_wizard, "default_config_path", return_value=config_path):
                        with mock.patch.object(setup_wizard.core, "BASE_MOUNT_DIR", str(Path(tempdir) / "mounts")):
                            ready = setup_wizard.ensure_ready_for_menu()

            self.assertTrue(ready)
            self.assertTrue(config_path.exists())

    def test_setup_tells_user_to_install_rclone_before_configuring_storage(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "missing-rclone.conf"
            args = argparse.Namespace(configure_rclone=True, skip_verify=True)

            with mock.patch.object(setup_wizard, "find_rclone", return_value=None):
                with mock.patch.object(setup_wizard, "_fuse_available", return_value=False):
                    with mock.patch.object(setup_wizard, "default_config_path", return_value=config_path):
                        with mock.patch.object(setup_wizard.core, "BASE_MOUNT_DIR", str(Path(tempdir) / "mounts")):
                            with mock.patch.dict(
                                "os.environ",
                                {
                                    "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                                    "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                                    "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
                                },
                                clear=False,
                            ):
                                with contextlib.redirect_stdout(io.StringIO()) as output:
                                    result = setup_wizard.setup_command(args)

            text = output.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("1. Install rclone: sudo apt install rclone", text)
            self.assertIn("2. Install FUSE: sudo apt install fuse3", text)
            self.assertIn(
                "3. After installing rclone, add cloud storage: mountlet setup --configure-rclone",
                text,
            )
            self.assertNotIn("3. Add cloud storage: mountlet setup --configure-rclone", text)

    def test_menu_readiness_does_not_suggest_rclone_config_when_rclone_is_missing(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "missing-rclone.conf"

            with mock.patch.object(setup_wizard, "find_rclone", return_value=None):
                with mock.patch.object(setup_wizard, "_fuse_available", return_value=True):
                    with mock.patch.object(setup_wizard, "default_config_path", return_value=config_path):
                        with mock.patch.object(setup_wizard.core, "BASE_MOUNT_DIR", str(Path(tempdir) / "mounts")):
                            with mock.patch.dict(
                                "os.environ",
                                {
                                    "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
                                    "XDG_STATE_HOME": str(Path(tempdir) / "state"),
                                    "XDG_CACHE_HOME": str(Path(tempdir) / "cache"),
                                },
                                clear=False,
                            ):
                                with contextlib.redirect_stdout(io.StringIO()) as output:
                                    ready = setup_wizard.ensure_ready_for_menu()

            text = output.getvalue()
            self.assertFalse(ready)
            self.assertIn("mountlet setup", text)
            self.assertNotIn("setup --configure-rclone", text)


if __name__ == "__main__":
    unittest.main()
