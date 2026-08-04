from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.platform_services.linux import LinuxPlatformServices


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = LinuxPlatformServices()
        self.platform.mount_driver_available = lambda: True
        patcher = mock.patch("mountlet.config_tools.shared.get_platform", return_value=self.platform)
        patcher.start()
        self.addCleanup(patcher.stop)

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

        core = importlib.reload(core)
        core.PLATFORM = self.platform
        core.DEFAULT_HOME_MOUNT = str(self.platform.default_mount_base())
        return core

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
            self.assertEqual(Path(remote.mount_path).parts[-2:], ("drive", "Work__Drive"))
            self.assertIn("--links", remote.flags)
            self.assertIn("--read-only", remote.flags)
            self.assertIn("10m", remote.flags)

    def test_import_does_not_create_mount_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            mount_base = Path(tempdir) / "mounts"
            self.load_core(tempdir, "[Docs]\ntype = drive\n")

            self.assertFalse(mount_base.exists())

    def test_default_mount_folders_distinguish_same_alias_across_providers(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Personal__Drive]
type = drive

[Personal__Dropbox]
type = dropbox
""".strip(),
            )

            remotes = core.load_remotes()

        self.assertEqual([remote.alias for remote in remotes], ["Personal", "Personal"])
        self.assertEqual(
            [Path(remote.mount_path).name for remote in remotes],
            ["Personal__Drive", "Personal__Dropbox"],
        )

    def test_mount_removes_empty_legacy_default_folder_before_mounting(self):
        with tempfile.TemporaryDirectory() as tempdir:
            (Path(tempdir) / "mounts").mkdir()
            core = self.load_core(tempdir, "[Work__Drive]\ntype = drive\n")
            remote = core.load_remotes()[0]
            legacy_path = Path(remote.mount_path).with_name("Work")
            legacy_path.mkdir(parents=True)

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core.PLATFORM, "mount_driver_available", return_value=True):
                    with mock.patch.object(core, "check_remote_connection", return_value=(True, "connected")):
                        with mock.patch.object(core, "_launch_mount_process", return_value=(True, "mounted")):
                            success, _message = core.mount_remote(remote)

            self.assertTrue(success)
            self.assertFalse(legacy_path.exists())

    def test_mount_preserves_nonempty_legacy_default_folder(self):
        with tempfile.TemporaryDirectory() as tempdir:
            (Path(tempdir) / "mounts").mkdir()
            core = self.load_core(tempdir, "[Work__Drive]\ntype = drive\n")
            remote = core.load_remotes()[0]
            legacy_path = Path(remote.mount_path).with_name("Work")
            legacy_path.mkdir(parents=True)
            (legacy_path / "local.txt").write_text("keep", encoding="utf-8")

            core._cleanup_legacy_default_mount(remote)

            self.assertTrue(legacy_path.exists())
            self.assertEqual((legacy_path / "local.txt").read_text(encoding="utf-8"), "keep")

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
                with mock.patch.object(core, "check_remote_connection", return_value=(True, "connected")):
                    with mock.patch.object(core, "_launch_mount_process", return_value=(True, "mounted")) as launch:
                        success, message = core.mount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(message, "mounted")
            args = launch.call_args.args[1]
            self.assertEqual(args[:5], ["/usr/bin/rclone", "--config", core.CONFIG_PATH, "mount", "Docs:"])
            self.assertEqual(args[5], remote.mount_path)
            self.assertIn("--vfs-cache-mode", args)
            self.assertEqual(launch.call_args.kwargs["wait_timeout"], self.platform.mount_start_timeout_seconds())

    def test_mount_remote_can_mount_remote_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config" / "mountlet"
            config_dir.mkdir(parents=True)
            (config_dir / "mounts.toml").write_text(
                '[remotes."R2__S3"]\nremote_path = "bucket/prefix"\n',
                encoding="utf-8",
            )
            core = self.load_core(tempdir, "[R2__S3]\ntype = s3\n", set_mount_base=False)
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "check_remote_connection", return_value=(True, "connected")):
                    with mock.patch.object(core, "_launch_mount_process", return_value=(True, "mounted")) as launch:
                        success, _message = core.mount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(
                launch.call_args.args[1][:5],
                ["/usr/bin/rclone", "--config", core.CONFIG_PATH, "mount", "R2__S3:bucket/prefix"],
            )

    def test_mount_remote_does_not_start_mount_when_connection_check_fails(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Archive__S3]\ntype = s3\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "_launch_mount_process", return_value=(True, "mounted")) as launch:
                    with mock.patch.object(
                        core,
                        "check_remote_connection",
                        return_value=(False, "[!] Archive (S3) is not connected."),
                    ):
                        success, message = core.mount_remote(remote)

            self.assertFalse(success)
            self.assertIn("not connected", message)
            launch.assert_not_called()

    def test_mount_remote_reports_missing_optional_mount_driver(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]
            core.PLATFORM.mount_driver_available = lambda: False
            core.PLATFORM.prerequisite_guidance = lambda: (
                "Install rclone.",
                "Install FUSE: sudo apt install fuse3",
            )

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "check_remote_connection") as check_connection:
                    success, message = core.mount_remote(remote)

            self.assertFalse(success)
            self.assertIn("Native folder mounting is not available", message)
            self.assertIn("Mountlet Files can browse", message)
            check_connection.assert_not_called()

    def test_check_remote_connection_uses_remote_source(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_dir = Path(tempdir) / "config" / "mountlet"
            config_dir.mkdir(parents=True)
            (config_dir / "mounts.toml").write_text(
                '[remotes."R2__S3"]\nremote_path = "bucket/prefix"\n',
                encoding="utf-8",
            )
            core = self.load_core(tempdir, "[R2__S3]\ntype = s3\n", set_mount_base=False)
            remote = core.load_remotes()[0]

            with mock.patch.object(core.subprocess, "run") as run:
                run.return_value.returncode = 0
                success, message = core.check_remote_connection(remote, "/usr/bin/rclone")

            self.assertTrue(success)
            self.assertIn("connected", message)
            self.assertEqual(
                run.call_args.args[0],
                [
                    "/usr/bin/rclone",
                    "--config",
                    core.CONFIG_PATH,
                    "lsf",
                    "R2__S3:bucket/prefix",
                    "--max-depth",
                    "1",
                ],
            )

    def test_check_remote_connection_explains_missing_backend_binary(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Personal__MEGA]\ntype = mega\n")
            remote = core.load_remotes()[0]
            result = mock.Mock(
                returncode=1,
                stderr='Failed to create file system: didn\'t find backend called "mega"',
            )

            with mock.patch.object(core.subprocess, "run", return_value=result):
                success, message = core.check_remote_connection(remote, "/usr/bin/rclone")

            self.assertFalse(success)
            self.assertIn("/usr/bin/rclone", message)
            self.assertIn("does not include the mega backend", message)
            self.assertIn("standard bundled-rclone build", message)

    def test_check_remote_connection_explains_invalid_icloud_session(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Personal__iCloud]\ntype = iclouddrive\n")
            remote = core.load_remotes()[0]
            result = mock.Mock(
                returncode=1,
                stderr=(
                    'HTTP error 421 (421 Misdirected Request) returned body: '
                    '''{"reason":"Invalid global session","error":2}'''
                ),
            )

            with mock.patch.object(core.subprocess, "run", return_value=result):
                success, message = core.check_remote_connection(remote, "/official/rclone")

            self.assertFalse(success)
            self.assertIn("saved iCloud session", message)
            self.assertIn("Reauthenticate", message)
            self.assertIn("iCloud.com", message)

    def test_check_remote_connection_explains_missing_icloud_adp_cookie(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Personal__iCloud]\ntype = iclouddrive\n")
            remote = core.load_remotes()[0]
            result = mock.Mock(
                returncode=1,
                stderr=(
                    "requestPCS(iclouddrive): HTTP error 500 returned body: "
                    '''{"success":false,"error":"Missing X-APPLE-WEBAUTH-TOKEN cookie"}'''
                ),
            )

            with mock.patch.object(core.subprocess, "run", return_value=result):
                success, message = core.check_remote_connection(remote, "/official/rclone")

            self.assertFalse(success)
            self.assertIn("Reauthenticate", message)
            self.assertIn("Advanced Data Protection", message)

    def test_reconnect_remote_uses_active_rclone_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\ntoken = REDACTED\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    run.return_value.stdout = ""
                    run.return_value.stderr = ""
                    success, message = core.reconnect_remote(remote)

            self.assertTrue(success)
            self.assertIn("reauthenticated", message)
            self.assertEqual(
                run.call_args.args[0],
                [
                    "/usr/bin/rclone",
                    "--config",
                    core.CONFIG_PATH,
                    "config",
                    "reconnect",
                    "Docs:",
                    "--auto-confirm",
                ],
            )

    def test_mount_remote_rejects_non_empty_mount_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]
            Path(remote.mount_path).mkdir(parents=True, exist_ok=True)
            (Path(remote.mount_path) / "existing.txt").write_text("keep", encoding="utf-8")

            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core, "check_remote_connection", return_value=(True, "connected")):
                    with mock.patch.object(core, "_launch_mount_process") as launch:
                        success, message = core.mount_remote(remote)

            self.assertFalse(success)
            self.assertIn("contains local files", message)
            launch.assert_not_called()

    def test_launch_mount_process_preserves_complete_rclone_error(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]
            process = mock.Mock(pid=42)
            process.poll.return_value = 1

            def failed_process(_args, *, stderr, **_kwargs):
                stderr.write("first diagnostic line\nfinal diagnostic line\n")
                stderr.flush()
                return process

            with mock.patch.object(core.rclone_log, "append_raw") as append_raw:
                with mock.patch.object(core.subprocess, "Popen", side_effect=failed_process):
                    with mock.patch.object(core, "wait_for", return_value=False):
                        success, message = core._launch_mount_process(
                            remote,
                            ["rclone", "mount"],
                            wait_timeout=0,
                        )

            self.assertFalse(success)
            self.assertIn("rclone exited with code 1", message)
            self.assertIn("first diagnostic line", message)
            self.assertIn("final diagnostic line", message)
            self.assertGreaterEqual(append_raw.call_count, 2)

    def test_launch_mount_process_rolls_back_a_late_partial_mount(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]
            process = mock.Mock(pid=42)
            process.poll.return_value = 1
            unmount_result = mock.Mock(success=True)

            with mock.patch.object(core.subprocess, "Popen", return_value=process):
                with mock.patch.object(core, "wait_for", return_value=False):
                    with mock.patch.object(core, "is_mounted", side_effect=(True, False)):
                        with mock.patch.object(core.PLATFORM, "unmount", return_value=unmount_result) as unmount:
                            with mock.patch.object(core, "_cleanup_mount_dir") as cleanup:
                                success, _message = core._launch_mount_process(
                                    remote,
                                    ["rclone", "mount"],
                                    wait_timeout=0,
                                )

            self.assertFalse(success)
            unmount.assert_called_once_with(remote.mount_path)
            cleanup.assert_called_once_with(remote.mount_path)

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
remote_path = "bucket/docs"
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
        self.assertEqual(Path(remotes[0].mount_path).name, "custom-docs")
        self.assertEqual(remotes[0].remote_path, "bucket/docs")
        self.assertTrue(Path(remotes[0].mount_path).is_absolute())
        self.assertFalse(remotes[0].auto_mount)
        self.assertIn("--read-only", remotes[0].flags)

    def test_load_remotes_displays_s3_provider_name_without_changing_mount_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Archive__S3]
type = s3
provider = Cloudflare
access_key_id = key
secret_access_key = secret
endpoint = https://account.r2.cloudflarestorage.com
""".strip(),
            )

            remote = core.load_remotes()[0]

        self.assertEqual(remote.provider, "Cloudflare R2")
        self.assertEqual(Path(remote.mount_path).parts[-2:], ("s3", "Archive__S3"))

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

