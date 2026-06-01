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
    def load_core(self, tempdir: str, config_text: str = "", *, set_mount_base: bool = True):
        config_path = Path(tempdir) / "rclone.conf"
        config_path.write_text(config_text, encoding="utf-8")
        mount_base = Path(tempdir) / "mounts"
        env = {
            "RCLONE_CONFIG": str(config_path),
            "XDG_CONFIG_HOME": str(Path(tempdir) / "config"),
        }
        if set_mount_base:
            env["MOUNTLET_MOUNT_BASE"] = str(mount_base)
        else:
            env["MOUNTLET_MOUNT_BASE"] = ""
            env["CLOUD_MOUNT_BASE"] = ""
            env["GDRIVE_MOUNT_BASE"] = ""

        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        import mountlet.core as core

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

    def test_load_remotes_applies_app_and_mount_settings(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config" / "mountlet"
            config_dir.mkdir(parents=True)
            (config_dir / "config.toml").write_text(
                "[app]\nmount_base = \"~/ignored\"\nauto_mount = true\n",
                encoding="utf-8",
            )
            (config_dir / "mounts.toml").write_text(
                """
[remotes."Docs"]
mount_path = "~/custom-docs"
mount_flags = "--read-only"
auto_mount = false

[remotes."Hidden"]
enabled = false
""".strip(),
                encoding="utf-8",
            )
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive

[Hidden]
type = dropbox
""".strip(),
                set_mount_base=False,
            )

            remotes = core.load_remotes()

        self.assertEqual([remote.name for remote in remotes], ["Docs"])
        self.assertTrue(remotes[0].mount_path.endswith("/custom-docs"))
        self.assertFalse(remotes[0].auto_mount)
        self.assertIn("--read-only", remotes[0].flags)

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

            def which(name: str) -> str | None:
                return "/usr/bin/fusermount3" if name == "fusermount3" else None

            with mock.patch.object(core.shutil, "which", side_effect=which):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    success, _ = core.unmount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/fusermount3", "-u"])


if __name__ == "__main__":
    unittest.main()
