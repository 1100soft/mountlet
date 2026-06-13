from __future__ import annotations

import subprocess
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import rclone_wizard


class FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "", timeout: bool = False) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.communicate_timeout = None
        self.terminated = False
        self.killed = False
        self.pid = 12345

    def communicate(self, *, timeout: int | None = None) -> tuple[str, str]:
        self.communicate_timeout = timeout
        if self.timeout:
            raise subprocess.TimeoutExpired("rclone", timeout)
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return None if self.terminated is False and self.killed is False else self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def send_signal(self, sig: int) -> None:
        if sig == signal.SIGTERM:
            self.terminated = True
        if sig == signal.SIGKILL:
            self.killed = True

    def wait(self, timeout: int | None = None) -> int:
        self.returncode = -15 if self.terminated else self.returncode
        return self.returncode


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
            process = FakeProcess(
                stdout='{"State":"next","Option":{"Name":"config_is_local"},"Error":"","Result":""}',
                stderr="",
            )
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "Popen", return_value=process) as popen:
                        step = rclone_wizard.start_drive_remote(
                            "Docs",
                            client_id="client.apps.googleusercontent.com",
                            client_secret="secret",
                            local_auth=True,
                            shared_drive=False,
                        )

        command = popen.call_args.args[0]
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
        self.assertEqual(process.communicate_timeout, rclone_wizard.RCLONE_BROWSER_AUTH_TIMEOUT_SECONDS)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_continue_drive_remote_passes_state_and_result(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            process = FakeProcess(stdout='{"State":"","Option":{},"Error":"","Result":""}', stderr="")
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "Popen", return_value=process) as popen:
                        step = rclone_wizard.continue_drive_remote(
                            "Docs",
                            "*state",
                            "true",
                            client_id="client",
                            client_secret="secret",
                        )

            self.assertEqual(popen.call_args.args[0][-5:], ["--continue", "--state", "*state", "--result", "true"])
            self.assertTrue(step.complete)

            text = config_path.read_text(encoding="utf-8")
            self.assertIn("type = drive", text)
            self.assertIn("client_id = client", text)
            self.assertIn("client_secret = secret", text)

    def test_start_remote_can_request_token_authorization(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            process = FakeProcess(
                stdout='{"State":"token","Option":{"Name":"config_token"},"Error":"","Result":""}',
                stderr="",
            )
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "Popen", return_value=process) as popen:
                        step = rclone_wizard.start_remote(
                            "Docs",
                            "drive",
                            ["config_is_local", "false"],
                        )

        command = popen.call_args.args[0]
        self.assertIn("config_is_local", command)
        self.assertEqual(command[command.index("config_is_local") + 1], "false")
        self.assertEqual(step.option["Name"], "config_token")

    def test_continue_remote_preserves_generic_backend_type(self):
        with tempfile.TemporaryDirectory() as tempdir:
            config_path = Path(tempdir) / "rclone.conf"
            process = FakeProcess(stdout='{"State":"","Option":{},"Error":"","Result":""}', stderr="")
            with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
                with mock.patch.object(rclone_wizard, "default_config_path", return_value=config_path):
                    with mock.patch.object(subprocess, "Popen", return_value=process):
                        step = rclone_wizard.continue_remote(
                            "Photos",
                            "dropbox",
                            "*state",
                            "token",
                            ["config_is_local", "false"],
                        )

            self.assertTrue(step.complete)
            self.assertIn("type = dropbox", config_path.read_text(encoding="utf-8"))

    def test_run_config_create_reports_rclone_failure(self):
        process = FakeProcess(returncode=1, stdout="", stderr="failed")
        with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
            with mock.patch.object(rclone_wizard, "default_config_path", return_value=Path("/tmp/rclone.conf")):
                with mock.patch.object(subprocess, "Popen", return_value=process):
                    with self.assertRaisesRegex(rclone_wizard.RcloneWizardError, "failed"):
                        rclone_wizard.start_drive_remote("Docs")

    def test_run_config_create_reports_timeout(self):
        process = FakeProcess(timeout=True)
        with mock.patch.object(rclone_wizard, "find_rclone", return_value="/usr/bin/rclone"):
            with mock.patch.object(rclone_wizard, "default_config_path", return_value=Path("/tmp/rclone.conf")):
                with mock.patch.object(subprocess, "Popen", return_value=process):
                    with self.assertRaisesRegex(rclone_wizard.RcloneWizardError, "timed out"):
                        rclone_wizard.start_drive_remote("Docs")
        self.assertTrue(process.terminated)

    def test_cancel_remote_config_terminates_active_process(self):
        process = FakeProcess()
        rclone_wizard._ACTIVE_CONFIG_PROCESSES["Docs"] = process

        self.assertTrue(rclone_wizard.cancel_remote_config("Docs"))

        self.assertTrue(process.terminated)
        self.assertNotIn("Docs", rclone_wizard._ACTIVE_CONFIG_PROCESSES)

    def test_cancel_all_remote_configs_terminates_active_processes(self):
        docs = FakeProcess()
        photos = FakeProcess()
        rclone_wizard._ACTIVE_CONFIG_PROCESSES["Docs"] = docs
        rclone_wizard._ACTIVE_CONFIG_PROCESSES["Photos"] = photos

        rclone_wizard.cancel_all_remote_configs()

        self.assertTrue(docs.terminated)
        self.assertTrue(photos.terminated)
        self.assertEqual(rclone_wizard._ACTIVE_CONFIG_PROCESSES, {})


if __name__ == "__main__":
    unittest.main()
