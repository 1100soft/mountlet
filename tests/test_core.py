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

    def test_mount_remote_rejects_non_empty_mount_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]
            Path(remote.mount_path).mkdir(parents=True, exist_ok=True)
            (Path(remote.mount_path) / "existing.txt").write_text("keep", encoding="utf-8")

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "_launch_mount_process") as launch:
                    success, message = core.mount_remote(remote)

            self.assertFalse(success)
            self.assertIn("is not empty", message)
            launch.assert_not_called()

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
mount_path = "custom-docs"
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
        self.assertTrue(Path(remotes[0].mount_path).is_absolute())
        self.assertFalse(remotes[0].auto_mount)
        self.assertIn("--read-only", remotes[0].flags)

    def test_load_remotes_uses_mountlet_order_when_configured(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config" / "mountlet"
            config_dir.mkdir(parents=True)
            (config_dir / "mounts.toml").write_text(
                """
[remotes."Photos"]
order = 0

[remotes."Docs"]
order = 1
""".strip(),
                encoding="utf-8",
            )
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive

[Photos]
type = dropbox
""".strip(),
            )

            remotes = core.load_remotes()

        self.assertEqual([remote.name for remote in remotes], ["Photos", "Docs"])

    def test_load_remotes_can_hide_incomplete_oauth_remotes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[PartialDrive]
type = drive

[ReadyDrive]
type = drive
token = REDACTED

[PartialDropbox]
type = dropbox

[PartialOneDrive]
type = onedrive
token = REDACTED

[ReadyOneDrive]
type = onedrive
token = REDACTED
drive_id = drive
drive_type = personal

[WebDav]
type = webdav
url = https://example.test
""".strip(),
            )

            remotes = core.load_remotes(include_incomplete=False)

        self.assertEqual([remote.name for remote in remotes], ["ReadyDrive", "ReadyOneDrive", "WebDav"])

    def test_storage_usage_uses_timeout(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core.subprocess, "check_output", return_value='{"used": 1}') as check_output:
                    usage = core.get_storage_usage_details(remote)

            self.assertEqual(usage.used, 1)
            self.assertEqual(check_output.call_args.kwargs["timeout"], core.RCLONE_STATUS_TIMEOUT_SECONDS)

    def test_editable_rclone_fields_are_safe_and_saveable(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive
root_folder_id = abc
token = REDACTED
""".strip(),
            )
            remote = core.load_remotes()[0]

            fields = core.editable_rclone_fields(remote)

            self.assertEqual(list(fields)[:4], ["shared_with_me", "root_folder_id", "team_drive", "scope"])
            self.assertEqual(fields["root_folder_id"], "abc")
            self.assertIn("team_drive", fields)
            self.assertNotIn("token", fields)

            core.save_rclone_fields("Docs", {"root_folder_id": "def", "token": "REDACTED"})
            remote = core.load_remotes()[0]

            self.assertEqual(remote.extra_info["root_folder_id"], "def")
            self.assertEqual(remote.extra_info["token"], "REDACTED")

    def test_delete_rclone_remote_removes_config_section(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive

[Photos]
type = dropbox
""".strip(),
            )

            self.assertTrue(core.delete_rclone_remote("Docs"))
            remotes = core.load_remotes()

        self.assertEqual([remote.name for remote in remotes], ["Photos"])

    def test_drive_oauth_credentials_reads_existing_drive_client_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive
client_id = docs-client
client_secret = docs-secret

[Blank]
type = drive
client_id =
client_secret =

[Other]
type = dropbox
client_id = other-client
client_secret = other-secret
""".strip(),
            )

            credentials = core.drive_oauth_credentials()

        self.assertEqual(len(credentials), 1)
        self.assertEqual(credentials[0].remote_name, "Docs")
        self.assertEqual(credentials[0].client_id, "docs-client")
        self.assertEqual(credentials[0].client_secret, "docs-secret")

    def test_drive_oauth_credentials_groups_duplicate_client_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs]
type = drive
client_id = shared-client
client_secret = shared-secret

[Photos]
type = drive
client_id = shared-client
client_secret = shared-secret

[Work]
type = drive
client_id = work-client
client_secret = work-secret
""".strip(),
            )

            credentials = core.drive_oauth_credentials()

        self.assertEqual([item.remote_name for item in credentials], ["Docs, +1", "Work"])
        self.assertEqual(credentials[0].remote_names, ("Docs", "Photos"))
        self.assertEqual(credentials[0].client_id, "shared-client")
        self.assertEqual(credentials[1].client_id, "work-client")

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

    def test_get_storage_usage_details_includes_numeric_percent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/custom/rclone"):
                with mock.patch.object(
                    core.subprocess,
                    "check_output",
                    return_value='{"used": 1073741824, "total": 2147483648}',
                ):
                    usage = core.get_storage_usage_details(remote)

            self.assertEqual(usage.text, "1.0 / 2.0 GB")
            self.assertEqual(usage.used, 1073741824)
            self.assertEqual(usage.total, 2147483648)
            self.assertEqual(usage.percent, 50)

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

    def test_unmount_remote_falls_back_to_lazy_unmount(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            def which(name: str) -> str | None:
                return "/usr/bin/fusermount3" if name == "fusermount3" else None

            with mock.patch.object(core.shutil, "which", side_effect=which):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.side_effect = [
                        mock.Mock(returncode=1),
                        mock.Mock(returncode=0),
                    ]
                    success, _ = core.unmount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/fusermount3", "-u", remote.mount_path])
            self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/fusermount3", "-uz", remote.mount_path])


if __name__ == "__main__":
    unittest.main()
