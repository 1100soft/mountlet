from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import rclone_wizard


class RcloneWizardTests(unittest.TestCase):
    def test_extract_json_object_ignores_rclone_notices(self):
        output = """
<5>NOTICE: Config file not found - using defaults
{
  "State": "*oauth-islocal",
  "Option": {"Name": "config_is_local"},
  "Error": "",
  "Result": ""
}
""".strip()

        parsed = rclone_wizard._extract_json_object(output)

        self.assertEqual(parsed["State"], "*oauth-islocal")
        self.assertEqual(parsed["Option"]["Name"], "config_is_local")

    def test_start_drive_remote_runs_non_interactive_create_with_oauth_credentials(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            completed = SimpleNamespace(
                returncode=0,
                stdout='{"State":"next","Option":{"Name":"config_is_local"},"Error":"","Result":""}',
                stderr="",
            )
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "run", return_value=completed) as run:
                        step = rclone_wizard.start_drive_remote(
                            "Docs",
                            client_id="client.apps.googleusercontent.com",
                            client_secret="secret",
                            local_auth=True,
                            shared_drive=False,
                        )

        command = run.call_args.args[0]
        self.assertEqual(command[:7], ["/usr/bin/rclone", "--config", str(config_path), "config", "create", "Docs", "drive"])
        self.assertIn("--non-interactive", command)
        self.assertEqual(
            command[-10:],
            [
                "client_id",
                "client.apps.googleusercontent.com",
                "client_secret",
                "secret",
                "scope",
                "drive",
                "config_is_local",
                "true",
                "config_team_drive",
                "false",
            ],
        )
        self.assertEqual(step.state, "next")
        self.assertEqual(step.option["Name"], "config_is_local")
        self.assertEqual(run.call_args.kwargs["timeout"], rclone_wizard.RCLONE_BROWSER_AUTH_TIMEOUT_SECONDS)

    def test_continue_drive_remote_passes_state_and_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            completed = SimpleNamespace(returncode=0, stdout='{"State":"","Option":{},"Error":"","Result":""}', stderr="")
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "run", return_value=completed) as run:
                        step = rclone_wizard.continue_drive_remote(
                            "Docs",
                            "*state",
                            "true",
                            client_id="client",
                            client_secret="secret",
                        )

            self.assertEqual(run.call_args.args[0][-5:], ["--continue", "--state", "*state", "--result", "true"])
            self.assertTrue(step.complete)

            text = config_path.read_text(encoding="utf-8")
            self.assertIn("type = drive", text)
            self.assertIn("client_id = client", text)
            self.assertIn("client_secret = secret", text)

    def test_run_config_create_reports_rclone_failure(self):
        completed = SimpleNamespace(returncode=1, stdout="", stderr="failed")
        with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
            with mock.patch.object(rclone_wizard, "default_config_path", return_value=Path("/tmp/rclone.conf")):
                with mock.patch.object(subprocess, "run", return_value=completed):
                    with self.assertRaisesRegex(rclone_wizard.RcloneWizardError, "failed"):
                        rclone_wizard.start_drive_remote("Docs")

    def test_run_config_create_reports_timeout(self):
        with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
            with mock.patch.object(rclone_wizard, "default_config_path", return_value=Path("/tmp/rclone.conf")):
                with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("rclone", 300)):
                    with self.assertRaisesRegex(rclone_wizard.RcloneWizardError, "timed out"):
                        rclone_wizard.start_drive_remote("Docs")


if __name__ == "__main__":
    unittest.main()
