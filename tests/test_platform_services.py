from __future__ import annotations

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
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
from mountlet.platform_services.processes import terminate_process
from mountlet.platform_services.processes import external_process_environment
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
                platform = WindowsPlatformServices()
                with mock.patch.object(platform, "bundled_rclone_candidates", return_value=()):
                    with mock.patch("mountlet.platform_services.base.shutil.which", return_value=None):
                        found = platform.find_rclone()

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

    def test_bundled_rclone_is_preferred_before_system_path(self):
        with tempfile.TemporaryDirectory() as tempdir:
            bundle_root = Path(tempdir) / "bundle"
            executable_dir = Path(tempdir) / "app"
            bundled = bundle_root / "vendor" / "rclone" / "rclone"
            bundled.parent.mkdir(parents=True)
            bundled.touch()
            executable = executable_dir / "Mountlet"
            executable_dir.mkdir()
            executable.touch()

            with mock.patch("mountlet.platform_services.base.sys.frozen", True, create=True):
                with mock.patch("mountlet.platform_services.base.sys._MEIPASS", str(bundle_root), create=True):
                    with mock.patch("mountlet.platform_services.base.sys.executable", str(executable)):
                        with mock.patch.dict("os.environ", {}, clear=True):
                            with mock.patch("mountlet.platform_services.base.shutil.which", return_value="/usr/bin/rclone"):
                                found = LinuxPlatformServices().find_rclone()

        self.assertEqual(found, str(bundled))

    def test_windows_forced_process_shutdown_does_not_require_posix_signals(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("rclone", 1), 0]

        terminate_process(process, WindowsPlatformServices(), timeout=1)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_frozen_external_process_restores_original_library_path(self):
        with mock.patch("mountlet.platform_services.processes.sys.frozen", True, create=True):
            with mock.patch.dict(
                "os.environ",
                {"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/system"},
                clear=True,
            ):
                environment = external_process_environment()

        self.assertEqual(environment["LD_LIBRARY_PATH"], "/system")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", environment)

    def test_source_external_process_preserves_library_path(self):
        with mock.patch("mountlet.platform_services.processes.sys.frozen", False, create=True):
            with mock.patch.dict("os.environ", {"LD_LIBRARY_PATH": "/custom"}, clear=True):
                environment = external_process_environment()

        self.assertEqual(environment["LD_LIBRARY_PATH"], "/custom")

    def test_macos_defaults_to_finder(self):
        platform = MacOSPlatformServices()

        managers = discover_file_managers(platform)

        self.assertEqual(default_file_manager_id(platform), "finder")
        self.assertEqual(managers[0].identifier, "finder")
        self.assertEqual(managers[0].label, "Finder")

    def test_macos_checks_homebrew_rclone_locations(self):
        self.assertEqual(
            MacOSPlatformServices().rclone_candidates(),
            (
                Path("/opt/homebrew/bin/rclone"),
                Path("/usr/local/bin/rclone"),
            ),
        )

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

    def test_windows_explorer_opens_location_directly(self):
        manager = FileManager("explorer", "File Explorer", ("explorer.exe",), True, True)

        with mock.patch("mountlet.platform_services.file_managers.subprocess.Popen") as popen:
            self.assertTrue(open_with_file_manager(manager, r"C:\Users\test\Mountlet\Docs"))

        self.assertEqual(
            popen.call_args.args[0],
            ["explorer.exe", r"C:\Users\test\Mountlet\Docs"],
        )

    def test_windows_desktop_service_opens_files_with_shell(self):
        qt = SimpleNamespace(
            QDesktopServices=SimpleNamespace(openUrl=mock.Mock(return_value=False)),
            QUrl=SimpleNamespace(fromLocalFile=lambda path: f"file:{path}"),
        )
        desktop = DesktopServices(qt)

        with mock.patch("mountlet.platform_services.desktop.platform.system", return_value="Windows"):
            with mock.patch("mountlet.platform_services.desktop.os.startfile", create=True) as startfile:
                self.assertTrue(desktop.open_file(Path(r"C:\Users\test\Mountlet\Docs\a.txt")))

        startfile.assert_called_once_with(r"C:\Users\test\Mountlet\Docs\a.txt")
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_linux_desktop_service_opens_files_with_system_association(self):
        qt = SimpleNamespace(
            QDesktopServices=SimpleNamespace(openUrl=mock.Mock(return_value=False)),
            QUrl=SimpleNamespace(fromLocalFile=lambda path: f"file:{path}"),
        )
        desktop = DesktopServices(qt)

        with mock.patch("mountlet.platform_services.desktop.platform.system", return_value="Linux"):
            with mock.patch("mountlet.platform_services.desktop.shutil.which", return_value="/usr/bin/xdg-open"):
                with mock.patch("mountlet.platform_services.desktop.subprocess.Popen") as popen:
                    self.assertTrue(desktop.open_file(PurePosixPath("/tmp/report.ods")))

        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "/tmp/report.ods"])
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_macos_paths_use_library_directories(self):
        platform = MacOSPlatformServices()
        with mock.patch("pathlib.Path.home", return_value=Path("/Users/tester")):
            paths = platform.user_directories("mountlet")
            mount_base = platform.default_mount_base()

        self.assertEqual(paths.config, Path("/Users/tester/Library/Application Support/mountlet"))
        self.assertEqual(paths.cache, Path("/Users/tester/Library/Caches/mountlet"))
        self.assertEqual(mount_base, Path("/Users/tester/Mountlet/mounted"))

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

    def test_windows_mount_detection_uses_nonblocking_volume_api(self):
        platform = WindowsPlatformServices()
        with mock.patch("mountlet.platform_services.windows.subprocess.run") as run:
            with mock.patch.object(platform, "_is_volume_mountpoint", return_value=True) as fallback:
                self.assertTrue(platform.is_mounted(r"C:\Users\test\Mountlet\Docs"))

        fallback.assert_called_once_with(r"C:\Users\test\Mountlet\Docs")
        run.assert_not_called()

    def test_windows_mount_detection_uses_reparse_point_when_volume_api_lags(self):
        platform = WindowsPlatformServices()
        with mock.patch.object(platform, "_is_volume_mountpoint", return_value=False):
            with mock.patch.object(platform, "_is_mount_reparse_point", return_value=True) as reparse:
                self.assertTrue(platform.is_mounted(r"C:\Users\test\Mountlet\Docs"))

        reparse.assert_called_once_with(r"C:\Users\test\Mountlet\Docs")

    def test_windows_mount_detection_does_not_depend_on_python_path_exists(self):
        platform = WindowsPlatformServices()
        with mock.patch("mountlet.platform_services.windows.os.path.exists", return_value=False):
            with mock.patch.object(platform, "_is_volume_mountpoint", return_value=True):
                self.assertTrue(platform.is_mounted(r"C:\Users\test\Mountlet\Docs"))

    def test_windows_volume_mountpoint_api_checks_requested_directory(self):
        requested = []

        def get_volume_name(path, buffer, _length):
            requested.append(path)
            buffer.value = "\\\\?\\Volume{mountlet}\\"
            return 1

        kernel32 = SimpleNamespace(GetVolumeNameForVolumeMountPointW=get_volume_name)
        with mock.patch(
            "mountlet.platform_services.windows.ctypes.windll",
            SimpleNamespace(kernel32=kernel32),
            create=True,
        ):
            mounted = WindowsPlatformServices._is_volume_mountpoint(r"C:\Users\test\Mountlet\Docs")

        self.assertTrue(mounted)
        self.assertEqual(requested, ["C:\\Users\\test\\Mountlet\\Docs\\"])

    def test_windows_reparse_point_check_uses_file_attributes(self):
        get_attributes = mock.Mock(return_value=0x00000410)
        kernel32 = SimpleNamespace(GetFileAttributesW=get_attributes)
        with mock.patch(
            "mountlet.platform_services.windows.ctypes.windll",
            SimpleNamespace(kernel32=kernel32),
            create=True,
        ):
            mounted = WindowsPlatformServices._is_mount_reparse_point(r"C:\Users\test\Mountlet\Docs")

        self.assertTrue(mounted)
        get_attributes.assert_called_once_with(r"C:\Users\test\Mountlet\Docs")

    def test_windows_mount_process_stays_attached_without_console(self):
        self.assertEqual(WindowsPlatformServices().mount_process_options(), {"creationflags": 0x08000000})

    def test_windows_short_lived_commands_hide_console_windows(self):
        with mock.patch("mountlet.platform_services.windows.os.name", "nt"):
            self.assertEqual(WindowsPlatformServices().command_process_options(), {"creationflags": 0x08000000})

    def test_windows_finds_winfsp_in_32_bit_program_files(self):
        with tempfile.TemporaryDirectory() as tempdir:
            program_files_x86 = Path(tempdir) / "Program Files (x86)"
            tool = program_files_x86 / "WinFsp" / "bin" / "fsptool-x64.exe"
            tool.parent.mkdir(parents=True)
            tool.touch()
            with mock.patch.dict(
                "os.environ",
                {
                    "ProgramFiles": str(Path(tempdir) / "Program Files"),
                    "ProgramFiles(x86)": str(program_files_x86),
                    "ProgramW6432": str(Path(tempdir) / "Program Files"),
                },
                clear=True,
            ):
                with mock.patch("mountlet.platform_services.windows.shutil.which", return_value=None):
                    available = WindowsPlatformServices().mount_driver_available()

        self.assertTrue(available)

    def test_linux_autostart_uses_freedesktop_entry(self):
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "mountlet.desktop"
            LinuxPlatformServices().set_start_at_login(
                "mountlet",
                True,
                command=("mountlet",),
                destination=destination,
            )
            text = destination.read_text(encoding="utf-8")
            self.assertIn("Exec=mountlet", text)
            LinuxPlatformServices().set_start_at_login(
                "mountlet",
                False,
                command=("mountlet",),
                destination=destination,
            )
            self.assertFalse(destination.exists())

    def test_macos_autostart_uses_launch_agent(self):
        with tempfile.TemporaryDirectory() as tempdir:
            destination = Path(tempdir) / "mountlet.plist"
            MacOSPlatformServices().set_start_at_login(
                "mountlet",
                True,
                command=("mountlet",),
                destination=destination,
            )
            with destination.open("rb") as handle:
                data = plistlib.load(handle)

        self.assertEqual(data["ProgramArguments"], ["mountlet"])
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
