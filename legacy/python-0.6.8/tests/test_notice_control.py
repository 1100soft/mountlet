from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

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
        state_path_patcher = mock.patch.object(notice_control, "_seen_path", return_value=root / "notices.json")
        state_path_patcher.start()
        self.addCleanup(state_path_patcher.stop)

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

    def test_fetch_sends_build_identity_to_packaged_notice_endpoint(self):
        with (
            mock.patch.object(notice_control.build_info, "channel", return_value="preview"),
            mock.patch.object(notice_control.build_info, "identifier", return_value="r314.2-12345678"),
            mock.patch.object(
                notice_control.build_info,
                "notice_api_url",
                return_value="https://wip.mountlet.pages.dev/api/notices",
            ),
            mock.patch.object(notice_control, "_get_json", return_value={"notices": []}) as get_json,
        ):
            notice_control.fetch_notices()

        requested = urlsplit(get_json.call_args.args[0])
        self.assertEqual(f"{requested.scheme}://{requested.netloc}{requested.path}", "https://wip.mountlet.pages.dev/api/notices")
        query = parse_qs(requested.query)
        self.assertEqual(query["buildChannel"], ["preview"])
        self.assertEqual(query["buildId"], ["r314.2-12345678"])

    def test_history_retains_content_and_deletes_only_noncritical_notices(self):
        ordinary = notice_control.Notice(id="ordinary", title="Update", message="Done")
        critical = notice_control.Notice(
            id="critical",
            title="Price change",
            message="Review the new price.",
            level=notice_control.NOTICE_LEVEL_CRITICAL,
        )
        notice_control.remember_notices([ordinary, critical])

        self.assertEqual({item.id for item in notice_control.notification_history()}, {ordinary.id, critical.id})
        self.assertTrue(notice_control.delete_notice(ordinary))
        self.assertFalse(notice_control.delete_notice(critical))
        self.assertEqual([item.id for item in notice_control.notification_history()], [critical.id])

    def test_new_notice_version_is_unread_after_old_version_was_seen(self):
        original = notice_control.Notice(id="release", title="Release", message="One", version="1")
        revised = notice_control.Notice(id="release", title="Release", message="Two", version="2")
        notice_control.remember_notices([original])
        notice_control.mark_seen(original)
        notice_control.remember_notices([revised])

        self.assertEqual(notice_control.unseen_notices([original]), [])
        self.assertEqual(notice_control.unseen_notices([revised]), [revised])

    def test_archived_notice_is_retained_but_never_delivered(self):
        active = notice_control.Notice(id="release", title="Release", message="Current", version="1")
        archived = notice_control.Notice(
            id="release",
            title="Release",
            message="Previous",
            version="2",
            archived=True,
        )
        notice_control.remember_notices([active])
        notice_control.remember_notices([archived])

        history = notice_control.notification_history()
        self.assertEqual(history, [archived])
        self.assertEqual(notice_control.unseen_notices(history), [])
        self.assertTrue(notice_control.is_seen(archived))

    def test_fetch_parses_archived_notice(self):
        with mock.patch.object(
            notice_control,
            "_get_json",
            return_value={
                "notices": [
                    {"id": "old", "title": "Old notice", "message": "Archived", "archived": True}
                ]
            },
        ):
            notices = notice_control.fetch_notices()

        self.assertTrue(notices[0].archived)
        self.assertEqual(notice_control.unseen_notices(notices), [])

    def test_expired_archived_notice_remains_in_public_history(self):
        with mock.patch.object(
            notice_control,
            "_get_json",
            return_value={
                "notices": [
                    {
                        "id": "old",
                        "title": "Old notice",
                        "message": "Archived",
                        "archived": True,
                        "endsAt": "2000-01-01T00:00:00Z",
                    }
                ]
            },
        ):
            notices = notice_control.fetch_notices()

        self.assertEqual([notice.id for notice in notices], ["old"])

    def test_expired_notice_remains_in_history(self):
        notice = notice_control.Notice(id="expired", title="Old notice", message="Still useful")
        notice_control.remember_notices([notice])
        path = notice_control._seen_path()
        state = json.loads(path.read_text(encoding="utf-8"))
        state["history"][notice.key]["endsAt"] = "2000-01-01T00:00:00Z"
        path.write_text(json.dumps(state), encoding="utf-8")

        self.assertEqual([item.id for item in notice_control.notification_history()], ["expired"])


if __name__ == "__main__":
    unittest.main()
