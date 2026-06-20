from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mountlet.platform_services import get_platform
from mountlet.platform_services.console import ConsoleServices
from mountlet.platform_services.desktop import DesktopServices
from mountlet.platform_services.file_managers import (
    FileManager,
    clear_file_manager_cache,
    default_file_manager_id,
    discover_file_managers,
    open_with_file_manager,
    resolve_file_manager,
)
from mountlet.platform_services.linux import LinuxPlatformServices
from mountlet.platform_services.macos import MacOSPlatformServices
from mountlet.platform_services.windows import WindowsPlatformServices


class PlatformServicesTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_file_manager_cache()

    def test_factory_selects_supported_platform_adapters(self):
        self.assertIsInstance(get_platform("Linux"), LinuxPlatformServices)
        self.assertIsInstance(get_platform("Windows"), WindowsPlatformServices)
        self.assertIsInstance(get_platform("Darwin"), MacOSPlatformServices)

    def test_linux_paths_follow_xdg_environment(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            environment = {
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
            }
            with mock.patch.dict("os.environ", environment, clear=True):
                paths = LinuxPlatformServices().user_directories("mountlet")

        self.assertEqual(paths.config, root / "config" / "mountlet")
        self.assertEqual(paths.state, root / "state" / "mountlet")
        self.assertEqual(paths.cache, root / "cache" / "mountlet")

    def test_windows_paths_separate_roaming_config_and_local_state(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            with mock.patch.dict(
                "os.environ",
                {
                    "APPDATA": str(root / "Roaming"),
                    "LOCALAPPDATA": str(root / "Local"),
                },
                clear=True,
            ):
                platform = WindowsPlatformServices()
                paths = platform.user_directories("mountlet")
                rclone_config = platform.default_rclone_config()

        self.assertEqual(paths.config, root / "Roaming" / "mountlet")
        self.assertEqual(paths.state, root / "Local" / "mountlet" / "State")
        self.assertEqual(rclone_config, root / "Roaming" / "rclone" / "rclone.conf")

    def test_windows_defaults_to_explorer(self):
        platform = WindowsPlatformServices()

        managers = discover_file_managers(platform)

        self.assertEqual(default_file_manager_id(platform), "explorer")
        self.assertEqual(managers[0].identifier, "explorer")
        self.assertEqual(managers[0].label, "File Explorer")

    def test_windows_finds_rclone_in_winget_links(self):
        with tempfile.TemporaryDirectory() as tempdir:
            local = Path(tempdir) / "Local"
            executable = local / "Microsoft" / "WinGet" / "Links" / "rclone.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(local)},
                clear=True,
            ):
                with mock.patch("mountlet.platform_services.base.shutil.which", return_value=None):
                    found = WindowsPlatformServices().find_rclone()

        self.assertEqual(found, str(executable))

    def test_windows_prefers_explicit_rclone_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            executable = Path(tempdir) / "portable" / "rclone.exe"
            executable.parent.mkdir()
            executable.touch()
            with mock.patch.dict(
                "os.environ",
                {"RCLONE_PATH": str(executable)},
                clear=True,
            ):
                found = WindowsPlatformServices().find_rclone()

        self.assertEqual(found, str(executable))

    def test_macos_defaults_to_finder(self):
        platform = MacOSPlatformServices()

        managers = discover_file_managers(platform)

        self.assertEqual(default_file_manager_id(platform), "finder")
        self.assertEqual(managers[0].identifier, "finder")
        self.assertEqual(managers[0].label, "Finder")

    def test_missing_saved_manager_falls_back_to_platform_default(self):
        manager = resolve_file_manager(WindowsPlatformServices(), "removed-manager")

        self.assertEqual(manager.identifier, "explorer")

    def test_linux_discovers_registered_directory_handlers(self):
        with tempfile.TemporaryDirectory() as tempdir:
            applications = Path(tempdir) / "applications"
            applications.mkdir()
            (applications / "org.example.Files.desktop").write_text(
                """
[Desktop Entry]
Type=Application
Name=Example Files
Exec=example-files %U
TryExec=example-files
MimeType=inode/directory;
Categories=Utility;FileManager;
""".strip(),
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0, stdout="org.example.Files.desktop\n")
            with mock.patch.dict(
                "os.environ",
                {"XDG_DATA_HOME": tempdir, "XDG_DATA_DIRS": ""},
                clear=False,
            ):
                with mock.patch(
                    "mountlet.platform_services.file_managers.shutil.which",
                    side_effect=lambda name: f"/usr/bin/{name}",
                ):
                    with mock.patch(
                        "mountlet.platform_services.file_managers.subprocess.run",
                        return_value=completed,
                    ):
                        managers = discover_file_managers(LinuxPlatformServices(), refresh=True)

        self.assertEqual(managers[0].identifier, "system")
        self.assertEqual(managers[0].label, "System default (Example Files)")
        self.assertEqual(managers[1].identifier, "org.example.Files.desktop")

    def test_file_manager_command_expands_desktop_path_placeholder(self):
        manager = FileManager("example", "Example", ("example-files", "%U"))

        with mock.patch("mountlet.platform_services.file_managers.subprocess.Popen") as popen:
            self.assertTrue(open_with_file_manager(manager, "/tmp/docs"))

        self.assertEqual(popen.call_args.args[0], ["example-files", "/tmp/docs"])

    def test_macos_paths_use_library_directories(self):
        platform = MacOSPlatformServices()
        with mock.patch("pathlib.Path.home", return_value=Path("/Users/tester")):
            paths = platform.user_directories("mountlet")
            mount_base = platform.default_mount_base()

        self.assertEqual(paths.config, Path("/Users/tester/Library/Application Support/mountlet"))
        self.assertEqual(paths.cache, Path("/Users/tester/Library/Caches/mountlet"))
        self.assertEqual(mount_base, Path("/Users/tester/Mountlet"))

    def test_windows_mountpoint_is_absent_before_rclone_mount(self):
        with tempfile.TemporaryDirectory() as tempdir:
            mountpoint = Path(tempdir) / "Mountlet" / "Work"
            mountpoint.mkdir(parents=True)

            result = WindowsPlatformServices().prepare_mount_path(str(mountpoint))

            self.assertTrue(result.success)
            self.assertFalse(mountpoint.exists())

    def test_windows_mountpoint_rejects_existing_content(self):
        with tempfile.TemporaryDirectory() as tempdir:
            mountpoint = Path(tempdir) / "Mountlet" / "Work"
            mountpoint.mkdir(parents=True)
            (mountpoint / "keep.txt").write_text("data", encoding="utf-8")

            result = WindowsPlatformServices().prepare_mount_path(str(mountpoint))

            self.assertFalse(result.success)
            self.assertIn("not empty", result.detail)

    def test_linux_autostart_uses_freedesktop_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "mountlet.desktop"
            LinuxPlatformServices().set_start_at_login(
                "mountlet",
                True,
                command=("mountlet", "tray"),
                destination=destination,
            )
            text = destination.read_text(encoding="utf-8")
            self.assertIn("Exec=mountlet tray", text)
            LinuxPlatformServices().set_start_at_login(
                "mountlet",
                False,
                command=("mountlet", "tray"),
                destination=destination,
            )
            self.assertFalse(destination.exists())

    def test_macos_autostart_uses_launch_agent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "mountlet.plist"
            MacOSPlatformServices().set_start_at_login(
                "mountlet",
                True,
                command=("mountlet", "tray"),
                destination=destination,
            )
            with destination.open("rb") as handle:
                data = plistlib.load(handle)

        self.assertEqual(data["ProgramArguments"], ["mountlet", "tray"])
        self.assertTrue(data["RunAtLoad"])

    def test_desktop_service_uses_qt_fallbacks(self):
        qt = SimpleNamespace(
            QDesktopServices=SimpleNamespace(openUrl=mock.Mock(return_value=True)),
            QUrl=SimpleNamespace(fromLocalFile=lambda path: f"file:{path}"),
        )
        desktop = DesktopServices(qt)

        self.assertTrue(desktop.open_folder("/tmp/docs"))
        self.assertTrue(desktop.open_text_file(Path("/tmp/config.toml")))
        self.assertEqual(desktop.file_manager_label(), "the file manager")
        self.assertIsNone(desktop.window_is_on_current_workspace(object()))

    def test_console_service_writes_portable_ansi_status(self):
        stream = mock.Mock()
        stream.isatty.return_value = True
        console = ConsoleServices(LinuxPlatformServices(), stream)

        console.print_status("", "mounted", "unmounted", True, color=True)

        written = "".join(call.args[0] for call in stream.write.call_args_list)
        self.assertIn("\033[92mmounted\033[0m", written)


if __name__ == "__main__":
    unittest.main()
