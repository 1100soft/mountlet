from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import settings
from mountlet.config_tools import bundle_file, export_config, import_config, path_config, shared, verify_config
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
        self.assertTrue(args.mountlet_settings)

    def test_export_parser_includes_mountlet_settings_by_default(self):
        args = export_config.build_parser().parse_args(["bundle"])

        self.assertTrue(args.mountlet_settings)

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

    def test_export_bundle_includes_mountlet_mount_settings(self):
        with tempfile.TemporaryDirectory() as tempdir:
            source_config = Path(tempdir) / "rclone.conf"
            destination = Path(tempdir) / "bundle"
            source_config.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
            env = {"XDG_CONFIG_HOME": str(Path(tempdir) / "config")}
            with mock.patch.dict("os.environ", env, clear=False):
                mounts = shared.app_mounts_file()
                mounts.parent.mkdir(parents=True, exist_ok=True)
                mounts.write_text('[remotes."Docs"]\nremote_path = "bucket"\n', encoding="utf-8")
                args = export_config.build_parser().parse_args([str(destination), "--config", str(source_config)])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(export_config.export_bundle(args), 0)

            self.assertEqual((destination / "rclone.conf").read_text(encoding="utf-8"), "[Docs]\ntype = drive\n")
            self.assertEqual(
                (destination / "mounts.toml").read_text(encoding="utf-8"),
                '[remotes."Docs"]\nremote_path = "bucket"\n',
            )

    def test_import_bundle_restores_mountlet_mount_settings_next_to_config(self):
        with tempfile.TemporaryDirectory() as tempdir:
            bundle = Path(tempdir) / "bundle"
            bundle.mkdir()
            source_config = bundle / "rclone.conf"
            source_mounts = bundle / "mounts.toml"
            target_config = Path(tempdir) / "target" / "rclone.conf"
            source_config.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
            source_mounts.write_text('[remotes."Docs"]\nremote_path = "bucket"\n', encoding="utf-8")
            env = {"XDG_CONFIG_HOME": str(Path(tempdir) / "config")}

            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch.object(import_config, "default_config_path", return_value=target_config):
                    with mock.patch.object(import_config, "run_rclone_version", return_value="rclone v1"):
                        args = import_config.build_parser().parse_args(
                            ["--config", str(source_config), "--no-verify"]
                        )
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(import_config.import_bundle(args), 0)
                imported_mounts = shared.app_mounts_file().read_text(encoding="utf-8")

            self.assertEqual(target_config.read_text(encoding="utf-8"), "[Docs]\ntype = drive\n")
            self.assertEqual(imported_mounts, '[remotes."Docs"]\nremote_path = "bucket"\n')

    def test_single_file_bundle_contains_rclone_mountlet_settings_and_secrets(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            rclone_config.parent.mkdir()
            rclone_config.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
            (rclone_config.parent / "client_secret_demo.json").write_text("{}", encoding="utf-8")
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                shared.app_config_file().parent.mkdir(parents=True)
                shared.app_config_file().write_text("[app]\nauto_mount = false\n", encoding="utf-8")
                shared.app_mounts_file().write_text('[remotes."Docs"]\nremote_path = "bucket"\n', encoding="utf-8")
                destination = bundle_file.export_bundle_file(root / "backup")

            self.assertEqual(destination.suffix, ".mountlet")
            with zipfile.ZipFile(destination) as archive:
                self.assertIn("rclone.conf", archive.namelist())
                self.assertIn("config.toml", archive.namelist())
                self.assertIn("mounts.toml", archive.namelist())
                self.assertIn("secrets/client_secret_demo.json", archive.namelist())
                manifest = bundle_file.bundle_metadata(destination)
                self.assertEqual(manifest["format"], "mountlet-config-bundle")
                self.assertIn("created_at", manifest)
                self.assertIn("device", manifest)
                self.assertIn("system", manifest)
                self.assertIn("system_release", manifest)
                self.assertIn("platform", manifest)
                self.assertIn("config_hash", manifest)

    def test_encrypted_single_file_bundle_hides_config_and_imports_with_password(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            destination = root / "secure.mountlet"
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                rclone_config.parent.mkdir()
                rclone_config.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
                bundle_file.export_bundle_file(destination, password="secret")

                with zipfile.ZipFile(destination) as archive:
                    self.assertIn("manifest.json", archive.namelist())
                    self.assertIn("payload.bin", archive.namelist())
                    self.assertNotIn("rclone.conf", archive.namelist())
                self.assertTrue(bundle_file.is_encrypted_bundle(destination))
                public_metadata = bundle_file.bundle_metadata(destination)
                self.assertTrue(public_metadata["encrypted"])
                self.assertIn("created_at", public_metadata)
                self.assertIn("device", public_metadata)
                self.assertIn("system", public_metadata)
                self.assertIn("system_release", public_metadata)
                self.assertIn("platform", public_metadata)
                self.assertIn("config_hash", public_metadata)

                rclone_config.write_text("[Old]\ntype = s3\n", encoding="utf-8")
                with self.assertRaises(bundle_file.BundlePasswordRequired):
                    bundle_file.import_bundle_file(destination, backup=False)
                with self.assertRaises(bundle_file.BundlePasswordInvalid):
                    bundle_file.import_bundle_file(destination, backup=False, password="wrong")

                bundle_file.import_bundle_file(destination, backup=False, password="secret")

            self.assertEqual(rclone_config.read_text(encoding="utf-8"), "[Docs]\ntype = drive\n")

    def test_config_fingerprint_tracks_shared_app_preferences_only(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                rclone_config.parent.mkdir()
                rclone_config.write_text("[Docs]\ntype = drive\n", encoding="utf-8")
                shared.app_config_file().parent.mkdir(parents=True)
                shared.app_config_file().write_text(
                    '[app]\nmount_base = "/local/mounts"\nauto_mount = false\n\n[tray]\nfile_manager = "dolphin"\n\n'
                    '[sync]\nconfig_remote = "Docs"\nconfig_path = "Mountlet/config.mountlet"\n',
                    encoding="utf-8",
                )
                before = bundle_file.current_config_fingerprint()
                shared.app_config_file().write_text(
                    '[app]\nmount_base = "/other/mounts"\nauto_mount = false\n\n[tray]\nfile_manager = "nautilus"\n\n'
                    '[sync]\nconfig_remote = "Docs"\nconfig_path = "Mountlet/config.mountlet"\n',
                    encoding="utf-8",
                )
                after_local_change = bundle_file.current_config_fingerprint()
                shared.app_config_file().write_text(
                    '[app]\nauto_mount = false\n\n[sync]\nconfig_remote = "Other"\n'
                    'config_path = "Other/config.mountlet"\n',
                    encoding="utf-8",
                )
                after_shared_change = bundle_file.current_config_fingerprint()

            self.assertEqual(before, after_local_change)
            self.assertNotEqual(before, after_shared_change)

    def test_config_fingerprint_ignores_rclone_oauth_token_refresh(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                rclone_config.parent.mkdir()
                rclone_config.write_text(
                    '[Docs]\ntype = drive\nclient_id = abc\ntoken = {"access_token":"one"}\n',
                    encoding="utf-8",
                )
                before = bundle_file.current_config_fingerprint()
                rclone_config.write_text(
                    '[Docs]\ntype = drive\nclient_id = abc\ntoken = {"access_token":"two"}\n',
                    encoding="utf-8",
                )
                after_token_refresh = bundle_file.current_config_fingerprint()
                rclone_config.write_text(
                    '[Docs]\ntype = drive\nclient_id = def\ntoken = {"access_token":"two"}\n',
                    encoding="utf-8",
                )
                after_user_change = bundle_file.current_config_fingerprint()

            self.assertEqual(before, after_token_refresh)
            self.assertNotEqual(before, after_user_change)

    def test_single_file_bundle_import_preserves_platform_specific_app_settings(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            source_bundle = root / "source.mountlet"
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                rclone_config.parent.mkdir()
                rclone_config.write_text("[Old]\ntype = drive\n", encoding="utf-8")
                shared.app_config_file().parent.mkdir(parents=True)
                shared.app_config_file().write_text(
                    '[app]\nmount_base = "/local/mounts"\nauto_mount = false\n'
                    'auto_mount_delay = 2\nstart_at_login = true\nintegrated_file_edits = false\n\n'
                    '[tray]\nfile_manager = "dolphin"\nopen_folder_behavior = "current_desktop"\n'
                    'focus_file_manager = false\n\n'
                    '[sync]\nconfig_remote = "Old"\nconfig_path = "Old/config.mountlet"\n\n'
                    '[shortcuts]\nbrowser_refresh = "F5"\n',
                    encoding="utf-8",
                )
                with zipfile.ZipFile(source_bundle, "w") as archive:
                    archive.writestr("manifest.json", '{"format":"mountlet-config-bundle","version":1}')
                    archive.writestr("rclone.conf", "[New]\ntype = s3\n")
                    archive.writestr(
                        "config.toml",
                        '[app]\nmount_base = "/remote/mounts"\nauto_mount = true\n'
                        'auto_mount_delay = 5\nstart_at_login = false\nintegrated_file_edits = true\n\n'
                        '[tray]\nfile_manager = "explorer"\nopen_folder_behavior = "new_window"\n'
                        'focus_file_manager = true\n\n'
                        '[sync]\nconfig_remote = "Docs"\nconfig_path = "Mountlet/config.mountlet"\n\n'
                        '[shortcuts]\nbrowser_refresh = "Ctrl+R"\n',
                    )

                bundle_file.import_bundle_file(source_bundle, backup=False)
                imported = settings.load_app_settings(shared.app_config_file())

            self.assertEqual(imported.mount_base, "/local/mounts")
            self.assertTrue(imported.start_at_login)
            self.assertEqual(imported.file_manager, "dolphin")
            self.assertEqual(imported.open_folder_behavior, "current_desktop")
            self.assertFalse(imported.focus_file_manager)
            self.assertTrue(imported.auto_mount)
            self.assertEqual(imported.auto_mount_delay, 5)
            self.assertTrue(imported.integrated_file_edits)
            self.assertEqual(imported.config_sync_remote, "Docs")
            self.assertEqual(imported.config_sync_path, "Mountlet/config.mountlet")
            self.assertEqual(imported.shortcuts["browser_refresh"], ("Ctrl+R",))

    def test_single_file_bundle_import_creates_restorable_backup_archive(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            rclone_config = root / "rclone" / "rclone.conf"
            source_bundle = root / "source.mountlet"
            env = {
                "RCLONE_CONFIG": str(rclone_config),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                rclone_config.parent.mkdir()
                rclone_config.write_text("[Old]\ntype = drive\n", encoding="utf-8")
                shared.app_config_file().parent.mkdir(parents=True)
                shared.app_config_file().write_text("[app]\nauto_mount = false\n", encoding="utf-8")
                shared.app_mounts_file().write_text('[remotes."Old"]\nremote_path = ""\n', encoding="utf-8")
                with zipfile.ZipFile(source_bundle, "w") as archive:
                    archive.writestr("manifest.json", '{"format":"mountlet-config-bundle","version":1}')
                    archive.writestr("rclone.conf", "[New]\ntype = s3\n")
                    archive.writestr("config.toml", "[app]\nauto_mount = true\n")
                    archive.writestr("mounts.toml", '[remotes."New"]\nremote_path = "bucket"\n')

                backup = bundle_file.import_bundle_file(source_bundle)

                self.assertEqual(rclone_config.read_text(encoding="utf-8"), "[New]\ntype = s3\n")
                self.assertTrue(settings.load_app_settings(shared.app_config_file()).auto_mount)
                self.assertEqual(shared.app_mounts_file().read_text(encoding="utf-8"), '[remotes."New"]\nremote_path = "bucket"\n')

            self.assertIsNotNone(backup)
            assert backup is not None
            with zipfile.ZipFile(backup) as archive:
                old_rclone = archive.read("rclone.conf").decode("utf-8").replace("\r\n", "\n")
                old_app_config = archive.read("config.toml").decode("utf-8").replace("\r\n", "\n")
                self.assertEqual(old_rclone, "[Old]\ntype = drive\n")
                self.assertEqual(old_app_config, "[app]\nauto_mount = false\n")

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
