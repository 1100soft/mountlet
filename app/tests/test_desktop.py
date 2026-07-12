from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import desktop


class DesktopTests(unittest.TestCase):
    def test_packaging_smoke_test_does_not_load_tray(self):
        with mock.patch.dict(sys.modules, {"mountlet.tray": None}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(desktop.main(["--packaging-smoke-test"]), 0)

        self.assertRegex(output.getvalue(), r"^Mountlet \d+\.\d+\.\d+\n$")

    def test_packaging_startup_import_test_loads_tray(self):
        with mock.patch.dict(sys.modules, {"mountlet.tray": mock.Mock()}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(desktop.main(["--packaging-startup-import-test"]), 0)

        self.assertRegex(output.getvalue(), r"^Mountlet \d+\.\d+\.\d+ startup imports ok\n$")

    def test_packaging_rclone_smoke_test_does_not_load_tray(self):
        with mock.patch.dict(sys.modules, {"mountlet.tray": None}):
            with mock.patch("mountlet.config_tools.shared.run_rclone_version", return_value="rclone v1.70.0"):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(desktop.main(["--packaging-rclone-smoke-test"]), 0)

        self.assertEqual(output.getvalue(), "rclone v1.70.0\n")

    def test_packaging_rclone_smoke_test_fails_when_rclone_is_missing(self):
        with mock.patch("mountlet.config_tools.shared.run_rclone_version", return_value="rclone binary not found"):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(desktop.main(["--packaging-rclone-smoke-test"]), 1)

        self.assertEqual(output.getvalue(), "rclone binary not found\n")

    def test_desktop_entrypoint_starts_tray_without_terminal_readiness_gate(self):
        with mock.patch("mountlet.tray.main", return_value=0) as tray_main:
            with mock.patch.object(desktop, "_write_startup_log") as write_log:
                self.assertEqual(desktop.main([]), 0)

        tray_main.assert_called_once_with([])
        write_log.assert_called_once()

    def test_desktop_entrypoint_writes_startup_log_on_exception(self):
        with mock.patch("mountlet.tray.main", side_effect=RuntimeError("boom")):
            with mock.patch.object(desktop, "_write_startup_log") as write_log:
                self.assertEqual(desktop.main([]), 1)

        self.assertIn("failed during desktop startup", write_log.call_args.args[0])
        self.assertIn("RuntimeError: boom", write_log.call_args.args[0])

    def test_frozen_linux_defaults_to_compose_input_method(self):
        with mock.patch("mountlet.desktop.platform.system", return_value="Linux"):
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.dict("os.environ", {}, clear=True):
                    with mock.patch.object(desktop, "_append_runtime_log"):
                        desktop._prepare_frozen_linux_qt_environment()

                    self.assertEqual(desktop.os.environ["QT_IM_MODULE"], "compose")
                    self.assertEqual(desktop.os.environ["QT_STYLE_OVERRIDE"], "Fusion")
                    self.assertNotIn("QT_QPA_PLATFORMTHEME", desktop.os.environ)

    def test_frozen_linux_input_method_override_is_respected(self):
        with mock.patch("mountlet.desktop.platform.system", return_value="Linux"):
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.dict("os.environ", {"MOUNTLET_QT_IM_MODULE": "ibus"}, clear=True):
                    with mock.patch.object(desktop, "_append_runtime_log"):
                        desktop._prepare_frozen_linux_qt_environment()

                    self.assertEqual(desktop.os.environ["QT_IM_MODULE"], "ibus")

    def test_frozen_linux_platform_theme_override_is_respected(self):
        with mock.patch("mountlet.desktop.platform.system", return_value="Linux"):
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.dict(
                    "os.environ",
                    {"MOUNTLET_QT_PLATFORMTHEME": "kde", "QT_PLUGIN_PATH": "/tmp/host-qt"},
                    clear=True,
                ):
                    with mock.patch.object(desktop, "_append_runtime_log"):
                        desktop._prepare_frozen_linux_qt_environment()

                    self.assertEqual(desktop.os.environ["QT_QPA_PLATFORMTHEME"], "kde")
                    self.assertNotIn("QT_PLUGIN_PATH", desktop.os.environ)


if __name__ == "__main__":
    unittest.main()
