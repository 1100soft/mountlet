from __future__ import annotations

import contextlib
import io
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import core, settings, tray


class _FakeWindow:
    def __init__(self) -> None:
        self.show_calls = 0
        self.toggle_calls = 0

    def show(self) -> None:
        self.show_calls += 1

    def toggle_from_tray(self) -> None:
        self.toggle_calls += 1


class TrayTests(unittest.TestCase):
    def setUp(self) -> None:
        tray._dolphin_tab_target_cache = None
        tray._wizard_pending_remote_names.clear()

    def test_tray_stops_before_qt_import_when_environment_is_not_ready(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=False):
            with mock.patch.object(tray, "_load_qt_bindings") as load_qt:
                self.assertEqual(tray.main([]), 1)

        load_qt.assert_not_called()

    def test_tray_reports_missing_pyside_dependency(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()) as output:
                        self.assertEqual(tray.main([]), 1)

        self.assertIn("missing PySide6", output.getvalue())

    def test_tray_can_skip_readiness_check(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu") as readiness:
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(tray.main(["--skip-readiness-check"]), 1)

        readiness.assert_not_called()

    def test_tray_reports_missing_desktop_session_before_qt_import(self):
        with mock.patch.object(tray.setup_wizard, "ensure_ready_for_menu", return_value=True):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(False, "no display")):
                with mock.patch.object(tray, "_load_qt_bindings") as load_qt:
                    with contextlib.redirect_stderr(io.StringIO()) as output:
                        self.assertEqual(tray.main([]), 1)

        load_qt.assert_not_called()
        self.assertIn("no display", output.getvalue())

    def test_remote_title_includes_mount_state(self):
        remote = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/docs",
        )

        self.assertEqual(tray._remote_title(remote, mounted=True), "Docs (drive) - Mounted")
        self.assertEqual(tray._remote_title(remote, mounted=False), "Docs (drive) - Unmounted")

    def test_remote_browser_url_uses_provider_web_home(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        self.assertEqual(tray._remote_browser_url(remote), "https://drive.google.com/drive/my-drive")

    def test_remote_browser_url_uses_webdav_url_when_available(self):
        remote = core.RemoteInfo(
            "Files__WebDAV",
            "Files",
            "WebDAV",
            "webdav",
            "/tmp/files",
            extra_info={"url": "https://cloud.example.com/files"},
        )

        self.assertEqual(tray._remote_browser_url(remote), "https://cloud.example.com/files")

    def test_remote_browser_url_ignores_generic_s3_endpoint(self):
        remote = core.RemoteInfo(
            "Archive__S3",
            "Archive",
            "S3",
            "s3",
            "/tmp/archive",
            extra_info={"endpoint": "https://s3.example.com"},
        )

        self.assertIsNone(tray._remote_browser_url(remote))

    def test_status_tooltip_summarizes_mounts(self):
        remotes = [
            core.RemoteInfo(
                name="Docs",
                alias="Docs",
                provider="drive",
                backend_type="drive",
                mount_path="/tmp/docs",
            ),
            core.RemoteInfo(
                name="Photos",
                alias="Photos",
                provider="dropbox",
                backend_type="dropbox",
                mount_path="/tmp/photos",
            ),
        ]

        self.assertEqual(tray._status_tooltip([], []), "Mountlet - no rclone remotes")
        self.assertEqual(
            tray._status_tooltip(remotes, []),
            "Mountlet - 0 mounted, 2 unmounted",
        )
        self.assertEqual(
            tray._status_tooltip(remotes, ["Docs"]),
            "Mountlet - mounted: Docs",
        )

    def test_mount_flag_options_do_not_include_allow_non_empty(self):
        tokens = {
            token
            for _label, _tooltip, option_tokens in tray.MOUNT_FLAG_OPTIONS
            for token in option_tokens
        }

        self.assertNotIn("--allow-non-empty", tokens)

    def test_local_port_available_detects_bound_port(self):
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            self.skipTest("socket creation is blocked in this environment")
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        try:
            self.assertFalse(tray._local_port_available(port))
        finally:
            server.close()
        self.assertTrue(tray._local_port_available(port))

    def test_local_port_status_reports_owner_hint_when_busy(self):
        fake_socket = mock.Mock()
        fake_socket.bind.side_effect = OSError("busy")

        with mock.patch.object(tray.socket, "socket", return_value=fake_socket):
            with mock.patch.object(tray, "_local_port_owner_hint", return_value="Process using the port: rclone (PID 123)."):
                self.assertEqual(
                    tray._local_port_status(53682),
                    (False, "Process using the port: rclone (PID 123)."),
                )

        fake_socket.close.assert_called_once_with()

    def test_summarize_port_owner_parses_ss_output(self):
        output = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess\n"
            'LISTEN 0 4096 127.0.0.1:53682 0.0.0.0:* users:(("rclone",pid=1767993,fd=7))'
        )

        self.assertEqual(
            tray._summarize_port_owner(output),
            "Process using the port: rclone (PID 1767993).",
        )

    def test_proc_listening_socket_inodes_finds_listening_port(self):
        tcp = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:D1B2 00000000:0000 0A 00000000:00000000 00:00000000 "
            "00000000 1000 0 98765 1 0000000000000000 100 0 0 10 0\n"
        )

        with mock.patch.object(Path, "read_text", side_effect=[tcp, ""]):
            self.assertEqual(tray._proc_listening_socket_inodes(53682), {"98765"})

    def test_config_bool_accepts_common_true_values(self):
        for value in ("true", "True", "1", "yes", "on"):
            self.assertTrue(tray._config_bool(value))

        for value in ("", "false", "0", "no", "off"):
            self.assertFalse(tray._config_bool(value))

    def test_packaged_icon_path_exists(self):
        icon_path = tray._packaged_icon_path()

        self.assertIsNotNone(icon_path)
        self.assertTrue(Path(icon_path or "").is_file())

    def test_left_click_activation_toggles_mountlet_window(self):
        fake_window = _FakeWindow()
        fake_qt = mock.Mock()
        fake_qt.QSystemTrayIcon.ActivationReason.Trigger = "trigger"
        fake_qt.QSystemTrayIcon.ActivationReason.DoubleClick = "double"
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = fake_qt
        tray_app.main_window = fake_window

        with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
            tray_app._handle_activation(fake_qt.QSystemTrayIcon.ActivationReason.Trigger)

        rebuild.assert_called_once_with()
        self.assertEqual(fake_window.toggle_calls, 1)

        with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
            tray_app._handle_activation(fake_qt.QSystemTrayIcon.ActivationReason.DoubleClick)

        rebuild.assert_not_called()
        self.assertEqual(fake_window.toggle_calls, 1)

    def test_mountlet_window_save_remote_order_preserves_existing_settings(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        original = {
            "Docs": settings.MountSettings(
                mount_path="docs",
                mount_flags=["--read-only"],
                auto_mount=True,
                enabled=True,
            ),
            "Photos": settings.MountSettings(
                mount_path="photos",
                mount_flags=[],
                auto_mount=False,
                enabled=False,
            ),
        }

        with mock.patch.object(tray, "load_mount_settings", return_value=original):
            with mock.patch.object(tray, "save_mount_settings") as save:
                mountlet_window._save_remote_order(["Photos", "Docs"])

        saved = save.call_args.args[0]
        self.assertEqual(saved["Photos"].order, 0)
        self.assertEqual(saved["Docs"].order, 1)
        self.assertEqual(saved["Docs"].mount_path, "docs")
        self.assertEqual(saved["Docs"].mount_flags, ["--read-only"])
        self.assertTrue(saved["Docs"].auto_mount)
        self.assertFalse(saved["Photos"].enabled)

    def test_new_remote_wizard_requires_drive_credentials_after_completion(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_name = "Docs"
        remote_without_token = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/docs",
            extra_info={"type": "drive"},
        )
        remote_with_token = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/docs",
            extra_info={"type": "drive", "token": "{}"},
        )

        with mock.patch.object(tray.core, "load_remotes", return_value=[remote_without_token]):
            self.assertFalse(wizard._created_remote_has_credentials())
        with mock.patch.object(tray.core, "load_remotes", return_value=[remote_with_token]):
            self.assertTrue(wizard._created_remote_has_credentials())

    def test_new_remote_wizard_requires_oauth_credentials_after_completion(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_name = "Cloud"
        remote_without_token = core.RemoteInfo(
            name="Cloud",
            alias="Cloud",
            provider="onedrive",
            backend_type="onedrive",
            mount_path="/tmp/cloud",
            extra_info={"type": "onedrive"},
        )
        remote_with_token = core.RemoteInfo(
            name="Cloud",
            alias="Cloud",
            provider="onedrive",
            backend_type="onedrive",
            mount_path="/tmp/cloud",
            extra_info={"type": "onedrive", "token": "{}", "drive_id": "drive", "drive_type": "personal"},
        )

        with mock.patch.object(tray.core, "load_remotes", return_value=[remote_without_token]):
            self.assertFalse(wizard._created_remote_has_credentials())
        with mock.patch.object(tray.core, "load_remotes", return_value=[remote_with_token]):
            self.assertTrue(wizard._created_remote_has_credentials())

    def test_load_visible_remotes_hides_incomplete_entries(self):
        with mock.patch.object(tray.core, "load_remotes", return_value=[]) as load_remotes:
            self.assertEqual(tray._load_visible_remotes(), [])

        load_remotes.assert_called_once_with(include_incomplete=False)

    def test_load_visible_remotes_hides_pending_wizard_entries(self):
        remote = core.RemoteInfo(
            name="Cloud",
            alias="Cloud",
            provider="onedrive",
            backend_type="onedrive",
            mount_path="/tmp/cloud",
            extra_info={"type": "onedrive", "token": "{}", "drive_id": "drive", "drive_type": "personal"},
        )
        tray._wizard_pending_remote_names.add("Cloud")
        self.addCleanup(tray._wizard_pending_remote_names.clear)

        with mock.patch.object(tray.core, "load_remotes", return_value=[remote]):
            self.assertEqual(tray._load_visible_remotes(), [])

    def test_rclone_port_owner_pid_only_matches_rclone(self):
        self.assertEqual(tray._rclone_port_owner_pid("Process using the port: rclone (PID 1234)."), 1234)
        self.assertIsNone(tray._rclone_port_owner_pid("Process using the port: python (PID 1234)."))

    def test_is_rclone_auth_port_error_matches_rclone_bind_failure(self):
        self.assertTrue(
            tray._is_rclone_auth_port_error(
                "config failed to refresh token: failed to start auth webserver: "
                "listen tcp 127.0.0.1:53682: bind: address already in use"
            )
        )
        self.assertFalse(tray._is_rclone_auth_port_error("listen tcp 127.0.0.1:12345: bind: address already in use"))

    def test_new_remote_wizard_can_offer_to_stop_stuck_rclone(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.dialog = mock.Mock()
        yes = 1
        no = 2
        wizard.qt = SimpleNamespace(
            QMessageBox=SimpleNamespace(
                StandardButton=SimpleNamespace(Yes=yes, No=no),
                question=mock.Mock(return_value=yes),
            )
        )

        with mock.patch.object(tray, "_terminate_process_id", return_value=True) as terminate:
            stopped = wizard._offer_to_stop_stuck_rclone("Process using the port: rclone (PID 1234).")

        self.assertTrue(stopped)
        terminate.assert_called_once_with(1234)

    def test_new_remote_wizard_recovers_from_rclone_port_error_by_waiting(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.status = mock.Mock()
        wizard._remote_name = "Docs__drive"
        wizard._remote_alias = "Docs"
        wizard._question = tray.rclone_wizard.RcloneConfigStep("state", {"Name": "config_is_local"})
        wizard._answer_field = mock.Mock()
        wizard._answer_group = mock.Mock()

        with mock.patch.object(wizard, "_cleanup_incomplete_remote") as cleanup:
            with mock.patch.object(wizard, "_show_setup_view") as show_setup:
                with mock.patch.object(wizard, "_update_action_button") as update_button:
                    recovered = wizard._recover_from_rclone_port_error(
                        "failed to start auth webserver: listen tcp 127.0.0.1:53682: bind: address already in use"
                    )

        self.assertTrue(recovered)
        cleanup.assert_called_once_with()
        show_setup.assert_called_once_with(True)
        update_button.assert_called_once_with()
        self.assertEqual(wizard._remote_name, "")
        self.assertIsNone(wizard._question)
        self.assertFalse(wizard._browser_port_available)
        wizard.status.setText.assert_called_once()
        self.assertIn("Waiting for rclone's browser sign-in port", wizard.status.setText.call_args.args[0])

    def test_new_remote_wizard_ignores_unrelated_rclone_errors(self):
        wizard = object.__new__(tray.NewRemoteWizard)

        with mock.patch.object(wizard, "_cleanup_incomplete_remote") as cleanup:
            recovered = wizard._recover_from_rclone_port_error(
                "failed to authenticate"
            )

        self.assertFalse(recovered)
        cleanup.assert_not_called()

    def test_new_remote_wizard_checks_port_before_browser_continue(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_name = "Docs"
        wizard._remote_type = "drive"
        wizard._state = "state"
        wizard._question = tray.rclone_wizard.RcloneConfigStep("state", {"Name": "config_is_local"})

        with mock.patch.object(wizard, "_answer_value", return_value="true"):
            with mock.patch.object(wizard, "_browser_auth_port_ready", return_value=False) as port_ready:
                with mock.patch.object(wizard, "_run_rclone") as run_rclone:
                    wizard._continue()

        port_ready.assert_called_once_with()
        run_rclone.assert_not_called()

    def test_new_remote_wizard_question_clears_status_text(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.qt = SimpleNamespace(
            QLabel=mock.Mock(side_effect=lambda text="": mock.Mock()),
            Qt=SimpleNamespace(TextInteractionFlag=SimpleNamespace(TextBrowserInteraction=1)),
        )
        wizard.question_layout = mock.Mock()
        wizard.question_frame = mock.Mock()
        wizard.status = mock.Mock()
        wizard.dialog = mock.Mock()
        wizard._answer_kind = ""
        wizard._answer_field = mock.Mock()

        with mock.patch.object(wizard, "_clear_layout"):
            with mock.patch.object(wizard, "_answer_widget", return_value=("text", wizard._answer_field)):
                with mock.patch.object(wizard, "_update_action_button"):
                    wizard._show_question(tray.rclone_wizard.RcloneConfigStep("state", {"Name": "drive_id"}))

        wizard.status.setText.assert_called_once_with("")

    def test_browser_auth_port_checks_once_before_launch(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "onedrive"
        wizard._drive_local_auth = True
        wizard.status = mock.Mock()

        with mock.patch.object(tray, "_local_port_status", return_value=(True, "")) as port_status:
            self.assertTrue(wizard._browser_auth_port_ready())

        port_status.assert_called_once_with(tray.RCLONE_OAUTH_LOCAL_PORT)
        wizard.status.setText.assert_called_once_with("")

    def test_browser_auth_port_wait_disables_create_button(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._question = None
        wizard._remote_name = ""
        wizard._remote_type = "drive"
        wizard._browser_port_available = True
        wizard.status = mock.Mock()
        wizard.action_button = mock.Mock()
        provider = mock.Mock()
        provider.currentData.return_value = "drive"
        local_auth = mock.Mock()
        local_auth.isChecked.return_value = True
        name = mock.Mock()
        name.text.return_value = "Docs"
        wizard.fields = {"provider": provider, "local_auth": local_auth, "name": name}

        with mock.patch.object(tray, "_local_port_status", return_value=(False, "Process using the port: rclone.")):
            wizard._update_browser_port_status()

        self.assertFalse(wizard._browser_port_available)
        wizard.action_button.setEnabled.assert_called_with(False)
        self.assertIn("Waiting for rclone's browser sign-in port", wizard.status.setText.call_args.args[0])

    def test_new_remote_wizard_local_browser_busy_message_is_specific(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "drive"
        wizard._drive_local_auth = True
        wizard._waiting_for_browser_auth = True

        self.assertEqual(
            wizard._busy_message(),
            "Waiting for browser authentication. A sign-in page should open in your browser.",
        )

    def test_new_remote_wizard_local_browser_message_resets_after_auth_step(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._waiting_for_browser_auth = True
        wizard._cancelled = False
        wizard.status = mock.Mock()
        wizard._question = None
        wizard._remote_type = "drive"
        step = tray.rclone_wizard.RcloneConfigStep("", {})

        with mock.patch.object(wizard, "_set_busy") as set_busy:
            with mock.patch.object(wizard, "_created_remote_has_credentials", return_value=False):
                with mock.patch.object(wizard, "_cleanup_incomplete_remote"):
                    with mock.patch.object(wizard, "_warning"):
                        wizard._handle_command_finished(step, None)

        self.assertFalse(wizard._waiting_for_browser_auth)
        set_busy.assert_called_once_with(False)

    def test_new_remote_wizard_can_hide_setup_view(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.initial_frame = mock.Mock()
        wizard.question_frame = mock.Mock()

        wizard._show_setup_view(False)
        wizard.initial_frame.setVisible.assert_called_once_with(False)
        wizard.question_frame.hide.assert_not_called()

        wizard._show_setup_view(True)
        wizard.question_frame.hide.assert_called_once_with()

    def test_new_remote_wizard_labels_drive_boolean_choices(self):
        wizard = object.__new__(tray.NewRemoteWizard)

        self.assertEqual(
            wizard._bool_radio_options("config_is_local", []),
            [
                ("true", "Open the browser on this computer"),
                ("false", "Authorize from another computer"),
            ],
        )
        self.assertEqual(
            wizard._bool_radio_options("config_team_drive", []),
            [
                ("false", "My Drive"),
                ("true", "Shared drive"),
            ],
        )

    def test_new_remote_wizard_applies_reused_drive_credentials(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._question = None
        credential_source = mock.Mock()
        credential_source.currentData.return_value = core.DriveOAuthCredentials(
            remote_name="Docs",
            client_id="docs-client",
            client_secret="docs-secret",
        )
        client_id = mock.Mock()
        client_secret = mock.Mock()
        wizard.fields = {
            "credential_source": credential_source,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        wizard._apply_credential_choice()

        client_id.setText.assert_called_once_with("docs-client")
        client_secret.setText.assert_called_once_with("docs-secret")
        client_id.setEnabled.assert_called_once_with(False)
        client_secret.setEnabled.assert_called_once_with(False)

    def test_drive_credential_option_label_prefers_existing_credentials(self):
        credentials = core.DriveOAuthCredentials("Docs, +1", "client", "secret", ("Docs", "Photos"))

        self.assertEqual(
            tray._drive_credential_option_label(credentials, 1),
            "Existing credentials (recommended)",
        )
        self.assertEqual(
            tray._drive_credential_option_label(credentials, 2),
            "Existing: Docs, +1",
        )

    def test_new_remote_wizard_uses_builtin_drive_client_by_default(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._question = None
        credential_source = mock.Mock()
        credential_source.currentData.return_value = tray.DRIVE_CREDENTIAL_SOURCE_BUILTIN
        client_id = mock.Mock()
        client_secret = mock.Mock()
        wizard.fields = {
            "credential_source": credential_source,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        wizard._apply_credential_choice()

        client_id.clear.assert_called_once_with()
        client_secret.clear.assert_called_once_with()
        client_id.setEnabled.assert_called_once_with(False)
        client_secret.setEnabled.assert_called_once_with(False)

    def test_new_remote_wizard_allows_custom_drive_credentials(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._question = None
        credential_source = mock.Mock()
        credential_source.currentData.return_value = tray.DRIVE_CREDENTIAL_SOURCE_CUSTOM
        client_id = mock.Mock()
        client_secret = mock.Mock()
        wizard.fields = {
            "credential_source": credential_source,
            "client_id": client_id,
            "client_secret": client_secret,
        }

        wizard._apply_credential_choice()

        client_id.setText.assert_not_called()
        client_secret.setText.assert_not_called()
        client_id.setEnabled.assert_called_once_with(True)
        client_secret.setEnabled.assert_called_once_with(True)

    def test_new_remote_wizard_answers_known_drive_questions_from_form(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._drive_local_auth = True
        wizard._drive_shared_drive = False
        wizard._drive_team_drive = ""

        self.assertEqual(
            wizard._automatic_answer(tray.rclone_wizard.RcloneConfigStep("state", {"Name": "config_is_local"})),
            "true",
        )
        self.assertEqual(
            wizard._automatic_answer(tray.rclone_wizard.RcloneConfigStep("state", {"Name": "config_team_drive"})),
            "false",
        )

    def test_new_remote_wizard_answers_shared_drive_prompt_by_help_text(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._drive_shared_drive = False
        wizard._drive_team_drive = ""
        step = tray.rclone_wizard.RcloneConfigStep(
            "state",
            {
                "Name": "drive_kind",
                "Type": "bool",
                "Help": "Configure this as a Shared Drive?",
            },
        )

        self.assertEqual(wizard._automatic_answer(step), "false")

    def test_new_remote_wizard_uses_generic_oauth_args_for_non_drive(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "dropbox"
        wizard._drive_local_auth = False

        self.assertEqual(wizard._initial_config_args(), ["config_is_local", "false"])

    def test_new_remote_wizard_uses_no_initial_args_for_webdav(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "webdav"

        self.assertEqual(wizard._initial_config_args(), [])

    def test_new_remote_wizard_allows_same_alias_across_providers(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        remotes = [
            core.RemoteInfo(
                name="Media",
                alias="Media",
                provider="drive",
                backend_type="drive",
                mount_path="/tmp/drive-media",
            ),
            core.RemoteInfo(
                name="Media__dropbox",
                alias="Media",
                provider="dropbox",
                backend_type="dropbox",
                mount_path="/tmp/dropbox-media",
            ),
        ]

        self.assertFalse(wizard._display_name_exists("Media", "box", remotes))
        self.assertTrue(wizard._display_name_exists("Media", "dropbox", remotes))
        self.assertEqual(wizard._config_remote_name("Media", "box", remotes), "Media__Box")
        self.assertEqual(wizard._config_remote_name("Photos", "box", remotes), "Photos__Box")

    def test_new_remote_wizard_mounts_created_remote_when_requested(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._connect_after_create = True
        wizard._remote_name = "Docs"
        step = tray.rclone_wizard.RcloneConfigStep("", {})

        with mock.patch.object(wizard, "_created_remote_has_credentials", return_value=True):
            with mock.patch.object(wizard, "_mount_created_remote") as mount_created:
                with mock.patch.object(wizard, "_set_busy"):
                    wizard._handle_command_finished(step, None)

        mount_created.assert_called_once_with()

    def test_mountlet_window_sorts_remotes_by_name(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {}
        remotes = [
            core.RemoteInfo("Beta__dropbox", "Beta", "dropbox", "dropbox", "/tmp/beta"),
            core.RemoteInfo("Alpha__drive", "Alpha", "drive", "drive", "/tmp/alpha"),
        ]

        self.assertEqual(window._sorted_remotes(remotes, "registration"), remotes)

        sorted_remotes = window._sorted_remotes(remotes, "name")

        self.assertEqual([remote.name for remote in sorted_remotes], ["Alpha__drive", "Beta__dropbox"])

    def test_mountlet_window_sorts_remotes_by_provider(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {}
        remotes = [
            core.RemoteInfo("Docs__onedrive", "Docs", "onedrive", "onedrive", "/tmp/docs"),
            core.RemoteInfo("Docs__box", "Docs", "box", "box", "/tmp/docs-box"),
        ]

        sorted_remotes = window._sorted_remotes(remotes, "provider")

        self.assertEqual([remote.name for remote in sorted_remotes], ["Docs__box", "Docs__onedrive"])

    def test_mountlet_window_sorts_remotes_by_storage_usage(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {
            "Small": core.StorageUsage("small", used=20, total=100),
            "Large": core.StorageUsage("large", used=50, total=200),
            "Unknown": core.StorageUsage("?"),
        }
        remotes = [
            core.RemoteInfo("Small", "Small", "drive", "drive", "/tmp/small"),
            core.RemoteInfo("Unknown", "Unknown", "drive", "drive", "/tmp/unknown"),
            core.RemoteInfo("Large", "Large", "drive", "drive", "/tmp/large"),
        ]

        self.assertEqual(
            [remote.name for remote in window._sorted_remotes(remotes, "size")],
            ["Large", "Small", "Unknown"],
        )
        self.assertEqual(
            [remote.name for remote in window._sorted_remotes(remotes, "remaining")],
            ["Small", "Large", "Unknown"],
        )

    def test_mountlet_window_keeps_manual_move_available_after_sorting(self):
        window = object.__new__(tray.MountletWindow)
        window._current_remote_names = ["Alpha", "Beta"]

        self.assertTrue(window._can_move_remote("Beta", -1))

    def test_mountlet_window_remote_title_colors_provider(self):
        window = object.__new__(tray.MountletWindow)
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        title = window._display_remote_name(remote)

        self.assertIn("Docs", title)
        self.assertIn("(Drive)", title)
        self.assertIn(tray.PROVIDER_COLORS["drive"], title)

    def test_mountlet_window_remote_title_escapes_rich_text(self):
        window = object.__new__(tray.MountletWindow)
        remote = core.RemoteInfo("A < B__Box", "A < B", "Box", "box", "/tmp/box")

        title = window._display_remote_name(remote)

        self.assertIn("A &lt; B", title)
        self.assertNotIn("A < B", title)

    def test_mountlet_window_sort_action_saves_order(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {}
        window._current_remote_names = ["Beta", "Alpha"]
        window.tray_app = mock.Mock()
        remotes = [
            core.RemoteInfo("Beta", "Beta", "dropbox", "dropbox", "/tmp/beta"),
            core.RemoteInfo("Alpha", "Alpha", "drive", "drive", "/tmp/alpha"),
        ]

        with mock.patch.object(tray, "_load_visible_remotes", return_value=remotes):
            with mock.patch.object(window, "_save_remote_order") as save:
                window._sort_remote_order("name")

        save.assert_called_once_with(["Alpha", "Beta"])
        self.assertEqual(window._current_remote_names, [])
        window.tray_app.rebuild_menus.assert_called_once_with()

    def test_mountlet_window_registration_sort_clears_manual_order(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {}
        window._current_remote_names = ["Beta", "Alpha"]
        window.tray_app = mock.Mock()
        remotes = [
            core.RemoteInfo("Beta", "Beta", "dropbox", "dropbox", "/tmp/beta"),
            core.RemoteInfo("Alpha", "Alpha", "drive", "drive", "/tmp/alpha"),
        ]
        mount_settings = {
            "Alpha": settings.MountSettings(order=0, auto_mount=True),
            "Beta": settings.MountSettings(order=1, mount_path="dropbox/Beta"),
        }

        with mock.patch.object(tray, "_load_visible_remotes", return_value=remotes):
            with mock.patch.object(tray, "load_mount_settings", return_value=mount_settings):
                with mock.patch.object(tray, "save_mount_settings") as save:
                    window._sort_remote_order("registration")

        saved = save.call_args.args[0]
        self.assertIsNone(saved["Alpha"].order)
        self.assertTrue(saved["Alpha"].auto_mount)
        self.assertIsNone(saved["Beta"].order)
        self.assertEqual(saved["Beta"].mount_path, "dropbox/Beta")
        self.assertEqual(window._current_remote_names, [])
        window.tray_app.rebuild_menus.assert_called_once_with()

    def test_mountlet_window_reverse_action_saves_reversed_order(self):
        window = object.__new__(tray.MountletWindow)
        window._current_remote_names = ["Alpha", "Beta"]
        window.tray_app = mock.Mock()
        remotes = [
            core.RemoteInfo("Alpha", "Alpha", "drive", "drive", "/tmp/alpha"),
            core.RemoteInfo("Beta", "Beta", "dropbox", "dropbox", "/tmp/beta"),
        ]

        with mock.patch.object(tray, "_load_visible_remotes", return_value=remotes):
            with mock.patch.object(window, "_save_remote_order") as save:
                window._reverse_remote_order()

        save.assert_called_once_with(["Beta", "Alpha"])
        self.assertEqual(window._current_remote_names, [])
        window.tray_app.rebuild_menus.assert_called_once_with()

    def test_mountlet_window_toggle_hides_visible_window_on_current_desktop(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isActiveWindow.return_value = True
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=True):
            with mock.patch.object(mountlet_window, "show") as show:
                mountlet_window.toggle_from_tray()

        mountlet_window.window.hide.assert_called_once_with()
        show.assert_not_called()

    def test_mountlet_window_toggle_raises_visible_unfocused_window(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isActiveWindow.return_value = False

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=True):
            with mock.patch.object(mountlet_window, "show") as show:
                mountlet_window.toggle_from_tray()

        mountlet_window.window.hide.assert_not_called()
        show.assert_called_once_with()

    def test_mountlet_window_close_hides_window_stack(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        event = mock.Mock()

        with mock.patch.object(mountlet_window, "_tray_is_quitting", return_value=False):
            with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
                handled = mountlet_window._handle_window_close(event)

        self.assertTrue(handled)
        hide_stack.assert_called_once_with()
        event.ignore.assert_called_once_with()

    def test_mountlet_window_toggle_shows_visible_window_from_other_desktop(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=False):
            with mock.patch.object(mountlet_window, "show") as show:
                mountlet_window.toggle_from_tray()

        mountlet_window.window.hide.assert_not_called()
        show.assert_called_once_with()

    def test_mountlet_window_show_refocuses_existing_window_without_repositioning(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isMinimized.return_value = False

        with mock.patch.object(mountlet_window, "refresh") as refresh:
            with mock.patch.object(mountlet_window, "_position_near_tray") as position:
                mountlet_window.show()

        refresh.assert_called_once_with()
        position.assert_not_called()
        mountlet_window.window.show.assert_called_once_with()
        mountlet_window.window.raise_.assert_called_once_with()
        mountlet_window.window.activateWindow.assert_called_once_with()

    def test_mountlet_window_show_restores_minimized_window(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isMinimized.return_value = True

        with mock.patch.object(mountlet_window, "refresh"):
            with mock.patch.object(mountlet_window, "_position_near_tray"):
                mountlet_window.show()

        mountlet_window.window.show.assert_not_called()
        mountlet_window.window.showNormal.assert_called_once_with()
        mountlet_window.window.raise_.assert_called_once_with()
        mountlet_window.window.activateWindow.assert_called_once_with()

    def test_mountlet_window_show_reopens_visible_window_from_other_desktop(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isMinimized.return_value = False
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=False):
            with mock.patch.object(mountlet_window, "refresh") as refresh:
                with mock.patch.object(mountlet_window, "_position_near_tray") as position:
                    mountlet_window.show()

        mountlet_window.window.hide.assert_called_once_with()
        refresh.assert_called_once_with()
        position.assert_called_once_with()
        mountlet_window.window.show.assert_called_once_with()
        mountlet_window.window.raise_.assert_called_once_with()
        mountlet_window.window.activateWindow.assert_called_once_with()

    def test_x11_qt_window_is_on_current_desktop_compares_window_desktop(self):
        window = mock.Mock()
        window.winId.return_value = 12345

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_x11_window_desktop", return_value=3):
                self.assertTrue(tray._x11_qt_window_is_on_current_desktop(window))
            with mock.patch.object(tray, "_x11_window_desktop", return_value=2):
                self.assertFalse(tray._x11_qt_window_is_on_current_desktop(window))
            with mock.patch.object(tray, "_x11_window_desktop", return_value=0xFFFFFFFF):
                self.assertTrue(tray._x11_qt_window_is_on_current_desktop(window))

    def test_set_x11_window_desktop_uses_net_wm_desktop_value(self):
        window = mock.Mock()
        window.winId.return_value = 12345
        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.dict(tray.os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}, clear=True):
                with mock.patch.object(tray.shutil, "which", return_value="/usr/bin/xprop"):
                    with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(tray._set_x11_window_desktop(window, 3))

        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/xprop",
                "-id",
                "12345",
                "-f",
                "_NET_WM_DESKTOP",
                "32c",
                "-set",
                "_NET_WM_DESKTOP",
                "3",
            ],
        )

    def test_set_x11_window_desktop_skips_wayland(self):
        window = mock.Mock()

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.dict(tray.os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}, clear=True):
                with mock.patch.object(tray.subprocess, "run") as run:
                    self.assertFalse(tray._set_x11_window_desktop(window, 3))

        run.assert_not_called()

    def test_focus_window_moves_to_current_desktop_before_activating(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isMinimized.return_value = False

        with mock.patch.object(tray, "_move_x11_window_to_current_desktop", return_value=True) as move:
            mountlet_window._focus_window()

        self.assertEqual(move.call_args_list, [mock.call(mountlet_window.window), mock.call(mountlet_window.window)])
        mountlet_window.window.show.assert_called_once_with()
        mountlet_window.window.raise_.assert_called_once_with()
        mountlet_window.window.activateWindow.assert_called_once_with()

    def test_focus_window_restores_modal_child_z_order(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        child = mock.Mock()
        child.isMinimized.return_value = False
        child.isVisible.return_value = True
        child.parentWidget.return_value = mock.Mock()
        mountlet_window.window = child.parentWidget.return_value
        mountlet_window.window.isMinimized.return_value = False
        qt = mock.Mock()
        qt.QApplication.activeModalWidget.return_value = child
        qt.QApplication.activeWindow.return_value = None
        mountlet_window.qt = qt

        with mock.patch.object(tray, "_move_x11_window_to_current_desktop", return_value=True):
            mountlet_window._focus_window()

        mountlet_window.window.show.assert_called_once_with()
        mountlet_window.window.raise_.assert_not_called()
        mountlet_window.window.activateWindow.assert_not_called()
        child.show.assert_called_once_with()
        child.raise_.assert_called_once_with()
        child.activateWindow.assert_called_once_with()

    def test_focus_window_restores_tracked_child_z_order(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        child = mock.Mock()
        child.isMinimized.return_value = False
        child.isVisible.return_value = True
        child.parentWidget.return_value = mock.Mock()
        mountlet_window.window = child.parentWidget.return_value
        mountlet_window.window.isMinimized.return_value = False
        mountlet_window._child_dialogs = [child]
        qt = mock.Mock()
        qt.QApplication.activeModalWidget.return_value = None
        qt.QApplication.activeWindow.return_value = mountlet_window.window
        mountlet_window.qt = qt

        with mock.patch.object(tray, "_move_x11_window_to_current_desktop", return_value=True):
            mountlet_window._focus_window()

        child.show.assert_called_once_with()
        child.raise_.assert_called_once_with()
        child.activateWindow.assert_called_once_with()
        mountlet_window.window.raise_.assert_not_called()
        mountlet_window.window.activateWindow.assert_not_called()
        self.assertEqual(qt.QTimer.singleShot.call_count, 3)

    def test_hide_window_stack_rejects_child_dialogs(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        child = mock.Mock()
        owner = mock.Mock()
        mountlet_window._child_dialogs = [child]
        mountlet_window._child_dialog_owners = {child: owner}

        mountlet_window._hide_window_stack()

        owner._reject.assert_called_once_with()
        child.hide.assert_not_called()
        mountlet_window.window.hide.assert_called_once_with()
        self.assertEqual(mountlet_window._child_dialogs, [])
        self.assertEqual(mountlet_window._child_dialog_owners, {})

    def test_open_child_dialog_tracks_owner_and_uses_modeless_show(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}
        mountlet_window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                WindowModality=SimpleNamespace(NonModal="nonmodal"),
            )
        )
        owner = SimpleNamespace(dialog=mock.Mock())
        on_accepted = mock.Mock()

        with mock.patch.object(mountlet_window, "_raise_child_windows") as raise_child:
            mountlet_window._open_child_dialog(owner, on_accepted)

        self.assertEqual(mountlet_window._child_dialogs, [owner.dialog])
        self.assertIs(mountlet_window._child_dialog_owners[owner.dialog], owner)
        owner.dialog.accepted.connect.assert_called_once_with(on_accepted)
        owner.dialog.finished.connect.assert_called_once()
        owner.dialog.setModal.assert_called_once_with(False)
        owner.dialog.setWindowFlag.assert_not_called()
        owner.dialog.show.assert_called_once_with()
        raise_child.assert_called_once_with()

    def test_restore_child_offsets_moves_subwindow_with_main_window(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        child = mock.Mock()
        mountlet_window._child_dialogs = [child]

        with mock.patch.object(mountlet_window, "_window_position", side_effect=[(300, 400)]):
            mountlet_window._restore_child_offsets({child: (25, 35)})

        child.move.assert_called_once_with(325, 435)

    def test_request_quit_stops_refresh_and_hides_ui(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.timer = mock.Mock()
        tray_app.main_window = mock.Mock()
        tray_app.tray = mock.Mock()
        tray_app.app = mock.Mock()

        with mock.patch.object(tray.rclone_wizard, "cancel_all_remote_configs") as cancel_configs:
            tray_app.request_quit()

        self.assertTrue(tray_app._quitting)
        cancel_configs.assert_called_once_with()
        tray_app.timer.stop.assert_called_once_with()
        tray_app.main_window.prepare_quit.assert_called_once_with()
        tray_app.tray.hide.assert_called_once_with()
        tray_app.app.exit.assert_called_once_with(0)

    def test_schedule_forced_exit_uses_daemon_timer(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._allow_forced_exit = True
        tray_app._forced_exit_scheduled = False
        timer = mock.Mock()

        with mock.patch.object(tray.threading, "Timer", return_value=timer) as timer_class:
            tray_app._schedule_forced_exit()

        timer_class.assert_called_once_with(tray.FORCED_QUIT_SECONDS, tray_app._force_exit_if_still_quitting)
        self.assertTrue(tray_app._forced_exit_scheduled)
        self.assertTrue(timer.daemon)
        timer.start.assert_called_once_with()

    def test_show_folder_uses_file_manager_dbus_service_on_linux(self):
        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                self.assertTrue(tray._show_folder_with_file_manager("/tmp/docs"))

        command = run.call_args.args[0]
        self.assertIn("--dest=org.freedesktop.FileManager1", command)
        self.assertIn("org.freedesktop.FileManager1.ShowFolders", command)
        self.assertIn("array:string:file:///tmp/docs", command)
        self.assertEqual(command[-1], "string:")

    def test_open_folder_uses_qt_default_opener_by_default(self):
        qt = mock.Mock()
        qt.QDesktopServices.openUrl.return_value = True
        qt.QUrl.fromLocalFile.return_value = "qt-url"

        with mock.patch.object(tray, "_show_folder_with_file_manager") as show_folder:
            with mock.patch.object(tray, "_open_folder_with_known_file_manager", return_value=False):
                self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        show_folder.assert_not_called()
        qt.QUrl.fromLocalFile.assert_called_once_with("/tmp/docs")
        qt.QDesktopServices.openUrl.assert_called_once_with("qt-url")

    def test_open_folder_uses_dolphin_new_window_when_dolphin_is_default(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=False):
                with mock.patch.object(tray.shutil, "which", return_value="/usr/bin/dolphin"):
                    with mock.patch.object(tray.subprocess, "Popen") as popen:
                        self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/dolphin", "--new-window", "/tmp/docs"])
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_prefers_dolphin_dbus_tab_when_available(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=True) as open_tab:
                self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        open_tab.assert_called_once_with("/tmp/docs", current_desktop=True, focus=True)
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_in_dolphin_tab_calls_running_dolphin_window(self):
        completed = SimpleNamespace(returncode=0)
        windows = [("org.kde.dolphin-1234", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openDirectories",
                "file:///tmp/docs",
                "false",
            ],
        )

    def test_open_folder_in_dolphin_tab_falls_back_when_no_window_is_available(self):
        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=[]):
                    with mock.patch.object(tray, "_dolphin_slow_tab_targets", return_value=[]):
                        with mock.patch.object(tray.subprocess, "run") as run:
                            self.assertFalse(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        run.assert_not_called()

    def test_open_folder_in_dolphin_tab_tries_next_window_after_failure(self):
        def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
            if "org.kde.dolphin-1234" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        windows = [
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"),
        ]
        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", side_effect=run_command) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 3)

    def test_open_folder_in_dolphin_tab_reuses_cached_window(self):
        completed = SimpleNamespace(returncode=0)
        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets") as fast_targets:
                    with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        fast_targets.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_open_folder_in_dolphin_tab_clears_stale_cache(self):
        def run_command(command: list[str], **kwargs: object) -> SimpleNamespace:
            if "org.kde.dolphin-1234" in command:
                return SimpleNamespace(returncode=1)
            return SimpleNamespace(returncode=0)

        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=None):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                    with mock.patch.object(tray.subprocess, "run", side_effect=run_command) as run:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        self.assertEqual(run.call_count, 3)
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_dolphin_dbus_windows_are_sorted_by_newest_service_suffix(self):
        def qdbus_lines(args: list[str]) -> list[str]:
            if args == []:
                return [
                    "org.kde.dolphin-1234",
                    "org.kde.dolphin-5678",
                    "org.example.Other",
                ]
            if args == ["org.kde.dolphin-1234"]:
                return ["/dolphin/Dolphin_1"]
            if args == ["org.kde.dolphin-5678"]:
                return ["/dolphin/Dolphin_2"]
            return []

        with mock.patch.object(tray, "_qdbus_lines", side_effect=qdbus_lines):
            self.assertEqual(
                tray._dolphin_dbus_windows(),
                [
                    ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
                    ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
                ],
            )

    def test_parse_xprop_cardinal_reads_desktop_number(self):
        self.assertEqual(tray._parse_xprop_cardinal("_NET_CURRENT_DESKTOP(CARDINAL) = 3"), 3)
        self.assertIsNone(tray._parse_xprop_cardinal("_NET_CURRENT_DESKTOP:  not found."))

    def test_current_desktop_targets_filter_dolphin_windows_by_x11_desktop(self):
        windows = [
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"),
        ]

        def is_on_desktop(
            dbus: object,
            qdbus: object,
            service: str,
            object_path: str,
            desktop: int,
        ) -> bool:
            return service == "org.kde.dolphin-5678" and desktop == 3

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_dolphin_fast_tab_targets", return_value=windows):
                with mock.patch.object(tray, "_dolphin_window_is_on_desktop", side_effect=is_on_desktop):
                    self.assertEqual(
                        tray._dolphin_current_desktop_targets(None, "/usr/bin/qdbus6", set()),
                        [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")],
                    )

    def test_open_folder_in_dolphin_tab_uses_current_desktop_window(self):
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=windows):
                    with mock.patch.object(tray, "_open_folder_in_dolphin_window", return_value=True) as open_window:
                        self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        open_window.assert_called_once()
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_open_folder_in_dolphin_tab_ignores_cached_window_from_other_desktop(self):
        tray._dolphin_tab_target_cache = ("org.kde.dolphin-1234", "/dolphin/Dolphin_1")
        windows = [("org.kde.dolphin-5678", "/dolphin/Dolphin_1")]

        def is_on_desktop(
            dbus: object,
            qdbus: object,
            service: str,
            object_path: str,
            desktop: int,
        ) -> bool:
            return service == "org.kde.dolphin-5678"

        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_window_is_on_desktop", side_effect=is_on_desktop):
                    with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=windows):
                        with mock.patch.object(
                            tray,
                            "_open_folder_in_dolphin_window",
                            return_value=True,
                        ) as open_window:
                            self.assertTrue(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        open_window.assert_called_once()
        self.assertEqual(open_window.call_args.args[2], "org.kde.dolphin-5678")
        self.assertEqual(tray._dolphin_tab_target_cache, ("org.kde.dolphin-5678", "/dolphin/Dolphin_1"))

    def test_open_folder_in_dolphin_tab_returns_false_when_current_desktop_has_no_dolphin_window(self):
        with mock.patch.object(tray, "_x11_current_desktop", return_value=3):
            with mock.patch.object(tray, "_qdbus_binary", return_value="/usr/bin/qdbus6"):
                with mock.patch.object(tray, "_dolphin_current_desktop_targets", return_value=[]):
                    with mock.patch.object(tray, "_dolphin_fast_tab_targets") as fast_targets:
                        self.assertFalse(tray._open_folder_in_dolphin_tab("/tmp/docs"))

        fast_targets.assert_not_called()

    def test_dolphin_dbus_services_can_use_qtdbus_without_qdbus_subprocess(self):
        reply = mock.Mock()
        reply.isValid.return_value = True
        reply.value.return_value = [
            "org.example.Other",
            "org.kde.dolphin-1234",
            "org.kde.dolphin-5678",
        ]
        bus = mock.Mock()
        bus.interface.return_value.registeredServiceNames.return_value = reply
        dbus = SimpleNamespace(bus=bus)

        with mock.patch.object(tray, "_qdbus_lines") as qdbus_lines:
            self.assertEqual(
                tray._dolphin_dbus_services(dbus),
                ["org.kde.dolphin-5678", "org.kde.dolphin-1234"],
            )

        qdbus_lines.assert_not_called()

    def test_open_folder_in_dolphin_window_can_use_qtdbus_without_subprocess(self):
        reply = mock.Mock()
        reply.errorName.return_value = ""
        interface = mock.Mock()
        interface.isValid.return_value = True
        interface.call.return_value = reply
        dbus = SimpleNamespace(QDBusInterface=mock.Mock(return_value=interface), bus=mock.Mock())

        with mock.patch.object(tray.subprocess, "run") as run:
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    dbus,
                    None,
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        self.assertEqual(
            interface.call.call_args_list,
            [
                mock.call("openDirectories", ["file:///tmp/docs"], False),
                mock.call("activateWindow", ""),
            ],
        )
        run.assert_not_called()

    def test_open_folder_in_dolphin_window_focuses_after_qdbus_fallback_open(self):
        completed = SimpleNamespace(returncode=0)

        with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1],
            mock.call(
                [
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "org.kde.dolphin.MainWindow.activateWindow",
                    "",
                ],
                stdout=tray.subprocess.DEVNULL,
                stderr=tray.subprocess.DEVNULL,
                timeout=1,
            ),
        )

    def test_open_folder_in_dolphin_window_ignores_focus_failure(self):
        open_completed = SimpleNamespace(returncode=0)

        with mock.patch.object(
            tray.subprocess,
            "run",
            side_effect=[open_completed, tray.subprocess.TimeoutExpired("qdbus6", 1)],
        ):
            self.assertTrue(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

    def test_open_folder_in_dolphin_window_does_not_focus_after_failed_open(self):
        completed = SimpleNamespace(returncode=1)

        with mock.patch.object(tray.subprocess, "run", return_value=completed) as run:
            self.assertFalse(
                tray._open_folder_in_dolphin_window(
                    None,
                    "/usr/bin/qdbus6",
                    "org.kde.dolphin-1234",
                    "/dolphin/Dolphin_1",
                    "file:///tmp/docs",
                )
            )

        run.assert_called_once_with(
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openDirectories",
                "file:///tmp/docs",
                "false",
            ],
            stdout=tray.subprocess.DEVNULL,
            stderr=tray.subprocess.DEVNULL,
            timeout=1,
        )

    def test_ordered_dolphin_dbus_windows_puts_active_window_first(self):
        windows = [
            ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
            ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
        ]

        def is_active(qdbus: str, service: str, object_path: str) -> bool:
            return service == "org.kde.dolphin-1234"

        with mock.patch.object(tray, "_dolphin_dbus_windows", return_value=windows):
            with mock.patch.object(tray, "_dolphin_window_is_active", side_effect=is_active):
                self.assertEqual(
                    tray._ordered_dolphin_dbus_windows("/usr/bin/qdbus6"),
                    [
                        ("org.kde.dolphin-1234", "/dolphin/Dolphin_1"),
                        ("org.kde.dolphin-5678", "/dolphin/Dolphin_2"),
                    ],
                )

    def test_open_folder_uses_dolphin_new_window_when_dbus_tab_fails(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_default_directory_app", return_value="org.kde.dolphin.desktop"):
            with mock.patch.object(tray.shutil, "which", return_value="/usr/bin/dolphin"):
                with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=False):
                    with mock.patch.object(tray.subprocess, "Popen") as popen:
                        self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/dolphin", "--new-window", "/tmp/docs"])
        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_folder_can_use_file_manager_service_strategy(self):
        qt = mock.Mock()

        with mock.patch.object(tray, "_show_folder_with_file_manager", return_value=True):
            self.assertTrue(tray._open_folder_default(qt, "/tmp/docs", strategy="file-manager-service"))

        qt.QUrl.fromLocalFile.assert_not_called()
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_text_file_focused_opens_known_editor(self):
        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.shutil, "which", side_effect=lambda name: "/usr/bin/kate" if name == "kate" else None):
                with mock.patch.object(tray.subprocess, "Popen") as popen:
                    self.assertTrue(tray._open_text_file_focused(Path("/tmp/config.toml")))

        self.assertEqual(popen.call_args.args[0], ["/usr/bin/kate", "/tmp/config.toml"])

    def test_open_text_file_focused_falls_back_without_known_editor(self):
        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.shutil, "which", return_value=None):
                with mock.patch.object(tray.subprocess, "Popen") as popen:
                    self.assertFalse(tray._open_text_file_focused(Path("/tmp/config.toml")))

        popen.assert_not_called()

    def test_open_folder_action_reports_failure_when_default_opener_fails(self):
        remote = core.RemoteInfo(
            name="Docs",
            alias="Docs",
            provider="drive",
            backend_type="drive",
            mount_path="/tmp/missing-docs",
        )
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = mock.Mock()

        with tempfile.TemporaryDirectory() as tempdir:
            remote.mount_path = tempdir
            with mock.patch.object(tray, "_open_folder_default", return_value=False):
                with mock.patch.object(tray_app, "_notify") as notify:
                    tray_app._open_folder(remote)

        notify.assert_called_once_with("Open folder", "Could not open the mount folder.", success=False)

    def test_open_remote_in_browser_uses_provider_url(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = mock.Mock()
        tray_app.qt.QUrl.return_value = "qt-url"
        tray_app.qt.QDesktopServices.openUrl.return_value = True

        with mock.patch.object(tray_app, "_notify") as notify:
            tray_app._open_remote_in_browser(remote)

        tray_app.qt.QUrl.assert_called_once_with("https://drive.google.com/drive/my-drive")
        tray_app.qt.QDesktopServices.openUrl.assert_called_once_with("qt-url")
        notify.assert_not_called()

    def test_open_remote_in_browser_reports_missing_url(self):
        remote = core.RemoteInfo("Archive__S3", "Archive", "S3", "s3", "/tmp/archive")
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = mock.Mock()

        with mock.patch.object(tray_app, "_notify") as notify:
            tray_app._open_remote_in_browser(remote)

        tray_app.qt.QDesktopServices.openUrl.assert_not_called()
        notify.assert_called_once_with(
            "Open in browser",
            "This remote does not have a known browser view.",
            success=False,
        )

    def test_remount_changes_match_mounted_remotes_by_name(self):
        old_remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/old/docs")
        unchanged_remote = core.RemoteInfo("Photos", "Photos", "drive", "drive", "/same/photos")
        new_remotes = [
            core.RemoteInfo("Docs", "Docs", "drive", "drive", "/new/docs"),
            core.RemoteInfo("Photos", "Photos", "drive", "drive", "/same/photos"),
        ]
        window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray.core, "load_remotes", return_value=new_remotes):
            changes = window._remount_changes([old_remote, unchanged_remote], {"Docs"})

        self.assertEqual(changes, [(old_remote, new_remotes[0])])

    def test_remount_changes_ignore_unmounted_remotes(self):
        old_remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/old/docs")
        new_remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/new/docs")
        window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray.core, "load_remotes", return_value=[new_remote]):
            changes = window._remount_changes([old_remote], set())

        self.assertEqual(changes, [])

    def test_remount_changes_include_flag_changes_for_mounted_remotes(self):
        old_remote = core.RemoteInfo(
            "Docs",
            "Docs",
            "drive",
            "drive",
            "/same/docs",
            flags=["--vfs-cache-mode", "full"],
        )
        new_remote = core.RemoteInfo(
            "Docs",
            "Docs",
            "drive",
            "drive",
            "/same/docs",
            flags=["--vfs-cache-mode", "full", "--read-only"],
        )
        window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray.core, "load_remotes", return_value=[new_remote]):
            changes = window._remount_changes([old_remote], {"Docs"})

        self.assertEqual(changes, [(old_remote, new_remote)])

    def test_schedule_auto_mounts_only_schedules_configured_unmounted_remotes(self):
        remotes = [
            core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs", auto_mount=True),
            core.RemoteInfo("Photos", "Photos", "drive", "drive", "/tmp/photos", auto_mount=False),
        ]
        qt = mock.Mock()
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = qt

        with mock.patch.object(tray.core, "load_remotes", return_value=remotes):
            with mock.patch.object(tray.core, "is_mounted", return_value=False):
                with mock.patch.object(tray, "load_app_settings") as load_settings:
                    load_settings.return_value.auto_mount_delay = 1.5
                    tray_app._schedule_auto_mounts()

        qt.QTimer.singleShot.assert_called_once()
        self.assertEqual(qt.QTimer.singleShot.call_args.args[0], 1500)

    def test_auto_mount_reports_results_and_rebuilds_menus(self):
        remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs", auto_mount=True)
        tray_app = object.__new__(tray.MountletTray)

        with mock.patch.object(tray.core, "mount_all", return_value=(["Docs"], [])) as mount_all:
            with mock.patch.object(tray_app, "_notify") as notify:
                with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
                    tray_app._auto_mount([remote])

        mount_all.assert_called_once_with([remote])
        notify.assert_called_once_with("Auto-mount", "Mounted: Docs", success=True)
        rebuild.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
