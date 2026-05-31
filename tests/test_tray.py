from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
