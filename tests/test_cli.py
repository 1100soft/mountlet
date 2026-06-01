from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import cli


class CliTests(unittest.TestCase):
    def test_version_prints_package_version(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["--version"]), 0)

        self.assertIn("mountlet 0.2.0", output.getvalue())

    def test_no_args_opens_menu(self):
        with mock.patch.object(cli.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(cli.tui, "main", return_value=None) as menu:
                self.assertEqual(cli.main([]), 0)

        menu.assert_called_once_with()

    def test_no_args_stops_when_environment_is_not_ready(self):
        with mock.patch.object(cli.setup_wizard, "ensure_ready_for_menu", return_value=False):
            with mock.patch.object(cli.tui, "main", return_value=None) as menu:
                self.assertEqual(cli.main([]), 1)

        menu.assert_not_called()

    def test_menu_subcommand_opens_menu(self):
        with mock.patch.object(cli.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(cli.tui, "main", return_value=None) as menu:
                self.assertEqual(cli.main(["menu"]), 0)

        menu.assert_called_once_with()

    def test_menu_subcommand_stops_when_environment_is_not_ready(self):
        with mock.patch.object(cli.setup_wizard, "ensure_ready_for_menu", return_value=False):
            with mock.patch.object(cli.tui, "main", return_value=None) as menu:
                self.assertEqual(cli.main(["menu"]), 1)

        menu.assert_not_called()

    def test_verify_subcommand_dispatches_to_verify_tool(self):
        with mock.patch.object(cli.verify_config, "main", return_value=0) as verify:
            self.assertEqual(cli.main(["verify", "--remote", "Docs"]), 0)

        verify.assert_called_once_with(["--remote", "Docs"])

    def test_setup_subcommand_dispatches_to_setup_tool(self):
        with mock.patch.object(cli.setup_wizard, "main", return_value=0) as setup:
            self.assertEqual(cli.main(["setup", "--skip-verify"]), 0)

        setup.assert_called_once_with(["--skip-verify"])

    def test_tray_subcommand_dispatches_to_tray_tool(self):
        from mountlet import tray

        with mock.patch.object(tray, "main", return_value=0) as tray_main:
            self.assertEqual(cli.main(["tray", "--skip-readiness-check"]), 0)

        tray_main.assert_called_once_with(["--skip-readiness-check"])

    def test_paths_alias_dispatches_to_path_tool(self):
        with mock.patch.object(cli.path_config, "main", return_value=0) as path:
            self.assertEqual(cli.main(["paths", "--ensure"]), 0)

        path.assert_called_once_with(["--ensure"])

    def test_unknown_command_returns_error(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["missing"]), 2)


if __name__ == "__main__":
    unittest.main()
