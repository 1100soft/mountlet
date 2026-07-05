from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mountlet import rclone_log


class RcloneLogTests(unittest.TestCase):
    def test_append_raw_keeps_bounded_tail(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "rclone-output.log"
            with mock.patch.object(rclone_log, "log_path", return_value=path):
                with mock.patch.object(rclone_log, "apply_permissions"):
                    rclone_log.append_raw("one\ntwo\n", max_lines=3)
                    rclone_log.append_raw("three\nfour\n", max_lines=3)

                self.assertEqual(rclone_log.tail_text(max_lines=10), "two\nthree\nfour")


if __name__ == "__main__":
    unittest.main()
