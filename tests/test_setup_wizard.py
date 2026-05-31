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


if __name__ == "__main__":
    unittest.main()
