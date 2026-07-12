from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import report_control


class ReportControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.state_dir = Path(self.tempdir.name)
        patcher = mock.patch("mountlet.report_control.app_state_dir", return_value=self.state_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_redact_text_removes_license_keys_and_secrets(self):
        text = (
            "license MNT-ABC12345-DEF67890 token=plain client_secret:shh "
            "https://example.test/callback?code=secret-code&state=ok"
        )

        redacted = report_control.redact_text(text)

        self.assertNotIn("MNT-ABC12345-DEF67890", redacted)
        self.assertNotIn("plain", redacted)
        self.assertNotIn("shh", redacted)
        self.assertNotIn("secret-code", redacted)
        self.assertIn("MNT-[redacted]", redacted)

    def test_unreported_crash_log_uses_marker(self):
        report_control.runtime_log_path().write_text(
            "Mountlet runtime\nFatal Python error: Segmentation fault\nframe detail\n",
            encoding="utf-8",
        )

        crash = report_control.unreported_crash_log()

        self.assertIn("Fatal Python error", crash)
        report_control.mark_crash_reported(crash)
        self.assertEqual(report_control.unreported_crash_log(), "")

    def test_report_payload_includes_redacted_logs(self):
        report_control.runtime_log_path().write_text("token=runtime-secret\n", encoding="utf-8")
        report_control.rclone_log_path().write_text("client_secret=rclone-secret\n", encoding="utf-8")

        payload = report_control.report_payload(
            kind="bug",
            message="license MNT-ABC12345-DEF67890",
            contact="user@example.test",
            include_logs=True,
        )
        payload_json = json.dumps(payload, sort_keys=True)

        self.assertEqual(payload["kind"], "bug")
        self.assertEqual(payload["contact"], "user@example.test")
        self.assertNotIn("MNT-ABC12345-DEF67890", payload_json)
        self.assertNotIn("runtime-secret", payload_json)
        self.assertNotIn("rclone-secret", payload_json)


if __name__ == "__main__":
    unittest.main()
