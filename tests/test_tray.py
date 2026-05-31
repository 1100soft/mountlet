from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloud_mount_manager import core, tray


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


if __name__ == "__main__":
    unittest.main()
