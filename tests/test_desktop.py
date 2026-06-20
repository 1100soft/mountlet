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

    def test_desktop_entrypoint_starts_tray_without_terminal_readiness_gate(self):
        with mock.patch("mountlet.tray.main", return_value=0) as tray_main:
            self.assertEqual(desktop.main([]), 0)

        tray_main.assert_called_once_with(["--skip-readiness-check"])


if __name__ == "__main__":
    unittest.main()
