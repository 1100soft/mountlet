from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import build_info, notice_control  # noqa: E402


class BuildInfoTests(unittest.TestCase):
    def test_production_build_has_no_visible_suffix(self):
        with (
            mock.patch.dict(build_info.os.environ, {}, clear=True),
            mock.patch.object(build_info, "data", return_value={"channel": "production", "buildId": "r1.1"}),
        ):
            self.assertEqual(build_info.channel(), "production")
            self.assertEqual(build_info.visible_label(), "")

    def test_preview_build_has_separate_identifier(self):
        with (
            mock.patch.dict(build_info.os.environ, {}, clear=True),
            mock.patch.object(build_info, "data", return_value={"channel": "preview", "buildId": "r314.2-deadbeef"}),
        ):
            self.assertEqual(build_info.visible_label(), "Preview r314.2-deadbeef")

    def test_unpackaged_source_is_visible_as_local(self):
        with (
            mock.patch.dict(build_info.os.environ, {}, clear=True),
            mock.patch.object(build_info, "data", return_value={}),
        ):
            self.assertEqual(build_info.channel(), "local")
            self.assertEqual(build_info.visible_label(), "Local source")

    def test_notice_history_path_is_separate_for_each_channel(self):
        state_root = Path("test-state")
        with (
            mock.patch.object(notice_control, "app_state_dir", return_value=state_root),
            mock.patch.object(build_info, "channel", return_value="preview"),
        ):
            preview_path = notice_control._seen_path()
        with (
            mock.patch.object(notice_control, "app_state_dir", return_value=state_root),
            mock.patch.object(build_info, "channel", return_value="production"),
        ):
            production_path = notice_control._seen_path()

        self.assertEqual(preview_path.name, "notices-preview.json")
        self.assertEqual(production_path.name, "notices-production.json")
        self.assertNotEqual(preview_path, production_path)


if __name__ == "__main__":
    unittest.main()
