from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cloud_mount_manager import tui


class TuiTests(unittest.TestCase):
    def test_quit_leaves_mounts_unchanged(self):
        with mock.patch.object(tui, "display") as display:
            with mock.patch.object(tui, "ensure_app_directories"):
                with mock.patch.object(tui.core, "load_remotes", return_value=[]) as load_remotes:
                    with mock.patch.object(tui, "unmount_all") as unmount_all:
                        with mock.patch("builtins.input", return_value="q"):
                            tui.main()

        load_remotes.assert_called()
        unmount_all.assert_not_called()
        self.assertIn("leaving mounted remotes", display.call_args.args[1])

    def test_keyboard_interrupt_leaves_mounts_unchanged(self):
        with mock.patch.object(tui, "display") as display:
            with mock.patch.object(tui, "ensure_app_directories"):
                with mock.patch.object(tui.core, "load_remotes", return_value=[]):
                    with mock.patch.object(tui, "unmount_all") as unmount_all:
                        with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                            tui.main()

        unmount_all.assert_not_called()
        self.assertIn("leaving mounted remotes", display.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