[PartialS3]
type = s3
provider = Cloudflare
access_key_id = key
secret_access_key = secret

[ReadyS3]
type = s3
provider = Minio
access_key_id = minioadmin
secret_access_key = minioadmin
endpoint = http://127.0.0.1:9000

[PartialWebDav]
type = webdav

[WebDav]
type = webdav
url = https://example.test

[PartialKoofr]
type = koofr
provider = koofr
user = eric@example.com

[Koofr]
type = koofr
provider = koofr
user = eric@example.com
password = REDACTED

[PartialICloud]
type = iclouddrive
apple_id = eric@example.com

[ICloud]
type = iclouddrive
apple_id = eric@example.com
password = REDACTED

[PartialGooglePhotos]
type = gphotos

[GooglePhotos]
type = gphotos
token = {"access_token":"token"}
""".strip(),
            )

            remotes = core.load_remotes(include_incomplete=False)

        self.assertEqual(
            [remote.name for remote in remotes],
            ["ReadyDrive", "ReadyOneDrive", "ReadyS3", "WebDav", "Koofr", "ICloud", "GooglePhotos"],
        )

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

            self.assertEqual(
                list(fields)[:7],
                [
                    "mountlet_google_account",
                    "client_id",
                    "client_secret",
                    "shared_with_me",
                    "root_folder_id",
                    "team_drive",
                    "scope",
                ],
            )
            self.assertEqual(fields["root_folder_id"], "abc")
            self.assertIn("team_drive", fields)
            self.assertNotIn("token", fields)

            core.save_rclone_fields(
                "Docs",
                {
                    "client_id": "client.apps.googleusercontent.com",
                    "client_secret": "new-secret",
                    "root_folder_id": "def",
                    "mountlet_google_account": "person+drive@example.com",
                    "token": "REDACTED",
                },
            )
            remote = core.load_remotes()[0]

            self.assertEqual(remote.extra_info["client_id"], "client.apps.googleusercontent.com")
            self.assertEqual(remote.extra_info["client_secret"], "new-secret")
            self.assertEqual(remote.extra_info["root_folder_id"], "def")
            self.assertEqual(remote.extra_info["mountlet_google_account"], "person+drive@example.com")
            self.assertIn("login_hint=person%2Bdrive%40example.com", remote.extra_info["auth_url"])
            self.assertEqual(remote.extra_info["token"], "REDACTED")

    def test_s3_credentials_are_editable_and_new_secrets_are_obscured(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Archive__CloudflareR2]
type = s3
provider = Cloudflare
endpoint = https://example.r2.cloudflarestorage.com
access_key_id = old-key
secret_access_key = obscured-old-secret
""".strip(),
            )
            remote = core.load_remotes()[0]

            fields = core.editable_rclone_fields(remote)

            self.assertEqual(fields["access_key_id"], "old-key")
            self.assertEqual(fields["secret_access_key"], "obscured-old-secret")
            self.assertIn("session_token", fields)
            completed = mock.Mock(returncode=0, stdout="obscured-new-secret\n", stderr="")
            with mock.patch.object(core, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(core.subprocess, "run", return_value=completed) as run:
                    core.save_rclone_fields(
                        remote.name,
                        {
                            "access_key_id": "new-key",
                            "secret_access_key": "new-secret",
                        },
                    )

            updated = core.load_remotes()[0]
            self.assertEqual(updated.extra_info["access_key_id"], "new-key")
            self.assertEqual(updated.extra_info["secret_access_key"], "obscured-new-secret")
            self.assertEqual(
                run.call_args.args[0],
                ["/usr/bin/rclone", "obscure", "new-secret"],
            )
            self.assertEqual(run.call_args.kwargs["timeout"], core.RCLONE_OBSCURE_TIMEOUT_SECONDS)

    def test_unchanged_s3_secret_is_not_obscured_again(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                "[Archive__CloudflareR2]\ntype = s3\nsecret_access_key = obscured-secret\n",
            )

            with mock.patch.object(core, "_obscure_rclone_secret") as obscure:
                core.save_rclone_fields(
                    "Archive__CloudflareR2",
                    {"secret_access_key": "obscured-secret"},
                )

            obscure.assert_not_called()

    def test_equivalent_false_rclone_boolean_does_not_rewrite_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                "[Archive__CloudflareR2]\ntype = s3\nprovider = Cloudflare\nenv_auth = false\n",
            )

            with mock.patch.object(core, "_save_config") as save:
                core.save_rclone_fields("Archive__CloudflareR2", {"env_auth": ""})

            save.assert_not_called()

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

    def test_rename_rclone_remote_alias_preserves_provider_suffix_and_section_order(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Alpha__Drive]
