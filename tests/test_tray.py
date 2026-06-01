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
        def qdbus_lines(args: list[str]) -> list[str]:
            if args == []:
                return ["org.kde.dolphin-1234"]
            if args == ["org.kde.dolphin-1234"]:
                return ["/dolphin/Dolphin_1"]
            return []

        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
            with mock.patch.object(tray, "_qdbus_lines", side_effect=qdbus_lines):
                with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                    self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openNewActivatedTab",
                "file:///tmp/docs",
            ],
        )

    def test_open_folder_in_dolphin_tab_falls_back_when_no_window_is_available(self):
        with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
            with mock.patch.object(tray, "_qdbus_lines", return_value=[]):
                with mock.patch.object(tray.subprocess, "run") as run:
                    self.assertFalse(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        run.assert_not_called()

    def test_open_folder_in_dolphin_tab_falls_back_to_short_method_name(self):
        def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
            if "org.kde.dolphin.MainWindow.openNewActivatedTab" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        windows = [("org.kde.dolphin-1234", "/dolphin/Dolphin_1")]
        with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
            with mock.patch.object(tray, "_dolphin_dbus_windows", return_value=windows):
                with mock.patch.object(tray.subprocess, "run", side_effect=run_command) as run:
                    self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 2)

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
