from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class CoreTests(unittest.TestCase):
    def load_core(self, tempdir: str, config_text: str = ""):
        config_path = Path(tempdir) / "rclone.conf"
        config_path.write_text(config_text, encoding="utf-8")
        mount_base = Path(tempdir) / "mounts"

        with mock.patch.dict(
            os.environ,
            {"RCLONE_CONFIG": str(config_path), "CLOUD_MOUNT_BASE": str(mount_base)},
            clear=False,
        ):
            import cloud_mount_manager.core as core

            return importlib.reload(core)

    def test_load_remotes_parses_provider_alias_and_mount_flags(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Work__Drive]
type = drive
mount_flags = --read-only --dir-cache-time 10m
""".strip(),
            )

            remotes = core.load_remotes()

            self.assertEqual(len(remotes), 1)
            remote = remotes[0]
            self.assertEqual(remote.name, "Work__Drive")
            self.assertEqual(remote.alias, "Work")
            self.assertEqual(remote.provider, "Drive")
            self.assertTrue(remote.mount_path.endswith("/drive/Work"))
            self.assertIn("--links", remote.flags)
            self.assertIn("--read-only", remote.flags)
            self.assertIn("10m", remote.flags)

    def test_import_does_not_create_mount_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            mount_base = Path(tempdir) / "mounts"
            self.load_core(tempdir, "[Docs]\ntype = drive\n")

            self.assertFalse(mount_base.exists())

    def test_mount_remote_builds_rclone_mount_command(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs]
type = dropbox
""".strip(),
            )
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "_launch_mount_process", return_value=(True, "mounted")) as launch:
                    success, message = core.mount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(message, "mounted")
            args = launch.call_args.args[1]
            self.assertEqual(args[:3], ["/usr/bin/rclone", "mount", "Docs:"])
            self.assertEqual(args[3], remote.mount_path)
            self.assertIn("--vfs-cache-mode", args)

    def test_get_storage_usage_uses_configured_rclone_binary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/custom/rclone"):
                with mock.patch.object(
                    core.subprocess,
                    "check_output",
                    return_value='{"used": 1073741824, "total": 2147483648}',
                ) as check_output:
                    usage = core.get_storage_usage(remote)

            self.assertEqual(usage, "1.0 / 2.0 GB")
            self.assertEqual(check_output.call_args.args[0][0], "/custom/rclone")

    def test_unmount_remote_uses_available_fuse_command(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core.shutil, "which", side_effect=lambda name: "/usr/bin/fusermount3" if name == "fusermount3" else None):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    success, _ = core.unmount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/fusermount3", "-u"])


if __name__ == "__main__":
    unittest.main()