type = drive
root_folder_id = abc

[Photos__Dropbox]
type = dropbox
""".strip(),
            )

            renamed = core.rename_rclone_remote_alias("Alpha__Drive", "Docs")
            remotes = core.load_remotes()

        self.assertEqual(renamed, "Docs__Drive")
        self.assertEqual([remote.name for remote in remotes], ["Docs__Drive", "Photos__Dropbox"])
        self.assertEqual(remotes[0].alias, "Docs")
        self.assertEqual(remotes[0].provider, "Drive")
        self.assertEqual(remotes[0].extra_info["root_folder_id"], "abc")

    def test_rename_rclone_remote_alias_rejects_existing_or_invalid_names(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(
                tempdir,
                """
[Docs__Drive]
type = drive

[Photos__Drive]
type = drive
""".strip(),
            )

            with self.assertRaises(ValueError):
                core.rename_rclone_remote_alias("Docs__Drive", "Photos")
            with self.assertRaises(ValueError):
                core.rename_rclone_remote_alias("Docs__Drive", "Bad/Name")

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
            self.assertEqual(
                check_output.call_args.args[0][:3],
                ["/custom/rclone", "--config", core.CONFIG_PATH],
            )

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

            with mock.patch("mountlet.platform_services.linux.shutil.which", side_effect=which):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.return_value.returncode = 0
                    success, _ = core.unmount_remote(remote)

            self.assertTrue(success)
            self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/fusermount3", "-u"])

    def test_unmount_remote_does_not_fall_back_to_lazy_unmount(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            def which(name: str) -> str | None:
                return "/usr/bin/fusermount3" if name == "fusermount3" else None

            with mock.patch("mountlet.platform_services.linux.shutil.which", side_effect=which):
                with mock.patch.object(core.subprocess, "run") as run:
                    run.side_effect = [
                        mock.Mock(returncode=1),
                    ]
                    success, message = core.unmount_remote(remote)

            self.assertFalse(success)
            self.assertIn("Close files or folders", message)
            self.assertEqual(run.call_args_list[0].args[0], ["/usr/bin/fusermount3", "-u", remote.mount_path])
            self.assertEqual(run.call_count, 1)

    def test_refresh_remote_does_not_mount_after_unmount_failure(self):
        with tempfile.TemporaryDirectory() as tempdir:
            core = self.load_core(tempdir, "[Docs]\ntype = drive\n")
            remote = core.load_remotes()[0]

            with mock.patch.object(core, "is_mounted", return_value=True):
                with mock.patch.object(core, "unmount_remote", return_value=(False, "mount is busy")):
                    with mock.patch.object(core, "mount_remote") as mount:
                        success, message = core.refresh_remote(remote)

            self.assertFalse(success)
            self.assertEqual(message, "mount is busy")
            mount.assert_not_called()


if __name__ == "__main__":
    unittest.main()
