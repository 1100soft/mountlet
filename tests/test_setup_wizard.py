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

from cloud_mount_manager.config_tools import setup_wizard


class SetupWizardTests(unittest.TestCase):
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

            self.assertEqual(result, 1)
            self.assertIn("cloud-mount-manager setup --configure-rclone", output.getvalue())

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
                "3. After installing rclone, add cloud storage: cloud-mount-manager setup --configure-rclone",
                text,
            )
            self.assertNotIn("3. Add cloud storage: cloud-mount-manager setup --configure-rclone", text)

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
            self.assertIn("cloud-mount-manager setup", text)
            self.assertNotIn("setup --configure-rclone", text)


if __name__ == "__main__":
    unittest.main()
