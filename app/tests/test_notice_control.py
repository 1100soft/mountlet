from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import notice_control


class NoticeControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        env = {
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }
        patcher = mock.patch.dict("os.environ", env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_notice_requires_core_fields_and_tracks_version(self):
        with mock.patch.object(
            notice_control,
            "_get_json",
            return_value={
                "notices": [
                    {"id": "n1", "title": "Title", "message": "Body", "level": "important", "version": "1"},
                    {"id": "broken", "title": "Missing message"},
                ]
            },
        ):
            notices = notice_control.fetch_notices()

        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].level, notice_control.NOTICE_LEVEL_IMPORTANT)
        self.assertEqual(notice_control.unseen_notices(notices), notices)

        notice_control.mark_seen(notices[0])
        self.assertEqual(notice_control.unseen_notices(notices), [])

        changed = notice_control.Notice(id="n1", title="Title", message="Body", version="2")
        self.assertEqual(notice_control.unseen_notices([changed]), [changed])


if __name__ == "__main__":
    unittest.main()
