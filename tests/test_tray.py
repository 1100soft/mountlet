from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloud_mount_manager import core, tray


class _FakeMenu:
    def __init__(self) -> None:
        self.popup_calls: list[object] = []

    def popup(self, position: object) -> None:
        self.popup_calls.append(position)


class TrayTests(unittest.TestCase):
    def setUp(self) -> None:
        tray._dolphin_tab_target_cache = None

    def test_tray_stops_before_qt_import_when_environment_is_not_ready(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=False):
            with mock.patch.object(tray, "_load_qt_bindings") as load_qt:
                self.assertEqual(tray.main([]), 1)

        load_qt.assert_not_called()

    def test_tray_reports_missing_pyside_dependency(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()) as output:
                        self.assertEqual(tray.main([]), 1)

        self.assertIn("missing PySide6", output.getvalue())

    def test_tray_can_skip_readiness_check(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu") as readiness:
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(tray.main(["--skip-readiness-check"]), 1)

        readiness.assert_not_called()

    def test_tray_reports_missing_desktop_session_before_qt_import(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(False, "no display")):
                with mock.patch.object(tray, "_load_qt_bindings") as load_qt:
                    with contextlib.redirect_stderr(io.StringIO()) as output:
                        self.assertEqual(tray.main([]), 1)

        load_qt.assert_not_called()
        self.assertIn("no display", output.getvalue())

    def test_remote_title_includes_mount_state(self):
        remote = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/docs",
        )

        self.assertEqual(tray._remote_title(remote, mounted=True), "Docs (drive) - Mounted")
        self.assertEqual(tray._remote_title(remote, mounted=False), "Docs (drive) - Unmounted")

    def test_status_tooltip_summarizes_mounts(self):
        remotes = [
            core.RemoteInfo(
                name="Docs",
                alias="Docs",
                provider="drive",
                backend_type="drive",
                mount_path="/tmp/docs",
            ),
            core.RemoteInfo(
                name="Photos",
                alias="Photos",
                provider="dropbox",
                backend_type="dropbox",
                mount_path="/tmp/photos",
            ),
        ]

        self.assertEqual(tray._status_tooltip([], []), "Cloud Mount Manager - no rclone remotes")
        self.assertEqual(
            tray._status_tooltip(remotes, []),
            "Cloud Mount Manager - 0 mounted, 2 unmounted",
        )
        self.assertEqual(
            tray._status_tooltip(remotes, ["Docs"]),
            "Cloud Mount Manager - mounted: Docs",
        )

    def test_left_click_activation_opens_remote_menu(self):
        fake_menu = _FakeMenu()
        fake_qt = mock.Mock()
        fake_qt.QSystemTrayIcon.ActivationReason.Trigger = "trigger"
        fake_qt.QSystemTrayIcon.ActivationReason.DoubleClick = "double"
        fake_qt.QCursor.pos.return_value = "cursor-position"
        tray_app = object.__new__(tray.CloudMountTray)
        tray_app.qt = fake_qt
        tray_app.remote_menu = fake_menu

        with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
            tray_app._handle_activation(fake_qt.QSystemTrayIcon.ActivationReason.Trigger)

        rebuild.assert_called_once_with()
        self.assertEqual(fake_menu.popup_calls, ["cursor-position"])

        with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
            tray_app._handle_activation(fake_qt.QSystemTrayIcon.ActivationReason.DoubleClick)

        rebuild.assert_not_called()
        self.assertEqual(fake_menu.popup_calls, ["cursor-position"])

    def test_show_folder_uses_file_manager_dbus_service_on_linux(self):
        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                self.assertTrue(tray._show_folder_with_file_manager("/tmp/docs"))

        command = run.call_args.args[0]
        self.assertIn("--dest=org.freedesktop.FileManager1", command)
        self.assertIn("org.freedesktop.FileManager1.ShowFolders", command)
        self.assertIn("array:string:file:///tmp/docs", command)
        self.assertEqual(command[-1], "string:")

    def test_open_folder_uses_qt_default_opener_by_default(self):
        qt = mock.Mock()
        qt.QDesktopServices.openUrl.return_value = True
        qt.QUrl.fromLocalFile.return_value = "qt-url"

        with mock.patch.object(tray, "_show_folder_with_file_manager") as show_folder:
            with mock.patch.object(tray, "_open_folder_with_known_file_manager", return_value=False):
                self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        show_folder.assert_not_called()
        qt.QUrl.fromLocalFile.assert_called_once_with("/tmp/docs")
        qt.QDesktopServices.openUrl.assert_called_once_with("qt-url")

    def test_open_folder_uses_dolphin_new_window_when_dolphin_is_default(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=False):
                with mock.patch.object(tray.shutil, "which", return_value="/usr/bin/dolphin"):
                    with mock.patch.object(tray.subprocess, "Popen") as popen:
                        self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/dolphin", "--new-window", "/tmp/docs"])
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_prefers_dolphin_dbus_tab_when_available(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=True) as open_tab:
                self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        open_tab.assert_called_once_with("/tmp/docs")
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_in_dolphin_tab_calls_running_dolphin_window(self):
        completed = SimpleNamespace(returncode=0)
        windows = [("org.kde.dolphin-1234", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openDirectories",
                "file:///tmp/docs",
                "false",
            ],
        )

    def test_open_folder_in_dolphin_tab_falls_back_when_no_window_is_available(self):
        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=[]):
                    with mock.patch.object(tray, "_dolphin_slow_tab_targets", return_value=[]):
                        with mock.patch.object(tray.subprocess, "run") as run:
                            self.assertFalse(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        run.assert_not_called()

    def test_open_folder_in_dolphin_tab_tries_next_window_after_failure(self):
        def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
            if "org.kde.dolphin-1234" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        windows = [
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"),
        ]
        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", side_effect=run_command) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 3)

    def test_open_folder_in_dolphin_tab_reuses_cached_window(self):
        completed = SimpleNamespace(returncode=0)
        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets") as fast_targets:
                    with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        fast_targets.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_open_folder_in_dolphin_tab_clears_stale_cache(self):
        def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
            if "org.kde.dolphin-1234" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", side_effect=run_command) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 3)
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_dolphin_dbus_windows_are_sorted_by_newest_service_suffix(self):
        def qdbus_lines(args: list[str]) -> list[str]:
            if args == []:
                return [
                    "org.kde.dolphin-1234",
                    "org.kde.dolphin-5678",
                    "org.example.Other",
                ]
            if args == ["org.kde.dolphin-1234"]:
                return ["/dolphin/Dolphin_1"]
            if args == ["org.kde.dolphin-5678"]:
                return ["/dolphin/Dolphin_2"]
            return []

        with mock.patch.object(tray, "_qdbus_lines", side_effect=qdbus_lines):
            self.assertEqual(
                tray._dolphin_dbus_windows(),
                [
                    ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
                    ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
                ],
            )

    def test_parse_xprop_cardinal_reads_desktop_number(self):
        self.assertEqual(tray._parse_xprop_cardinal("_NET_CURRENT_DESKTOP(CARDINAL) = 3"), 3)
        self.assertIsNone(tray._parse_xprop_cardinal("_NET_CURRENT_DESKTOP:  not found."))

    def test_current_desktop_targets_filter_dolphin_windows_by_x11_desktop(self):
        windows = [
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"),
        ]

        def is_on_desktop(
            dbus: object,
            qdbus: object,
            service: str,
            object_path: str,
            desktop: int,
        ) -> bool:
            return service == "org.kde.dolphin-5678" and desktop == 3

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                with mock.patch.object(tray, "_dolphin_window_is_on_desktop", side_effect=is_on_desktop):
                    self.assertEqual(
                        tray._dolphin_current_desktop_targets(None, "/usr/bin/qdbus6", set()),
                        [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")],
                    )

    def test_open_folder_in_dolphin_tab_uses_current_desktop_window(self):
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=windows):
                    with mock.patch.object(tray, "_open_folder_in_dolphin_window", return_value=True) as open_window:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        open_window.assert_called_once()
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_open_folder_in_dolphin_tab_ignores_cached_window_from_other_desktop(self):
        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        def is_on_desktop(
            dbus: object,
            qdbus: object,
            service: str,
            object_path: str,
            desktop: int,
        ) -> bool:
            return service == "org.kde.dolphin-5678"

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_window_is_on_desktop", side_effect=is_on_desktop):
                    with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=windows):
                        with mock.patch.object(
                            tray,
                            "_open_folder_in_dolphin_window",
                            return_value=True,
                        ) as open_window:
                            self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        open_window.assert_called_once()
        self.assertEqual(open_window.call_args.args[2], "org.kde.dolphin-5678")
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_open_folder_in_dolphin_tab_returns_false_when_current_desktop_has_no_dolphin_window(self):
        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=[]):
                    with mock.patch.object(tray, "_dolphin_fast_tab_targets") as fast_targets:
                        self.assertFalse(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        fast_targets.assert_not_called()

    def test_dolphin_dbus_services_can_use_qtdbus_without_qdbus_subprocess(self):
        reply = mock.Mock()
        reply.isValid.return_value = True
        reply.value.return_value = [
            "org.example.Other",
            "org.kde.dolphin-1234",
            "org.kde.dolphin-5678",
        ]
        bus = mock.Mock()
        bus.interface.return_value.registeredServiceNames.return_value = reply
        dbus = SimpleNamespace(bus=bus)

        with mock.patch.object(tray, "_qdbus_lines") as qdbus_lines:
            self.assertEqual(
                tray._dolphin_dbus_services(dbus),
                ["org.kde.dolphin-5678", "org.kde.dolphin-1234"],
            )

        qdbus_lines.assert_not_called()

    def test_open_folder_in_dolphin_window_can_use_qtdbus_without_subprocess(self):
        reply = mock.Mock()
        reply.errorName.return_value = ""
        interface = mock.Mock()
        interface.isValid.return_value = True
        interface.call.return_value = reply
        dbus = SimpleNamespace(QDBusInterface=mock.Mock(return_value=interface), bus=mock.Mock())

        with mock.patch.object(tray.subprocess, "run") as run:
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    dbus,
                    None,
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        self.assertEqual(
            interface.call.call_args_list,
            [
                mock.call("openDirectories", ["file:///tmp/docs"], False),
                mock.call("activateWindow", ""),
            ],
        )
        run.assert_not_called()

    def test_open_folder_in_dolphin_window_focuses_after_qdbus_fallback_open(self):
        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1],
            mock.call(
                [
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "org.kde.dolphin.MainWindow.activateWindow",
                    "",
                ],
                stdout=tray.subprocess.DEVNULL,
                stderr=tray.subprocess.DEVNULL,
                timeout=1,
            ),
        )

    def test_open_folder_in_dolphin_window_ignores_focus_failure(self):
        open_completed = SimpleNamespace(returncode=0)

        with mock.patch.object(
            tray.subprocess,
            "run",
            side_effect=[open_completed, tray.subprocess.TimeoutExpired("qdbus6", 1)],
        ):
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

    def test_open_folder_in_dolphin_window_does_not_focus_after_failed_open(self):
        completed = SimpleNamespace(returncode=1)

        with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
            self.assertFalse(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        run.assert_called_once_with(
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openDirectories",
                "file:///tmp/docs",
                "false",
            ],
            stdout=tray.subprocess.DEVNULL,
            stderr=tray.subprocess.DEVNULL,
            timeout=1,
        )

    def test_ordered_dolphin_dbus_windows_puts_active_window_first(self):
        windows = [
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
        ]

        def is_active(qdbus: str, service: str, object_path: str) -> bool:
            return service == "org.kde.dolphin-1234"

        with mock.patch.object(tray, "_dolphin_dbus_windows", return_value=windows):
            with mock.patch.object(tray, "_dolphin_window_is_active", side_effect=is_active):
                self.assertEqual(
                    tray._ordered_dolphin_dbus_windows("/usr/bin/qdbus6"),
                    [
                        ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
                        ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
                    ],
                )

    def test_open_folder_uses_dolphin_new_window_when_dbus_tab_fails(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray.shutil, "which", return_value="/usr/bin/dolphin"):
                with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=False):
                    with mock.patch.object(tray.subprocess, "Popen") as popen:
                        self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/dolphin", "--new-window", "/tmp/docs"])
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_can_use_file_manager_service_strategy(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_show_folder_with_file_manager", return_value=True):
            self.assertTrue(tray._open_folder_default(qt, "/tmp/docs", strategy="file-manager-service"))

        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_action_reports_failure_when_default_opener_fails(self):
        remote = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/missing-docs",
        )
        tray_app = object.__new__(tray.CloudMountTray)
        tray_app.qt = mock.Mock()

        with tempfile.TemporaryDirectory() as tempdir:
            remote.mount_path = tempdir
            with mock.patch.object(tray, "_open_folder_default", return_value=False):
                with mock.patch.object(tray_app, "_notify") as notify:
                    tray_app._open_folder(remote)

        notify.assert_called_once_with("Open folder", "Could not open the mount folder.", success=False)


if __name__ == "__main__":
    unittest.main()
