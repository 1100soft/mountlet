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
from mountlet.platform_services.linux import LinuxPlatformServices


DOCS_URI = Path("/tmp/docs").resolve().as_uri()


class _FakeWindow:
    def __init__(self) -> None:
        self.show_calls = 0
        self.toggle_calls = 0

    def show(self) -> None:
        self.show_calls += 1

    def toggle_from_tray(self) -> None:
        self.toggle_calls += 1


class _FakeAction:
    def __init__(self, text: str) -> None:
        self._text = text
        self._enabled = True
        self.triggered = SimpleNamespace(connect=lambda _callback: None)

    def text(self) -> str:
        return self._text

    def isSeparator(self) -> bool:
        return False

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled


class _FakeSeparator(_FakeAction):
    def __init__(self) -> None:
        super().__init__("")

    def isSeparator(self) -> bool:
        return True


class _FakeMenu:
    def __init__(self, text: str = "", *, visible: bool = False) -> None:
        self._text = text
        self.items: list[_FakeAction | _FakeMenu] = []
        self._visible = visible

    def text(self) -> str:
        return self._text

    def isSeparator(self) -> bool:
        return False

    def isVisible(self) -> bool:
        return self._visible

    def clear(self) -> None:
        self.items.clear()

    def addAction(self, action: _FakeAction | str) -> _FakeAction:
        if isinstance(action, str):
            action = _FakeAction(action)
        self.items.append(action)
        return action

    def addMenu(self, text: str) -> "_FakeMenu":
        menu = _FakeMenu(text)
        self.items.append(menu)
        return menu

    def addSeparator(self) -> _FakeSeparator:
        separator = _FakeSeparator()
        self.items.append(separator)
        return separator

    def actions(self) -> list[_FakeAction | "_FakeMenu"]:
        return self.items


class TrayTests(unittest.TestCase):
    def setUp(self) -> None:
        tray._dolphin_tab_target_cache = None
        tray._wizard_pending_remote_names.clear()
        patcher = mock.patch.object(tray, "get_platform", return_value=LinuxPlatformServices())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tray_opens_setup_wizard_when_environment_is_not_ready(self):
        readiness = SimpleNamespace(ready=False)
        qt = SimpleNamespace()
        with mock.patch.object(tray.setup_wizard, "check_readiness", return_value=readiness):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                with mock.patch.object(tray, "_load_qt_bindings", return_value=qt):
                    with mock.patch.object(tray, "_run_prerequisite_wizard", return_value=False) as wizard:
                        self.assertEqual(tray.main([]), 1)

        wizard.assert_called_once_with(qt)

    def test_tray_rechecks_environment_after_setup_wizard(self):
        qt = SimpleNamespace()
        readiness = [SimpleNamespace(ready=False), SimpleNamespace(ready=False)]
        with mock.patch.object(tray.setup_wizard, "check_readiness", side_effect=readiness) as check:
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                with mock.patch.object(tray, "_load_qt_bindings", return_value=qt):
                    with mock.patch.object(tray, "_run_prerequisite_wizard", return_value=True):
                        self.assertEqual(tray.main([]), 1)

        self.assertEqual(check.call_count, 2)

    def test_tray_reports_missing_pyside_dependency(self):
        with mock.patch.object(tray.setup_wizard, "check_readiness", return_value=SimpleNamespace(ready=True)):
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()) as output:
                        self.assertEqual(tray.main([]), 1)

        self.assertIn("missing PySide6", output.getvalue())

    def test_tray_can_skip_readiness_check(self):
        with mock.patch.object(tray.setup_wizard, "check_readiness") as readiness:
            with mock.patch.object(tray, "_desktop_session_available", return_value=(True, "")):
                missing_dependency = tray.TrayDependencyError("missing PySide6")
                with mock.patch.object(tray, "_load_qt_bindings", side_effect=missing_dependency):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(tray.main(["--skip-readiness-check"]), 1)

        readiness.assert_not_called()

    def test_tray_reports_missing_desktop_session_before_qt_import(self):
        with mock.patch.object(tray.setup_wizard, "check_readiness", return_value=SimpleNamespace(ready=True)):
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

    def test_drive_usage_note_is_limited_to_google_drive_backend(self):
        drive = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        s3 = core.RemoteInfo("Docs__S3", "Docs", "Google Drive", "s3", "/tmp/docs")

        self.assertTrue(tray._is_google_drive_remote(drive))
        self.assertFalse(tray._is_google_drive_remote(s3))

    def test_popup_position_clamps_full_window_to_available_screen(self):
        position = tray._popup_position(
            890,
            640,
            (100, 50, 800, 600),
            (400, 300),
        )

        self.assertEqual(position, (500, 332))

    def test_provider_status_colors_follow_light_system_palette(self):
        foreground = SimpleNamespace(name=lambda: "#202020")
        background = SimpleNamespace(red=lambda: 245, green=lambda: 245, blue=lambda: 245)
        palette = mock.Mock()
        palette.color.side_effect = lambda role: foreground if role == "foreground" else background
        widget = mock.Mock()
        widget.palette.return_value = palette
        widget.foregroundRole.return_value = "foreground"
        widget.backgroundRole.return_value = "background"

        self.assertEqual(tray._provider_status_color("tested", widget), "#202020")
        self.assertEqual(tray._provider_status_color("untested", widget), "#92400e")

    def test_platform_without_driver_config_hides_config_action(self):
        platform = mock.Mock()
        platform.mount_driver_config_paths.return_value = ()

        with mock.patch.object(tray, "get_platform", return_value=platform):
            available = tray._has_mount_driver_config()

        self.assertFalse(available)

    def test_remote_browser_tooltip_names_service_not_remote(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        self.assertEqual(tray._remote_browser_tooltip(remote), "Open Drive in browser")

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

    def test_license_requirement_is_opt_in_for_paid_builds(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(tray._license_required())
        with mock.patch.dict("os.environ", {tray.LICENSE_REQUIRE_ENV: "1"}, clear=True):
            self.assertTrue(tray._license_required())

    def test_license_lock_allows_trial_without_paid_build_flag(self):
        status = tray.license_control.LicenseStatus("trial", "Trial: 1 day remaining")
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(tray.license_control, "current_status", return_value=status):
                self.assertFalse(tray._license_locked())

    def test_license_lock_blocks_expired_trial_without_paid_build_flag(self):
        status = tray.license_control.LicenseStatus("expired", "Trial expired")
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(tray.license_control, "current_status", return_value=status):
                self.assertTrue(tray._license_locked())

    def test_license_lock_blocks_required_unlicensed_build(self):
        status = tray.license_control.LicenseStatus("missing", "No license")
        with mock.patch.dict("os.environ", {tray.LICENSE_REQUIRE_ENV: "1"}, clear=True):
            with mock.patch.object(tray.license_control, "current_status", return_value=status):
                self.assertTrue(tray._license_locked())

    def test_effective_window_mode_forces_single_on_wayland(self):
        config = settings.AppSettings(window_mode=settings.WINDOW_MODE_MULTIPLE)

        self.assertEqual(tray._effective_window_mode(config, is_wayland=True), settings.WINDOW_MODE_SINGLE)

    def test_effective_window_mode_uses_setting_off_wayland(self):
        config = settings.AppSettings(window_mode=settings.WINDOW_MODE_SINGLE)

        self.assertEqual(tray._effective_window_mode(config, is_wayland=False), settings.WINDOW_MODE_SINGLE)

    def test_single_window_managed_size_detects_full_height_docking(self):
        class Rect:
            def __init__(self, width: int, height: int) -> None:
                self._width = width
                self._height = height

            def width(self) -> int:
                return self._width

            def height(self) -> int:
                return self._height

        window = object.__new__(tray.MountletWindow)
        window.file_browser = SimpleNamespace(_embedded=True)
        window.qt = SimpleNamespace(Qt=SimpleNamespace(WindowState=SimpleNamespace()))
        window.window = SimpleNamespace(
            isMaximized=lambda: False,
            isFullScreen=lambda: False,
            windowState=lambda: 0,
            frameGeometry=lambda: Rect(760, 800),
        )
        screen = SimpleNamespace(availableGeometry=lambda: Rect(1200, 804))

        self.assertTrue(window._single_window_size_managed(screen))

    def test_single_window_managed_size_detects_quarter_tiling(self):
        class Rect:
            def __init__(self, width: int, height: int) -> None:
                self._width = width
                self._height = height

            def width(self) -> int:
                return self._width

            def height(self) -> int:
                return self._height

        window = object.__new__(tray.MountletWindow)
        window.file_browser = SimpleNamespace(_embedded=True)
        window.qt = SimpleNamespace(Qt=SimpleNamespace(WindowState=SimpleNamespace()))
        window.window = SimpleNamespace(
            isMaximized=lambda: False,
            isFullScreen=lambda: False,
            windowState=lambda: 0,
            frameGeometry=lambda: Rect(600, 402),
        )
        screen = SimpleNamespace(availableGeometry=lambda: Rect(1200, 804))

        self.assertTrue(window._single_window_size_managed(screen))

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

        rebuild.assert_not_called()
        fake_qt.QTimer.singleShot.assert_called_once_with(25, rebuild)
        self.assertEqual(fake_window.toggle_calls, 1)

        fake_qt.QTimer.singleShot.reset_mock()
        with mock.patch.object(tray_app, "rebuild_menus") as rebuild:
            tray_app._handle_activation(fake_qt.QSystemTrayIcon.ActivationReason.DoubleClick)

        fake_qt.QTimer.singleShot.assert_called_once_with(25, rebuild)
        self.assertEqual(fake_window.toggle_calls, 2)

    def test_gnome_wayland_detection(self):
        environment = {
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_CURRENT_DESKTOP": "GNOME:GNOME-Classic",
        }
        with mock.patch.object(tray.get_platform(), "system_name", "Linux"):
            with mock.patch.dict(tray.os.environ, environment, clear=True):
                self.assertTrue(tray._is_gnome_wayland())

    def test_macos_tray_handles_left_and_right_click_separately(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._is_macos = True
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()
        tray_app.app_menu = mock.Mock()
        tray_app.rebuild_menus = mock.Mock()
        trigger = object()
        context = object()
        tray_app.qt = SimpleNamespace(
            QSystemTrayIcon=SimpleNamespace(
                ActivationReason=SimpleNamespace(Trigger=trigger, Context=context)
            ),
            QCursor=SimpleNamespace(pos=lambda: "cursor-position"),
            QTimer=mock.Mock(),
        )

        tray_app._handle_activation(trigger)
        tray_app._handle_activation(context)

        tray_app.main_window.toggle_from_tray.assert_called_once_with()
        tray_app.app_menu.popup.assert_called_once_with("cursor-position")
        tray_app.qt.QTimer.singleShot.assert_called_once_with(25, tray_app.rebuild_menus)

    def test_tray_context_menu_keeps_short_top_level_with_cascades(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.remote_menu = _FakeMenu("remote")
        tray_app.app_menu = _FakeMenu("app")
        tray_app.tray = mock.Mock()
        tray_app.qt = SimpleNamespace(QAction=lambda label, _menu: _FakeAction(label))
        main_window = mock.Mock()
        main_window.is_visible.return_value = False
        main_window._add_open_config_files_menu.side_effect = lambda menu: menu.addMenu("Open config file")
        tray_app.main_window = main_window

        with mock.patch.object(tray, "_license_locked", return_value=False):
            with mock.patch.object(tray, "_load_visible_remotes", return_value=[]):
                tray_app.rebuild_menus()

        top_level = [item.text() for item in tray_app.app_menu.items if not item.isSeparator()]
        self.assertEqual(top_level, ["Open Mountlet", "More", "Quit"])
        more_menu = next(item for item in tray_app.app_menu.items if item.text() == "More")
        more_items = [item.text() for item in more_menu.items if not item.isSeparator()]
        self.assertEqual(more_items, ["App", "Mount", "Config"])

    def test_rebuild_menus_does_not_clear_visible_context_menu(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.remote_menu = _FakeMenu("remote")
        tray_app.app_menu = _FakeMenu("app", visible=True)
        existing = tray_app.app_menu.addAction("Existing")
        tray_app.tray = mock.Mock()
        tray_app.main_window = mock.Mock()

        tray_app.rebuild_menus()

        self.assertEqual(tray_app.app_menu.items, [existing])
        tray_app.tray.setToolTip.assert_not_called()

    def test_rebuild_menus_does_not_clear_context_menu_open_flag(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app._context_menu_open = True
        tray_app.remote_menu = _FakeMenu("remote")
        tray_app.app_menu = _FakeMenu("app")
        existing = tray_app.app_menu.addAction("Existing")
        tray_app.tray = mock.Mock()
        tray_app.main_window = mock.Mock()

        tray_app.rebuild_menus()

        self.assertEqual(tray_app.app_menu.items, [existing])
        tray_app.tray.setToolTip.assert_not_called()

    def test_macos_accessory_mode_hides_dock_application(self):
        application = mock.Mock()
        application.setActivationPolicy_.return_value = True
        appkit = SimpleNamespace(
            NSApplication=SimpleNamespace(sharedApplication=lambda: application),
            NSApplicationActivationPolicyAccessory="accessory",
        )

        with mock.patch.dict(sys.modules, {"AppKit": appkit}):
            enabled = tray._set_macos_accessory_mode()

        self.assertTrue(enabled)
        application.setActivationPolicy_.assert_called_once_with("accessory")

    def test_macos_main_window_uses_normal_window_type(self):
        self.assertEqual(tray._main_window_type_name(True), "Window")
        self.assertEqual(tray._main_window_type_name(False), "Tool")
        self.assertEqual(tray._main_window_type_name(False, False, True), "Window")

    def test_wayland_main_window_uses_normal_window_type(self):
        self.assertEqual(tray._main_window_type_name(False, True), "Window")
        self.assertTrue(tray._main_window_uses_native_frame(True))
        self.assertFalse(tray._main_window_uses_native_frame(False))
        self.assertTrue(tray._main_window_uses_native_frame(False, True))

    def test_mountlet_window_save_remote_order_preserves_existing_settings(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        original = {
            "Docs": settings.MountSettings(
                mount_path="docs",
                remote_path="bucket/docs",
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
        self.assertEqual(saved["Docs"].remote_path, "bucket/docs")
        self.assertEqual(saved["Docs"].mount_flags, ["--read-only"])
        self.assertTrue(saved["Docs"].auto_mount)
        self.assertFalse(saved["Photos"].enabled)

    def test_mount_config_rename_unmounts_mounted_remote_and_marks_for_remount(self):
        dialog = object.__new__(tray.MountConfigDialog)
        dialog.remote = core.RemoteInfo("Old__Drive", "Old", "Drive", "drive", "/mnt/old")
        dialog.dialog = mock.Mock()
        dialog.fields = {
            "remote_alias": mock.Mock(text=mock.Mock(return_value="New")),
            "mount_path": mock.Mock(text=mock.Mock(return_value="")),
            "remote_path": mock.Mock(text=mock.Mock(return_value="")),
            "auto_mount": mock.Mock(isChecked=mock.Mock(return_value=True)),
        }
        dialog.flag_fields = []
        dialog._preserved_mount_flags = []
        dialog._saved_enabled = True
        dialog._saved_order = 4
        dialog.rclone_fields = {}
        dialog.renamed_from = ""
        dialog.renamed_to = "Old__Drive"
        dialog.remount_after_rename = False
        dialog.qt = SimpleNamespace(
            QMessageBox=SimpleNamespace(
                StandardButton=SimpleNamespace(Yes=1, No=2),
                question=mock.Mock(return_value=1),
                warning=mock.Mock(),
            )
        )

        with mock.patch.object(tray.core, "is_mounted", return_value=True) as is_mounted:
            with mock.patch.object(tray.core, "unmount_remote", return_value=(True, "unmounted")) as unmount:
                with mock.patch.object(tray.core, "rename_rclone_remote_alias", return_value="New__Drive") as rename:
                    with mock.patch.object(
                        tray,
                        "load_mount_settings",
                        return_value={"Old__Drive": settings.MountSettings(order=4)},
                    ):
                        with mock.patch.object(tray, "save_mount_settings") as save:
                            dialog._save()

        is_mounted.assert_called_once_with(dialog.remote)
        unmount.assert_called_once_with(dialog.remote)
        rename.assert_called_once_with("Old__Drive", "New")
        saved = save.call_args.args[0]
        self.assertNotIn("Old__Drive", saved)
        self.assertIn("New__Drive", saved)
        self.assertEqual(saved["New__Drive"].order, 4)
        self.assertEqual(dialog.renamed_from, "Old__Drive")
        self.assertEqual(dialog.renamed_to, "New__Drive")
        self.assertTrue(dialog.remount_after_rename)
        dialog.dialog.accept.assert_called_once_with()

    def test_mount_config_delete_unmounts_mounted_remote_after_single_confirmation(self):
        dialog = object.__new__(tray.MountConfigDialog)
        dialog.remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/mnt/docs")
        dialog.dialog = mock.Mock()
        dialog.deleted = False
        dialog.qt = SimpleNamespace(
            QMessageBox=SimpleNamespace(
                StandardButton=SimpleNamespace(Yes=1, No=2),
                question=mock.Mock(return_value=1),
                warning=mock.Mock(),
            )
        )

        with mock.patch.object(tray.core, "is_mounted", return_value=True):
            with mock.patch.object(tray.core, "unmount_remote", return_value=(True, "unmounted")) as unmount:
                with mock.patch.object(tray.core, "delete_rclone_remote", return_value=True) as delete:
                    with mock.patch.object(
                        tray,
                        "load_mount_settings",
                        return_value={"Docs__Drive": settings.MountSettings()},
                    ):
                        with mock.patch.object(tray, "save_mount_settings") as save:
                            dialog._delete_remote()

        unmount.assert_called_once_with(dialog.remote)
        delete.assert_called_once_with("Docs__Drive")
        dialog.qt.QMessageBox.question.assert_called_once()
        self.assertNotIn("Docs__Drive", save.call_args.args[0])
        self.assertTrue(dialog.deleted)
        dialog.dialog.accept.assert_called_once_with()

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

    def test_new_remote_wizard_declines_advanced_config_prompt(self):
        wizard = object.__new__(tray.NewRemoteWizard)

        self.assertEqual(
            wizard._automatic_answer(tray.rclone_wizard.RcloneConfigStep("state", {"Name": "config_edit_advanced"})),
            "false",
        )

    def test_new_remote_wizard_acknowledges_warning_prompts(self):
        wizard = object.__new__(tray.NewRemoteWizard)

        self.assertEqual(
            wizard._automatic_answer(
                tray.rclone_wizard.RcloneConfigStep(
                    "state",
                    {"Name": "config_warning", "Type": "bool", "Help": "IMPORTANT: Google Photos API limits apply."},
                )
            ),
            "true",
        )

    def test_new_remote_wizard_uses_generic_oauth_args_for_non_drive(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "dropbox"
        wizard._drive_local_auth = False

        self.assertEqual(wizard._initial_config_args(), ["config_is_local", "false"])

    def test_new_remote_wizard_uses_s3_form_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "s3"
        wizard.fields = {
            "s3_provider": mock.Mock(currentData=mock.Mock(return_value="Minio")),
            "s3_access_key_id": mock.Mock(text=mock.Mock(return_value="minioadmin")),
            "s3_secret_access_key": mock.Mock(text=mock.Mock(return_value="miniosecret")),
            "s3_region": mock.Mock(text=mock.Mock(return_value="us-east-1")),
            "s3_endpoint": mock.Mock(text=mock.Mock(return_value="http://127.0.0.1:9000")),
            "s3_remote_path": mock.Mock(text=mock.Mock(return_value="")),
        }

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "provider",
                "Minio",
                "access_key_id",
                "minioadmin",
                "secret_access_key",
                "miniosecret",
                "region",
                "us-east-1",
                "endpoint",
                "http://127.0.0.1:9000",
            ],
        )

    def test_new_remote_wizard_requires_s3_endpoint_for_minio(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.fields = {
            "s3_provider": mock.Mock(currentData=mock.Mock(return_value="Minio")),
            "s3_access_key_id": mock.Mock(text=mock.Mock(return_value="minioadmin")),
            "s3_secret_access_key": mock.Mock(text=mock.Mock(return_value="miniosecret")),
            "s3_endpoint": mock.Mock(text=mock.Mock(return_value="")),
        }

        self.assertFalse(wizard._s3_fields_are_valid())

    def test_new_remote_wizard_adds_cloudflare_r2_safety_options(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "s3"
        wizard.fields = {
            "s3_provider": mock.Mock(currentData=mock.Mock(return_value="Cloudflare")),
            "s3_access_key_id": mock.Mock(text=mock.Mock(return_value="r2-key")),
            "s3_secret_access_key": mock.Mock(text=mock.Mock(return_value="r2-secret")),
            "s3_region": mock.Mock(text=mock.Mock(return_value="auto")),
            "s3_endpoint": mock.Mock(text=mock.Mock(return_value="https://account.r2.cloudflarestorage.com")),
            "s3_remote_path": mock.Mock(text=mock.Mock(return_value="bucket")),
        }

        args = wizard._initial_config_args()

        self.assertIn("Cloudflare", args)
        self.assertIn("no_check_bucket", args)
        self.assertIn("true", args)
        self.assertIn("acl", args)
        self.assertIn("private", args)

    def test_new_remote_wizard_applies_s3_provider_defaults(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        app_provider = mock.Mock()
        app_provider.currentData.return_value = "s3"
        provider = mock.Mock()
        provider.currentData.return_value = {
            "label": "Cloudflare R2",
            "provider": "Cloudflare",
            "config_name": "Cloudflare R2",
            "endpoint": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com",
            "region": "auto",
            "access_key": "R2 access key ID",
            "secret_key": "R2 secret access key",
            "bucket": "Bucket name or bucket/folder",
            "instructions": '<a href="https://developers.cloudflare.com/r2/api/tokens/">Cloudflare R2 token guide</a>',
        }
        endpoint = mock.Mock()
        endpoint.text.return_value = ""
        region = mock.Mock()
        region.text.return_value = ""
        wizard.fields = {
            "provider": app_provider,
            "s3_provider": provider,
            "s3_endpoint": endpoint,
            "s3_region": region,
            "s3_access_key_id": mock.Mock(),
            "s3_secret_access_key": mock.Mock(),
            "s3_remote_path": mock.Mock(),
            "s3_help": mock.Mock(),
        }

        with mock.patch.object(wizard, "_set_form_row_visible") as set_visible:
            wizard._apply_s3_provider_choice()

        endpoint.setText.assert_called_once_with("https://<ACCOUNT_ID>.r2.cloudflarestorage.com")
        region.setText.assert_called_once_with("auto")
        wizard.fields["s3_help"].setText.assert_called_once()
        set_visible.assert_any_call(endpoint, True)

    def test_new_remote_wizard_uses_koofr_backend_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "koofr"
        wizard.fields = {
            "koofr_user": mock.Mock(text=mock.Mock(return_value="eric@example.com")),
            "koofr_pass": mock.Mock(text=mock.Mock(return_value="app-password")),
        }

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "provider",
                "koofr",
                "user",
                "eric@example.com",
                "password",
                "app-password",
            ],
        )

    def test_new_remote_wizard_uses_protondrive_backend_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "protondrive"
        wizard.fields = {
            "proton_user": mock.Mock(text=mock.Mock(return_value="eric@example.com")),
            "proton_pass": mock.Mock(text=mock.Mock(return_value="account-password")),
            "proton_2fa": mock.Mock(text=mock.Mock(return_value="123456")),
            "proton_mailbox_pass": mock.Mock(text=mock.Mock(return_value="mailbox-password")),
        }

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "username",
                "eric@example.com",
                "password",
                "account-password",
                "enable_caching",
                "false",
                "2fa",
                "123456",
                "mailbox_password",
                "mailbox-password",
            ],
        )

    def test_new_remote_wizard_uses_icloud_backend_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "iclouddrive"
        wizard.fields = {
            "icloud_service": mock.Mock(currentData=mock.Mock(return_value="photos")),
            "icloud_user": mock.Mock(text=mock.Mock(return_value="eric@example.com")),
            "icloud_pass": mock.Mock(text=mock.Mock(return_value="apple-password")),
        }

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "service",
                "photos",
                "apple_id",
                "eric@example.com",
                "password",
                "apple-password",
            ],
        )

    def test_new_remote_wizard_requires_icloud_credentials(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.fields = {
            "icloud_user": mock.Mock(text=mock.Mock(return_value="eric@example.com")),
            "icloud_pass": mock.Mock(text=mock.Mock(return_value="")),
        }

        self.assertFalse(wizard._icloud_fields_are_valid())

        wizard.fields["icloud_pass"].text.return_value = "apple-password"

        self.assertTrue(wizard._icloud_fields_are_valid())

    def test_new_remote_wizard_uses_google_photos_backend_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "gphotos"
        wizard._drive_local_auth = True
        wizard._drive_client_id = "client-id"
        wizard._drive_client_secret = "client-secret"
        wizard.fields = {"gphotos_read_only": mock.Mock(isChecked=mock.Mock(return_value=True))}

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "client_id",
                "client-id",
                "client_secret",
                "client-secret",
                "read_only",
                "true",
                "config_edit_advanced",
                "false",
                "config_is_local",
                "true",
                "read_size",
                "true",
            ],
        )

    def test_new_remote_wizard_schedules_input_focus(self):
        timer = mock.Mock(side_effect=lambda _msec, callback: callback())
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.qt = SimpleNamespace(
            Qt=SimpleNamespace(FocusReason=SimpleNamespace(OtherFocusReason="other")),
            QTimer=SimpleNamespace(singleShot=timer),
        )
        field = mock.Mock()

        wizard._schedule_focus_widget(field)

        self.assertEqual(field.setFocus.call_args_list, [mock.call("other"), mock.call("other")])
        timer.assert_called_once()

    def test_new_remote_wizard_focuses_checked_answer_radio(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        checked = mock.Mock()
        fallback = mock.Mock()
        wizard._answer_kind = "radio"
        wizard._answer_group = mock.Mock(checkedButton=mock.Mock(return_value=checked))
        wizard._answer_field = fallback

        self.assertIs(wizard._answer_focus_widget(), checked)

    def test_new_remote_wizard_external_provider_opens_rclone_terminal(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard.status = mock.Mock()
        wizard.dialog = mock.Mock()
        wizard._completed = False

        with mock.patch.object(wizard, "_stop_port_timer") as stop_timer:
            with mock.patch.object(tray.rclone_wizard, "open_config_in_external_terminal", return_value="/tmp/rclone.conf"):
                wizard._open_external_rclone_config()

        stop_timer.assert_called_once_with()
        wizard.dialog.accept.assert_called_once_with()
        self.assertTrue(wizard._completed)
        self.assertIn("/tmp/rclone.conf", wizard.status.setText.call_args.args[0])

    def test_new_remote_wizard_saves_initial_s3_remote_path(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_name = "Archive__S3"
        wizard._initial_remote_path = "bucket/prefix"

        with mock.patch.object(tray, "load_mount_settings", return_value={}) as load_settings:
            with mock.patch.object(tray, "save_mount_settings") as save:
                wizard._save_initial_mount_settings()

        load_settings.assert_called_once_with()
        saved = save.call_args.args[0]["Archive__S3"]
        self.assertEqual(saved.remote_path, "bucket/prefix")

    def test_new_remote_wizard_uses_webdav_form_args(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_type = "webdav"
        wizard.fields = {
            "webdav_url": mock.Mock(text=mock.Mock(return_value="https://cloud.example.com/dav")),
            "webdav_vendor": mock.Mock(currentData=mock.Mock(return_value="nextcloud")),
            "webdav_user": mock.Mock(text=mock.Mock(return_value="eric")),
            "webdav_pass": mock.Mock(text=mock.Mock(return_value="secret")),
        }

        self.assertEqual(
            wizard._initial_config_args(),
            [
                "url",
                "https://cloud.example.com/dav",
                "vendor",
                "nextcloud",
                "user",
                "eric",
                "pass",
                "secret",
            ],
        )

    def test_new_remote_wizard_failed_mount_cleans_up_remote(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        wizard._remote_name = "Broken__WebDAV"
        wizard._remote_alias = "Broken"
        wizard._remote_type = "webdav"
        wizard.fields = {}

        with mock.patch.object(wizard, "_set_busy") as set_busy:
            with mock.patch.object(wizard, "_cleanup_incomplete_remote") as cleanup:
                with mock.patch.object(wizard, "_reset_after_failed_registration") as reset:
                    with mock.patch.object(wizard, "_warning") as warning:
                        with mock.patch.object(wizard, "_finish_success") as finish:
                            wizard._handle_mount_finished(False, "[!] not connected")

        set_busy.assert_called_once_with(False)
        cleanup.assert_called_once_with()
        reset.assert_called_once_with()
        warning.assert_called_once()
        finish.assert_not_called()

    def test_new_remote_wizard_allows_same_alias_across_providers(self):
        wizard = object.__new__(tray.NewRemoteWizard)
        s3_provider = mock.Mock()
        s3_provider.currentData.return_value = "Cloudflare"
        wizard.fields = {"s3_provider": s3_provider}
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
            core.RemoteInfo(
                name="Media__Wasabi",
                alias="Media",
                provider="Wasabi",
                backend_type="s3",
                mount_path="/tmp/wasabi-media",
            ),
        ]

        self.assertFalse(wizard._display_name_exists("Media", "box", remotes))
        self.assertTrue(wizard._display_name_exists("Media", "dropbox", remotes))
        self.assertFalse(wizard._display_name_exists("Media", "s3", remotes, provider_name="Cloudflare R2"))
        self.assertTrue(wizard._display_name_exists("Media", "s3", remotes, provider_name="Wasabi"))
        self.assertEqual(wizard._config_remote_name("Media", "box", remotes), "Media__Box")
        self.assertEqual(
            wizard._config_remote_name("Media", "s3", remotes, provider_name="Cloudflare R2"),
            "Media__Cloudflare R2",
        )
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

    def test_mountlet_window_browser_button_uses_provider_color(self):
        window = object.__new__(tray.MountletWindow)
        button = mock.Mock()
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        with mock.patch.object(tray, "_shortcut_hint", return_value=""):
            window._update_browser_button(button, remote)

        button.setEnabled.assert_called_once_with(True)
        button.setStyleSheet.assert_called_once_with(f"color: {tray.PROVIDER_COLORS['drive']};")
        button.setToolTip.assert_called_once_with("Open Drive in browser")

    def test_mountlet_window_remote_status_icon_symbols_match_states(self):
        class Label:
            def __init__(self) -> None:
                self.text = ""
                self.style = ""

            def setToolTip(self, _text: str) -> None:
                return

            def setPixmap(self, _pixmap: object) -> None:
                raise AssertionError("fallback path should not set a pixmap")

            def setText(self, text: str) -> None:
                self.text = text

            def setStyleSheet(self, style: str) -> None:
                self.style = style

        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(QCursor=SimpleNamespace(pos=mock.Mock(return_value=None)))
        window._connection_cache = {}
        window._show_immediate_tooltip = mock.Mock()
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        unreachable = Label()
        window._connection_cache[remote.name] = False
        window._apply_remote_status_icon(unreachable, remote, mounted=False, checking=False)

        reachable = Label()
        window._connection_cache[remote.name] = True
        window._apply_remote_status_icon(reachable, remote, mounted=False, checking=False)

        mounted = Label()
        window._apply_remote_status_icon(mounted, remote, mounted=True, checking=False)

        self.assertEqual(unreachable.text, "")
        self.assertEqual(reachable.text, "☁")
        self.assertEqual(mounted.text, "☁▲")

    def test_shortcut_hint_uses_first_assigned_shortcut(self):
        with mock.patch.object(tray, "shortcut_values", return_value=("Alt+B", "Ctrl+B")):
            self.assertEqual(tray._shortcut_hint("remote_open_browser"), "\nShortcut: Alt+B")

    def test_mountlet_window_remote_title_escapes_rich_text(self):
        window = object.__new__(tray.MountletWindow)
        remote = core.RemoteInfo("A < B__Box", "A < B", "Box", "box", "/tmp/box")

        title = window._display_remote_name(remote)

        self.assertIn("A &lt; B", title)
        self.assertNotIn("A < B", title)

    def test_mountlet_window_row_usage_loads_for_unmounted_remote(self):
        window = object.__new__(tray.MountletWindow)
        window._usage_cache = {}
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        usage = window._row_usage(remote, mounted=False)

        self.assertEqual(usage.text, "Checking...")

    def test_mountlet_window_row_usage_returns_cached_usage_when_unmounted(self):
        window = object.__new__(tray.MountletWindow)
        usage = core.StorageUsage("7.0/15.0 GB", used=7, total=15)
        window._usage_cache = {"Docs__Drive": usage}
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")

        self.assertIs(window._row_usage(remote, mounted=False), usage)

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
            "Beta": settings.MountSettings(order=1, mount_path="dropbox/Beta", remote_path="bucket/beta"),
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
        self.assertEqual(saved["Beta"].remote_path, "bucket/beta")
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

    def test_mountlet_window_toggle_hides_when_file_browser_has_focus(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        browser_window = mock.Mock()
        mountlet_window.file_browser = SimpleNamespace(window=browser_window)
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}
        mountlet_window.qt = SimpleNamespace(
            QApplication=SimpleNamespace(activeWindow=lambda: browser_window)
        )
        mountlet_window.desktop = SimpleNamespace(window_is_on_current_workspace=lambda _window: True)

        with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
            with mock.patch.object(mountlet_window, "show") as show:
                mountlet_window.toggle_from_tray()

        hide_stack.assert_called_once_with()
        show.assert_not_called()

    def test_mountlet_window_offline_reconcile_is_debounced(self):
        callbacks: list[tuple[int, object]] = []
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._offline_reconcile_scheduled = set()
        mountlet_window.qt = SimpleNamespace(
            QTimer=SimpleNamespace(singleShot=lambda delay, callback: callbacks.append((delay, callback)))
        )

        mountlet_window._schedule_offline_reconcile("Docs", delay_ms=500)
        mountlet_window._schedule_offline_reconcile("Docs", delay_ms=500)

        self.assertEqual(mountlet_window._offline_reconcile_scheduled, {"Docs"})
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(callbacks[0][0], 500)

    def test_mountlet_window_disables_remote_polling_for_frozen_linux_x11_by_default(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window._remote_change_poll_timer = None
        mountlet_window.qt = SimpleNamespace(QTimer=mock.Mock())

        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
                mountlet_window._setup_remote_change_polling()

        mountlet_window.qt.QTimer.assert_not_called()

    def test_mountlet_window_allows_remote_polling_override_for_frozen_linux_x11(self):
        timer = mock.Mock()
        timer_type = mock.Mock(return_value=timer)
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window._remote_change_poll_timer = None
        mountlet_window.window = mock.Mock()
        mountlet_window.qt = SimpleNamespace(QTimer=timer_type)

        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0", tray.REMOTE_CHANGE_POLLING_ENV: "1"}, clear=True):
                with mock.patch.object(tray, "load_app_settings", return_value=SimpleNamespace(remote_sync_interval_seconds=30)):
                    mountlet_window._setup_remote_change_polling()

        timer_type.assert_called_once_with(mountlet_window.window)
        timer.setInterval.assert_called_once_with(30_000)
        timer.timeout.connect.assert_called_once_with(mountlet_window._scan_remote_cache_changes)
        timer.start.assert_called_once_with()

    def test_mountlet_window_disables_background_storage_checks_for_frozen_linux_x11_by_default(self):
        remote = SimpleNamespace(name="Docs")
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._usage_cache = {}
        mountlet_window._connection_cache = {}

        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
                with mock.patch.object(tray.core, "is_mounted", return_value=False):
                    with mock.patch.object(mountlet_window, "_license_locked", return_value=False):
                        mountlet_window._schedule_storage_load(remote)

        self.assertEqual(mountlet_window._usage_cache["Docs"].text, "?")
        self.assertFalse(mountlet_window._connection_cache["Docs"])

    def test_frozen_linux_x11_disables_custom_keyboard_shortcuts_by_default(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
                self.assertFalse(tray._custom_keyboard_shortcuts_enabled())

    def test_frozen_linux_x11_allows_custom_keyboard_shortcut_override(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0", tray.CUSTOM_KEYBOARD_SHORTCUTS_ENV: "1"}, clear=True):
                self.assertTrue(tray._custom_keyboard_shortcuts_enabled())

    def test_main_key_handler_ignores_packaged_x11_when_shortcuts_disabled(self):
        event = mock.Mock()
        mountlet_window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray, "_custom_keyboard_shortcuts_enabled", return_value=False):
            self.assertFalse(mountlet_window._handle_main_key(event))

        event.accept.assert_not_called()

    def test_remote_row_key_handler_ignores_packaged_x11_when_shortcuts_disabled(self):
        event = mock.Mock()
        mountlet_window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray, "_custom_keyboard_shortcuts_enabled", return_value=False):
            mountlet_window._handle_remote_row_key(event, mock.Mock(), mock.Mock())

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_mountlet_window_disables_config_sync_metadata_check_for_frozen_linux_x11_by_default(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window._remote_sync_check_pending = False
        mountlet_window._remote_sync_metadata = {"config_hash": "old"}
        mountlet_window._update_config_sync_buttons = mock.Mock()
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)

        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
                mountlet_window._request_config_sync_metadata_check([])

        self.assertIsNone(mountlet_window._remote_sync_metadata)
        mountlet_window._update_config_sync_buttons.assert_called_once_with()

    def test_mountlet_window_local_cache_change_schedules_remote_reconcile(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(remote_name_for_offline_path=lambda _path: "Docs")
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        mountlet_window._handle_local_cache_file_changed("/tmp/cache/Docs/a.txt")

        mountlet_window._refresh_offline_file_watches.assert_called_once_with()
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._schedule_offline_reconcile.assert_called_once_with("Docs", delay_ms=500)

    def test_mountlet_window_local_cache_change_rearms_file_watch(self):
        watcher = mock.Mock()
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window._offline_file_watcher = watcher
        mountlet_window._offline_watched_paths = {"/tmp/cache/Docs/a.txt"}
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(remote_name_for_offline_path=lambda _path: "Docs")
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        mountlet_window._handle_local_cache_file_changed("/tmp/cache/Docs/a.txt")

        watcher.removePath.assert_called_once_with("/tmp/cache/Docs/a.txt")
        self.assertEqual(mountlet_window._offline_watched_paths, set())
        mountlet_window._refresh_offline_file_watches.assert_called_once_with()
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._schedule_offline_reconcile.assert_called_once_with("Docs", delay_ms=500)

    def test_mountlet_window_local_cache_directory_change_scans_cache(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(remote_name_for_offline_path=lambda _path: "Docs")
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._scan_local_cache_changes = mock.Mock()

        mountlet_window._handle_local_cache_directory_changed("/tmp/cache/Docs")

        mountlet_window._refresh_offline_file_watches.assert_called_once_with()
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._scan_local_cache_changes.assert_called_once_with()

    def test_mountlet_window_refresh_offline_file_watches_includes_parent_directories(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "Docs"
            report_dir = root / "Reports"
            report_dir.mkdir(parents=True)
            local = report_dir / "a.txt"
            local.write_text("cached", encoding="utf-8")
            watcher = mock.Mock()
            mountlet_window = object.__new__(tray.MountletWindow)
            mountlet_window._offline_file_watcher = watcher
            mountlet_window._offline_watched_paths = set()
            mountlet_window.file_browser = SimpleNamespace(
                backend=SimpleNamespace(
                    managed_file_paths=lambda: {"Docs": [local]},
                    offline_path=lambda _remote, _path: root,
                )
            )

            mountlet_window._refresh_offline_file_watches()

            added = set(watcher.addPaths.call_args.args[0])
            self.assertIn(str(root), added)
            self.assertIn(str(report_dir), added)
            self.assertIn(str(local), added)
            self.assertEqual(mountlet_window._offline_watched_paths, added)

    def test_mountlet_window_local_cache_scan_schedules_changed_unmounted_remotes(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._action_pending = set()
        mountlet_window._offline_reconcile_scheduled = set()
        mountlet_window._offline_reconcile_running = set()
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: ["Docs"])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        mountlet_window._scan_local_cache_changes()

        mountlet_window._refresh_offline_file_watches.assert_called_once_with()
        mountlet_window._schedule_offline_reconcile.assert_called_once_with("Docs")

    def test_mountlet_window_local_cache_scan_skips_running_reconcile(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._action_pending = set()
        mountlet_window._offline_reconcile_scheduled = set()
        mountlet_window._offline_reconcile_running = {"Docs"}
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: ["Docs"])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        mountlet_window._scan_local_cache_changes()

        self.assertEqual(mountlet_window._offline_reconcile_scheduled, {"Docs"})
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._schedule_offline_reconcile.assert_not_called()

    def test_mountlet_window_local_cache_scan_does_not_repaint_already_queued_running_reconcile(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._action_pending = set()
        mountlet_window._offline_reconcile_scheduled = {"Docs"}
        mountlet_window._offline_reconcile_running = {"Docs"}
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: ["Docs"])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        mountlet_window._scan_local_cache_changes()

        mountlet_window._refresh_file_browser_mount_state.assert_not_called()
        mountlet_window._schedule_offline_reconcile.assert_not_called()

    def test_mountlet_window_reconcile_ready_drops_stale_followup_when_clean(self):
        remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs")
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._offline_reconcile_running = {"Docs"}
        mountlet_window._offline_reconcile_scheduled = {"Docs"}
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: [])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            mountlet_window._handle_offline_reconcile_ready("Docs", [], None)

        self.assertEqual(mountlet_window._offline_reconcile_running, set())
        self.assertEqual(mountlet_window._offline_reconcile_scheduled, set())
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._schedule_offline_reconcile.assert_not_called()

    def test_mountlet_window_reconcile_ready_keeps_followup_when_still_dirty(self):
        remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs")
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window._offline_reconcile_running = {"Docs"}
        mountlet_window._offline_reconcile_scheduled = {"Docs"}
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: ["Docs"])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()
        mountlet_window._schedule_offline_reconcile = mock.Mock()

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            mountlet_window._handle_offline_reconcile_ready("Docs", [], None)

        self.assertEqual(mountlet_window._offline_reconcile_running, set())
        self.assertEqual(mountlet_window._offline_reconcile_scheduled, set())
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")
        mountlet_window._schedule_offline_reconcile.assert_called_once_with("Docs", delay_ms=500)

    def test_mountlet_window_cache_sync_debug_report_runs_reconcile_with_diagnostics(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/tmp/docs")
        diagnostic_lines = ["file: Reports/a.txt", "  upload: ok in 0.001s"]
        backend = SimpleNamespace(
            changed_managed_remote_names=mock.Mock(side_effect=[["Docs"], []]),
            changed_managed_files=mock.Mock(return_value=[]),
            _offline_records={"Docs": {"Reports/a.txt": {"is_dir": False, "local_size": 11}}},
            offline_changed=mock.Mock(return_value=False),
        )
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.file_browser = SimpleNamespace(backend=backend)
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()

        def reconcile(_remote, diagnostics=None):
            diagnostics.extend(diagnostic_lines)
            return []

        backend.changed_managed_files.side_effect = reconcile

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            report = mountlet_window._cache_sync_debug_report()

        self.assertIn("changed_remotes_before: ['Docs']", report)
        self.assertIn("upload: ok", report)
        self.assertIn("changed_after: False", report)
        mountlet_window._refresh_file_browser_mount_state.assert_not_called()

    def test_mountlet_window_cache_sync_debug_report_starts_worker(self):
        started: list[object] = []

        class Thread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                started.append(self)

        class Dialog:
            def setWindowTitle(self, _title):
                pass

            def reject(self):
                pass

            def resize(self, *_args):
                pass

        class Layout:
            def __init__(self, _dialog):
                pass

            def addWidget(self, _widget):
                pass

        class Buttons:
            StandardButton = SimpleNamespace(Close="close")

            def __init__(self, _buttons):
                self.rejected = SimpleNamespace(connect=lambda _callback: None)

        text = mock.Mock()
        label = mock.Mock()
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.qt = SimpleNamespace(
            QDialog=lambda _parent: Dialog(),
            QVBoxLayout=Layout,
            QLabel=lambda _text: label,
            QPlainTextEdit=lambda: text,
            QDialogButtonBox=Buttons,
        )
        mountlet_window.window = object()
        mountlet_window.tray_app = SimpleNamespace(_notify=mock.Mock())
        mountlet_window._open_child_dialog = mock.Mock()
        mountlet_window._bridge = SimpleNamespace(cache_sync_debug_ready=SimpleNamespace(emit=mock.Mock()))
        mountlet_window._cache_sync_debug_report = mock.Mock(return_value="report")

        with mock.patch.object(tray.threading, "Thread", Thread):
            mountlet_window._show_cache_sync_debug_report()

        self.assertTrue(mountlet_window._cache_sync_debug_running)
        text.setPlainText.assert_called_once_with("Running cache sync diagnostics…")
        self.assertEqual(len(started), 1)
        started[0].target()
        mountlet_window._bridge.cache_sync_debug_ready.emit.assert_called_once_with("report")

    def test_mountlet_window_cache_sync_debug_ready_populates_report(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window._cache_sync_debug_running = True
        mountlet_window._cache_sync_debug_text = mock.Mock()
        mountlet_window._cache_sync_debug_label = mock.Mock()
        mountlet_window.file_browser = SimpleNamespace(
            backend=SimpleNamespace(changed_managed_remote_names=lambda: ["Docs"])
        )
        mountlet_window._refresh_offline_file_watches = mock.Mock()
        mountlet_window._refresh_file_browser_mount_state = mock.Mock()

        mountlet_window._handle_cache_sync_debug_ready("report")

        self.assertFalse(mountlet_window._cache_sync_debug_running)
        mountlet_window._cache_sync_debug_text.setPlainText.assert_called_once_with("report")
        mountlet_window._refresh_file_browser_mount_state.assert_called_once_with("Docs")

    def test_mountlet_window_focus_return_scans_all_local_cache_changes(self):
        remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs")
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.file_browser = SimpleNamespace(remote=remote, is_visible=lambda: True, invalidate=mock.Mock())
        mountlet_window._scan_local_cache_changes = mock.Mock()

        mountlet_window._refresh_file_browser_after_focus_return()

        mountlet_window._scan_local_cache_changes.assert_called_once_with()
        mountlet_window.file_browser.invalidate.assert_called_once_with("Docs")

    def test_mountlet_window_toggle_hides_window_stack_when_child_is_open(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isActiveWindow.return_value = False
        child = mock.Mock()
        child.isVisible.return_value = True
        mountlet_window._child_dialogs = [child]

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=True):
            with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
                with mock.patch.object(mountlet_window, "show") as show:
                    mountlet_window.toggle_from_tray()

        hide_stack.assert_called_once_with()
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

    def test_mountlet_window_toggle_closes_after_deactivation_to_windows_tray(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window.window.isActiveWindow.return_value = False
        mountlet_window._deactivated_for_tray = True
        mountlet_window._child_dialogs = []

        with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
            with mock.patch.object(mountlet_window, "show") as show:
                mountlet_window.toggle_from_tray()

        hide_stack.assert_called_once_with()
        show.assert_not_called()
        self.assertFalse(mountlet_window._deactivated_for_tray)

    def test_windows_foreground_tray_detection_recognizes_overflow_window(self):
        def class_name(_window: int, buffer: object, _length: int) -> int:
            buffer.value = "TopLevelWindowForOverflowXamlIsland"
            return 1

        user32 = SimpleNamespace(GetForegroundWindow=lambda: 42, GetClassNameW=class_name)
        with mock.patch.object(tray.platform, "system", return_value="Windows"):
            with mock.patch.object(
                tray.ctypes,
                "windll",
                SimpleNamespace(user32=user32),
                create=True,
            ):
                self.assertTrue(tray._windows_foreground_is_tray())

    def test_mountlet_window_close_hides_window_stack(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        event = mock.Mock()

        with mock.patch.object(mountlet_window, "_tray_is_quitting", return_value=False):
            with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
                handled = mountlet_window._handle_window_close(event)

        self.assertTrue(handled)
        hide_stack.assert_called_once_with()
        event.ignore.assert_called_once_with()

    def test_mountlet_window_close_hides_window_stack_when_child_is_open(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        event = mock.Mock()

        with mock.patch.object(mountlet_window, "_tray_is_quitting", return_value=False):
            with mock.patch.object(mountlet_window, "_has_visible_child_dialog", return_value=True):
                with mock.patch.object(mountlet_window, "_raise_child_windows") as raise_child:
                    with mock.patch.object(mountlet_window, "_schedule_child_window_raises") as schedule:
                        with mock.patch.object(mountlet_window, "_hide_window_stack") as hide_stack:
                            handled = mountlet_window._handle_window_close(event)

        self.assertTrue(handled)
        raise_child.assert_not_called()
        schedule.assert_not_called()
        hide_stack.assert_called_once_with()
        event.ignore.assert_called_once_with()

    def test_mountlet_main_window_close_event_uses_shared_close_handler(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        window_type = SimpleNamespace(
            Tool=1,
            FramelessWindowHint=2,
        )
        qt = SimpleNamespace(
            Qt=SimpleNamespace(WindowType=window_type),
            QMainWindow=type("BaseMainWindow", (), {"closeEvent": lambda self, event: None}),
        )
        mountlet_window.qt = qt
        event = mock.Mock()

        with mock.patch.object(mountlet_window, "_handle_window_close", return_value=True) as handle:
            window = mountlet_window._make_main_window()
            window.closeEvent(event)

        handle.assert_called_once_with(event)

    def test_apply_frameless_window_flags_uses_frameless_hint(self):
        window_type = SimpleNamespace(
            Dialog=1,
            FramelessWindowHint=2,
        )
        qt = SimpleNamespace(Qt=SimpleNamespace(WindowType=window_type))
        window = mock.Mock()

        tray._apply_frameless_window_flags(qt, window, base_name="Dialog")

        window.setWindowFlags.assert_called_once_with(3)

    def test_toggle_keep_above_uses_x11_state_without_remapping_window(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.qt = SimpleNamespace(
            Qt=SimpleNamespace(WindowType=SimpleNamespace(WindowStaysOnTopHint="top"))
        )
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window._keep_above = False
        mountlet_window._keep_above_button = mock.Mock()

        with mock.patch.object(tray, "_set_x11_keep_above", return_value=True) as set_above:
            mountlet_window._toggle_keep_above(True)

        self.assertTrue(mountlet_window._keep_above)
        set_above.assert_called_once_with(mountlet_window.window, True)
        mountlet_window.window.setWindowFlag.assert_not_called()
        mountlet_window.window.move.assert_not_called()
        mountlet_window.window.show.assert_not_called()
        mountlet_window._keep_above_button.setChecked.assert_called_once_with(True)
        mountlet_window._keep_above_button.setToolTip.assert_called_once_with(
            "Stop keeping Mountlet above other windows"
        )
        mountlet_window._keep_above_button.setStyleSheet.assert_called_once()

    def test_macos_pin_applies_native_always_on_top_flag(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                WindowType=SimpleNamespace(WindowStaysOnTopHint="top"),
            )
        )
        mountlet_window.tray_app = SimpleNamespace(_is_macos=True)
        mountlet_window.desktop = mock.Mock()
        mountlet_window.desktop.set_keep_above.return_value = False
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = True
        mountlet_window._keep_above = True

        mountlet_window._apply_keep_above()

        mountlet_window.window.setWindowFlag.assert_called_once_with("top", True)
        mountlet_window.window.show.assert_called_once_with()

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
        single_shot = mock.Mock()
        qt = SimpleNamespace(
            QApplication=SimpleNamespace(
                activeModalWidget=lambda: None,
                activeWindow=lambda: None,
            ),
            QTimer=SimpleNamespace(singleShot=single_shot),
        )
        mountlet_window.qt = qt

        with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=False):
            with mock.patch.object(mountlet_window, "refresh") as refresh:
                with mock.patch.object(mountlet_window, "_position_near_tray") as position:
                    mountlet_window.show()

        mountlet_window.window.hide.assert_called_once_with()
        refresh.assert_called_once_with()
        position.assert_called_once_with()
        mountlet_window.window.show.assert_called_once_with()
        mountlet_window.window.raise_.assert_not_called()
        mountlet_window.window.activateWindow.assert_not_called()
        self.assertEqual(single_shot.call_count, 9)

    def test_activate_main_window_skips_when_window_still_on_other_desktop(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()

        with mock.patch.object(tray, "_move_x11_window_to_current_desktop") as move:
            with mock.patch.object(tray, "_x11_qt_window_is_on_current_desktop", return_value=False):
                mountlet_window._activate_main_window_if_current_desktop()

        move.assert_called_once_with(mountlet_window.window)
        mountlet_window.window.raise_.assert_not_called()
        mountlet_window.window.activateWindow.assert_not_called()

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
                with mock.patch.object(tray, "_send_x11_window_desktop_request", return_value=False):
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

    def test_send_x11_window_desktop_request_uses_ewmh_client_message(self):
        x11 = SimpleNamespace(
            XOpenDisplay=mock.Mock(return_value=123),
            XDefaultRootWindow=mock.Mock(return_value=456),
            XInternAtom=mock.Mock(return_value=789),
            XSendEvent=mock.Mock(return_value=1),
            XFlush=mock.Mock(),
            XCloseDisplay=mock.Mock(),
        )

        with mock.patch.object(tray.ctypes.util, "find_library", return_value="libX11.so"):
            with mock.patch.object(tray.ctypes, "CDLL", return_value=x11):
                self.assertTrue(tray._send_x11_window_desktop_request(12345, 3))

        event = x11.XSendEvent.call_args.args[4]._obj
        self.assertEqual(event.xclient.window, 12345)
        self.assertEqual(event.xclient.message_type, 789)
        self.assertEqual(event.xclient.format, 32)
        self.assertEqual(event.xclient.data.l[0], 3)
        self.assertEqual(event.xclient.data.l[1], 2)
        x11.XFlush.assert_called_once_with(123)
        x11.XCloseDisplay.assert_called_once_with(123)

    def test_send_x11_keep_above_request_uses_net_wm_state_above(self):
        with mock.patch.object(tray, "_send_x11_client_message", return_value=True) as send:
            self.assertTrue(tray._send_x11_keep_above_request(12345, True))

        send.assert_called_once_with(
            12345,
            b"_NET_WM_STATE",
            [1, 0, 0, 2],
            atom_data={1: b"_NET_WM_STATE_ABOVE"},
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

    def test_focus_window_restores_x11_keep_above_after_desktop_move(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isMinimized.return_value = False
        mountlet_window._keep_above = True

        with mock.patch.object(tray, "_move_x11_window_to_current_desktop"):
            with mock.patch.object(tray, "_set_x11_keep_above", return_value=True) as set_above:
                mountlet_window._focus_window()

        set_above.assert_called_once_with(mountlet_window.window, True)

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

    def test_hide_window_stack_closes_active_untracked_child_dialog(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        child = mock.Mock()
        child.parentWidget.return_value = mountlet_window.window
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}
        qt = mock.Mock()
        qt.QApplication.activeModalWidget.return_value = child
        qt.QApplication.activeWindow.return_value = None
        mountlet_window.qt = qt

        mountlet_window._hide_window_stack()

        child.reject.assert_called_once_with()
        mountlet_window.window.hide.assert_called_once_with()

    def test_open_child_dialog_tracks_owner_and_uses_window_modal_show(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}
        mountlet_window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                WindowModality=SimpleNamespace(WindowModal="window-modal"),
                WindowType=SimpleNamespace(
                    Dialog=1,
                    FramelessWindowHint=2,
                ),
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
        owner.dialog.setWindowFlags.assert_not_called()
        owner.dialog.setModal.assert_called_once_with(True)
        owner.dialog.setWindowModality.assert_called_once_with("window-modal")
        owner.dialog.show.assert_called_once_with()
        raise_child.assert_called_once_with()

    def test_open_child_dialog_shows_main_window_before_hidden_parent_dialog(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        mountlet_window.window.isVisible.return_value = False
        mountlet_window._child_dialogs = []
        mountlet_window._child_dialog_owners = {}
        mountlet_window.tray_app = SimpleNamespace(_quitting=False)
        mountlet_window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                WindowModality=SimpleNamespace(WindowModal="window-modal"),
                WindowType=SimpleNamespace(Dialog=1, FramelessWindowHint=2),
            ),
            QTimer=mock.Mock(),
        )
        owner = SimpleNamespace(dialog=mock.Mock())
        events: list[str] = []

        with mock.patch.object(mountlet_window, "show", side_effect=lambda: events.append("main")):
            with mock.patch.object(
                mountlet_window,
                "_show_tracked_child_dialog",
                side_effect=lambda _dialog: events.append("child"),
            ):
                mountlet_window.qt.QTimer.singleShot.side_effect = lambda _delay, callback: (
                    events.append("timer"),
                    callback(),
                )
                mountlet_window._open_child_dialog(owner)

        self.assertEqual(events, ["main", "timer", "child"])
        owner.dialog.show.assert_not_called()

    def test_restore_child_offsets_moves_subwindow_with_main_window(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        mountlet_window.window = mock.Mock()
        child = mock.Mock()
        mountlet_window._child_dialogs = [child]

        with mock.patch.object(mountlet_window, "_window_position", side_effect=[(300, 400)]):
            mountlet_window._restore_child_offsets({child: (25, 35)})

        child.move.assert_called_once_with(325, 435)

    def test_completed_content_resize_does_not_requery_moving_tray_geometry(self):
        mountlet_window = object.__new__(tray.MountletWindow)
        root = mock.Mock()
        scroll = mock.Mock()
        container = mock.Mock()
        mountlet_window.window = mock.Mock()
        mountlet_window.window.centralWidget.return_value = root
        tray_geometry = mock.Mock()
        tray_geometry.isValid.return_value = True
        mountlet_window.tray_app = SimpleNamespace(
            _quitting=False,
            _is_gnome_wayland=False,
            tray=SimpleNamespace(geometry=lambda: tray_geometry),
        )

        with mock.patch.object(mountlet_window, "_fit_to_content") as fit:
            with mock.patch.object(mountlet_window, "is_visible", return_value=True):
                with mock.patch.object(mountlet_window, "_position_near_tray") as position:
                    mountlet_window._finish_content_fit(root, scroll, container)

        fit.assert_called_once_with(root, scroll, container)
        position.assert_not_called()

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
        self.assertIn(f"array:string:{DOCS_URI}", command)
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
                DOCS_URI,
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
                    DOCS_URI,
                )
            )

        self.assertEqual(
            interface.call.call_args_list,
            [
                mock.call("openDirectories", [DOCS_URI], False),
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
                    DOCS_URI,
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
                    DOCS_URI,
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
                    DOCS_URI,
                )
            )

        run.assert_called_once_with(
            [
                "/usr/bin/qdbus6",
                "org.kde.dolphin-1234",
                "/dolphin/Dolphin_1",
                "org.kde.dolphin.MainWindow.openDirectories",
                DOCS_URI,
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

    def test_open_folder_explicit_manager_overrides_desktop_service(self):
        qt = mock.Mock()
        manager = SimpleNamespace(identifier="org.example.Files.desktop")
        settings = SimpleNamespace(
            file_manager=manager.identifier,
            open_folder_behavior="file-manager-service",
            focus_file_manager=True,
        )
        with mock.patch.object(tray, "load_app_settings", return_value=settings):
            with mock.patch.object(tray, "resolve_file_manager", return_value=manager):
                with mock.patch.object(tray, "_show_folder_with_file_manager") as service:
                    with mock.patch.object(tray, "open_with_file_manager", return_value=True) as opener:
                        self.assertTrue(tray._open_folder_default(qt, "/tmp/docs"))

        service.assert_not_called()
        opener.assert_called_once_with(manager, "/tmp/docs", new_window=False)

    def test_file_manager_action_label_uses_tool_name_for_system_default(self):
        settings = SimpleNamespace(file_manager="system")
        manager = SimpleNamespace(label="System default (Dolphin)")
        tray._file_manager_label_cache = None
        try:
            with mock.patch.object(tray, "load_app_settings", return_value=settings):
                with mock.patch.object(tray, "resolve_file_manager", return_value=manager):
                    self.assertEqual(tray._file_manager_label(), "Dolphin")
        finally:
            tray._file_manager_label_cache = None

    def test_open_folder_uses_explorer_command_on_windows(self):
        qt = mock.Mock()
        manager = SimpleNamespace(identifier="explorer")
        settings = SimpleNamespace(
            file_manager=manager.identifier,
            open_folder_behavior="current_desktop",
            focus_file_manager=True,
        )
        with mock.patch.object(tray, "load_app_settings", return_value=settings):
            with mock.patch.object(tray, "resolve_file_manager", return_value=manager):
                with mock.patch.object(tray.platform, "system", return_value="Windows"):
                    with mock.patch.object(tray, "open_with_file_manager", return_value=True) as opener:
                        self.assertTrue(tray._open_folder_default(qt, r"C:\Mountlet\Docs"))

        opener.assert_called_once_with(manager, r"C:\Mountlet\Docs", new_window=False)
        qt.QDesktopServices.openUrl.assert_not_called()

    def test_open_text_file_focused_opens_known_editor(self):
        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.shutil, "which", side_effect=lambda name: "/usr/bin/kate" if name == "kate" else None):
                with mock.patch.object(tray.subprocess, "Popen") as popen:
                    self.assertTrue(tray._open_text_file_focused(Path("/tmp/config.toml")))

        self.assertEqual(popen.call_args.args[0], ["/usr/bin/kate", str(Path("/tmp/config.toml"))])

    def test_open_text_file_focused_falls_back_without_known_editor(self):
        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray.shutil, "which", return_value=None):
                with mock.patch.object(tray.subprocess, "Popen") as popen:
                    self.assertFalse(tray._open_text_file_focused(Path("/tmp/config.toml")))

        popen.assert_not_called()

    def test_open_folder_action_delegates_to_main_window_runner(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()

        tray_app._open_folder(remote)

        tray_app.main_window._open_folder.assert_called_once_with(remote)

    def test_window_folder_open_uses_mount_status_instead_of_directory_probe(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", r"C:\Mountlet\Docs")
        window = object.__new__(tray.MountletWindow)
        window.tray_app = mock.Mock()
        active_status = tray.license_control.LicenseStatus("trial", "Trial active")

        with mock.patch.object(tray.license_control, "current_status", return_value=active_status):
            with mock.patch.object(core, "is_mounted", return_value=False):
                with mock.patch.object(tray.os.path, "isdir", return_value=True) as isdir:
                    window._open_folder(remote)

        isdir.assert_not_called()
        window.tray_app._notify.assert_called_once_with(
            "Open folder",
            "Mount the remote before opening its folder.",
            success=False,
        )

    def test_window_folder_open_refuses_unreachable_mount_path(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", r"C:\Mountlet\Docs")
        window = object.__new__(tray.MountletWindow)
        window.tray_app = mock.Mock()
        window.desktop = mock.Mock()
        active_status = tray.license_control.LicenseStatus("trial", "Trial active")

        with mock.patch.object(tray.license_control, "current_status", return_value=active_status):
            with mock.patch.object(core, "is_mounted", return_value=True):
                with mock.patch.object(tray.platform, "system", return_value="Linux"):
                    with mock.patch.object(tray.Path, "is_dir", return_value=False):
                        window._open_folder(remote)

        window.desktop.open_folder.assert_not_called()
        window.tray_app._notify.assert_called_once_with(
            "Open folder",
            "The mount folder is not reachable. Remount this remote and try again.",
            success=False,
        )

    def test_windows_folder_open_skips_python_directory_probe_after_mount_check(self):
        class ImmediateThread:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self) -> None:
                self.target()

        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", r"C:\Mountlet\Docs")
        window = object.__new__(tray.MountletWindow)
        window.tray_app = mock.Mock()
        window.desktop = mock.Mock()
        window._bridge = SimpleNamespace(folder_opened=SimpleNamespace(emit=mock.Mock()))
        active_status = tray.license_control.LicenseStatus("trial", "Trial active")

        with mock.patch.object(tray.license_control, "current_status", return_value=active_status):
            with mock.patch.object(core, "is_mounted", return_value=True):
                with mock.patch.object(tray.platform, "system", return_value="Windows"):
                    with mock.patch.object(tray.Path, "is_dir", return_value=False) as is_dir:
                        with mock.patch.object(tray.threading, "Thread", ImmediateThread):
                            window.desktop.open_folder.return_value = True
                            window._open_folder(remote)

        is_dir.assert_not_called()
        window.desktop.open_folder.assert_called_once_with(r"C:\Mountlet\Docs")
        window._bridge.folder_opened.emit.assert_called_once_with(True)

    def test_remote_row_style_keeps_border_geometry_constant(self):
        class Row:
            def __init__(self, values: dict[str, object]) -> None:
                self.values = values

            def property(self, name: str) -> object:
                return self.values.get(name)

        window = object.__new__(tray.MountletWindow)
        normal = window._remote_row_style(Row({"mounted": True}), highlighted=False)
        selected = window._remote_row_style(
            Row({"mounted": True, "browserSelected": True}),
            highlighted=False,
        )
        focused = window._remote_row_style(
            Row({"mounted": True, "keyboardFocus": True}),
            highlighted=False,
        )

        self.assertIn("border: 2px solid", normal)
        self.assertIn("border: 2px solid", selected)
        self.assertIn("border: 2px solid", focused)
        self.assertIn("border-radius: 4px", normal)

    def test_focused_remote_name_prefers_hover_selected_remote(self):
        class Row:
            def __init__(self, focused: bool = False) -> None:
                self.focused = focused

            def hasFocus(self) -> bool:
                return self.focused

        window = object.__new__(tray.MountletWindow)
        window._current_remote_names = ["Alpha", "Beta", "Gamma"]
        window._selected_remote_name = "Beta"
        window._row_widgets = {
            "Alpha": SimpleNamespace(frame=Row(focused=True)),
            "Beta": SimpleNamespace(frame=Row()),
            "Gamma": SimpleNamespace(frame=Row()),
        }

        self.assertEqual(window._focused_remote_name(), "Beta")

    def test_keyboard_navigation_starts_from_hover_selected_remote(self):
        class Row:
            def __init__(self) -> None:
                self.properties: dict[str, object] = {}
                self.focused = False

            def property(self, name: str) -> object:
                return self.properties.get(name)

            def setProperty(self, name: str, value: object) -> None:
                self.properties[name] = value

            def setStyleSheet(self, _style: str) -> None:
                return

            def setFocus(self, _reason: object) -> None:
                self.focused = True

        rows = {name: Row() for name in ("Alpha", "Beta", "Gamma")}
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(Qt=SimpleNamespace(FocusReason=SimpleNamespace(ShortcutFocusReason="shortcut")))
        window._current_remote_names = ["Alpha", "Beta", "Gamma"]
        window._selected_remote_name = "Beta"
        window._row_widgets = {name: SimpleNamespace(frame=row) for name, row in rows.items()}

        window._focus_relative_remote(window._focused_remote_name(), 1)

        self.assertEqual(window._selected_remote_name, "Gamma")
        self.assertTrue(rows["Gamma"].focused)

    def test_keyboard_navigation_scrolls_selected_remote_into_view(self):
        class Row:
            def __init__(self) -> None:
                self.properties: dict[str, object] = {}

            def property(self, name: str) -> object:
                return self.properties.get(name)

            def setProperty(self, name: str, value: object) -> None:
                self.properties[name] = value

            def setStyleSheet(self, _style: str) -> None:
                return

            def setFocus(self, _reason: object) -> None:
                return

        rows = {name: Row() for name in ("Alpha", "Beta", "Gamma")}
        scroll = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(Qt=SimpleNamespace(FocusReason=SimpleNamespace(ShortcutFocusReason="shortcut")))
        window._current_remote_names = ["Alpha", "Beta", "Gamma"]
        window._selected_remote_name = "Beta"
        window._row_widgets = {name: SimpleNamespace(frame=row) for name, row in rows.items()}
        window._remote_scroll = scroll

        window._focus_relative_remote(window._focused_remote_name(), 1)

        scroll.ensureWidgetVisible.assert_called_once_with(rows["Gamma"], 0, 6)

    def test_remote_list_direction_key_enters_browser_only_toward_browser_side(self):
        class Event:
            def __init__(self, key: object) -> None:
                self._key = key
                self.accepted = False

            def key(self) -> object:
                return self._key

            def accept(self) -> None:
                self.accepted = True

        qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )
        window = object.__new__(tray.MountletWindow)
        window.qt = qt
        window.file_browser = SimpleNamespace(side=lambda: "right")

        with mock.patch.object(tray, "matches_shortcut", return_value=False):
            with mock.patch.object(window, "_focus_current_browser") as focus_browser:
                self.assertTrue(window._handle_main_key(Event("right")))
                focus_browser.assert_called_once_with()

            with mock.patch.object(window, "_focus_current_browser") as focus_browser:
                event = Event("left")
                self.assertTrue(window._handle_main_key(event))
                self.assertTrue(event.accepted)
                focus_browser.assert_not_called()

    def test_remote_list_left_key_enters_left_side_browser(self):
        class Event:
            def __init__(self, key: object) -> None:
                self._key = key

            def key(self) -> object:
                return self._key

            def accept(self) -> None:
                return

        qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )
        window = object.__new__(tray.MountletWindow)
        window.qt = qt
        window.file_browser = SimpleNamespace(side=lambda: "left")

        with mock.patch.object(tray, "matches_shortcut", return_value=False):
            with mock.patch.object(window, "_focus_current_browser") as focus_browser:
                self.assertTrue(window._handle_main_key(Event("left")))
                focus_browser.assert_called_once_with()

    def test_remote_list_alternative_navigation_shortcut_moves_selection(self):
        class Event:
            def key(self) -> object:
                return "custom"

            def accept(self) -> None:
                return

        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )

        def matches(_qt: object, _event: object, action: str) -> bool:
            return action == "common_next"

        with mock.patch.object(tray, "matches_shortcut", side_effect=matches):
            with mock.patch.object(window, "_focused_remote_name", return_value="Beta"):
                with mock.patch.object(window, "_focus_relative_remote") as focus:
                    self.assertTrue(window._handle_main_key(Event()))

        focus.assert_called_once_with("Beta", 1)

    def test_remote_row_toggle_shortcut_uses_mount_action(self):
        remote = SimpleNamespace(name="Docs", display_name="Docs")
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )

        class Event:
            def key(self) -> object:
                return "custom"

            def accept(self) -> None:
                return

        def matches(_qt: object, _event: object, action: str) -> bool:
            return action == "remote_toggle_mount"

        with mock.patch.object(tray, "matches_shortcut", side_effect=matches):
            with mock.patch.object(window, "_toggle_remote_mount") as toggle:
                window._handle_remote_row_key(Event(), remote, object())

        toggle.assert_called_once_with(remote)

    def test_main_remote_config_shortcut_opens_focused_remote(self):
        remote = SimpleNamespace(name="Docs", display_name="Docs")
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )

        class Event:
            def key(self) -> object:
                return "custom"

            def accept(self) -> None:
                return

        def matches(_qt: object, _event: object, action: str) -> bool:
            return action == "remote_config"

        with mock.patch.object(tray, "matches_shortcut", side_effect=matches):
            with mock.patch.object(window, "_focused_remote_name", return_value="Docs"):
                with mock.patch.object(window, "_remote_by_name", return_value=remote):
                    with mock.patch.object(window, "_show_mount_config_editor") as configure:
                        self.assertTrue(window._handle_main_key(Event()))

        configure.assert_called_once_with(remote)

    def test_remote_list_reorder_shortcut_moves_and_refocuses_remote(self):
        window = object.__new__(tray.MountletWindow)

        with mock.patch.object(window, "_can_move_remote", return_value=True):
            with mock.patch.object(window, "_move_remote") as move:
                with mock.patch.object(window, "_focus_remote_row") as focus:
                    window._move_focused_remote("Beta", -1)

        move.assert_called_once_with("Beta", -1)
        focus.assert_called_once_with("Beta")

    def test_remote_list_reorder_shortcut_does_not_load_remote_metadata(self):
        class Event:
            def key(self) -> object:
                return "custom"

            def accept(self) -> None:
                return

        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            Qt=SimpleNamespace(
                Key=SimpleNamespace(
                    Key_Up="up",
                    Key_Down="down",
                    Key_Return="return",
                    Key_Enter="enter",
                    Key_Left="left",
                    Key_Right="right",
                )
            )
        )

        def matches(_qt: object, _event: object, action: str) -> bool:
            return action == "remote_move_up"

        with mock.patch.object(tray, "matches_shortcut", side_effect=matches):
            with mock.patch.object(window, "_focused_remote_name", return_value="Beta"):
                with mock.patch.object(window, "_move_focused_remote") as move:
                    with mock.patch.object(window, "_remote_by_name") as remote_by_name:
                        self.assertTrue(window._handle_main_key(Event()))

        move.assert_called_once_with("Beta", -1)
        remote_by_name.assert_not_called()

    def test_sync_metadata_summary_is_human_readable(self):
        with mock.patch.object(tray.platform, "node", return_value="laptop"):
            summary = tray._sync_metadata_summary(
                {
                    "created_at": "2026-06-25T10:00:00Z",
                    "device": "desktop-7f3a",
                    "system": "Linux",
                    "system_release": "6.8.0",
                }
            )

        self.assertIn("Updated on Jun 25, 2026 at", summary)
        self.assertIn("from Linux 6.8.0", summary)
        self.assertIn('on device "desktop-7f3a"', summary)
        self.assertNotIn("2026-06-25T10:00:00Z", summary)

    def test_sync_metadata_summary_handles_legacy_metadata(self):
        with mock.patch.object(tray.platform, "node", return_value="desktop-7f3a"):
            summary = tray._sync_metadata_summary(
                {"created_at": "2026-06-25T10:00:00Z", "device": "desktop-7f3a"}
            )

        self.assertIn("from an unknown OS on this device", summary)

    def test_hover_focuses_remote_row_for_keyboard_navigation(self):
        class Row:
            def __init__(self) -> None:
                self.properties: dict[str, object] = {"mounted": True}
                self.focus_reason = None

            def property(self, name: str) -> object:
                return self.properties.get(name)

            def setProperty(self, name: str, value: object) -> None:
                self.properties[name] = value

            def setStyleSheet(self, _style: str) -> None:
                return

            def setFocus(self, reason: object) -> None:
                self.focus_reason = reason

        row = Row()
        remote = core.RemoteInfo("Beta", "Beta", "Drive", "drive", "/tmp/beta")
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            Qt=SimpleNamespace(FocusReason=SimpleNamespace(MouseFocusReason="mouse")),
            QToolTip=mock.Mock(),
            QCursor=mock.Mock(),
        )

        with mock.patch.object(window, "_select_browser_remote") as select_remote:
            window._highlight_remote_row(row, highlighted=True, remote=remote)

        self.assertEqual(row.focus_reason, "mouse")
        select_remote.assert_called_once_with(remote, row)

    def test_shortcut_conflicts_are_scoped_to_context(self):
        dialog = object.__new__(tray.ShortcutConfigDialog)
        shortcuts = {
            **tray.DEFAULT_SHORTCUTS,
            "browser_parent": ("Backspace",),
            "browser_root": ("Backspace",),
        }

        conflicts = dialog._shortcut_conflicts(shortcuts)

        self.assertEqual(len(conflicts), 1)
        self.assertIn("File browser", conflicts[0])
        self.assertIn("Parent folder", conflicts[0])
        self.assertIn("Remote root", conflicts[0])

    def test_window_folder_open_failure_reports_notification(self):
        window = object.__new__(tray.MountletWindow)
        window.tray_app = mock.Mock()
        window.tray_app._quitting = False

        window._handle_folder_opened(False)

        window.tray_app._notify.assert_called_once_with("Open folder", "Could not open the mount folder.", success=False)

    def test_open_remote_in_browser_uses_provider_url(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        tray_app = object.__new__(tray.MountletTray)
        tray_app.qt = mock.Mock()
        tray_app.qt.QUrl.return_value = "qt-url"
        tray_app.qt.QDesktopServices.openUrl.return_value = True
        tray_app.qt.QTimer.singleShot.side_effect = lambda _delay, callback: callback()

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

        tray_app.qt.QTimer.singleShot.assert_not_called()
        tray_app.qt.QDesktopServices.openUrl.assert_not_called()
        notify.assert_called_once_with(
            "Open in browser",
            "This remote does not have a known browser view.",
            success=False,
        )

    def test_tray_remote_action_delegates_to_threaded_main_window_runner(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()

        tray_app._run_remote_action(remote, core.mount_remote)

        tray_app.main_window._run_remote_action.assert_called_once_with(remote, core.mount_remote)

    def test_tray_bulk_actions_delegate_to_threaded_main_window_runner(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()

        tray_app._mount_all([remote])
        tray_app._unmount_all([remote])

        tray_app.main_window._mount_all.assert_called_once_with()
        tray_app.main_window._unmount_all.assert_called_once_with()

    def test_tray_activation_opens_window_before_scheduling_menu_refresh(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()
        tray_app.rebuild_menus = mock.Mock()
        trigger = object()
        tray_app.qt = SimpleNamespace(
            QSystemTrayIcon=SimpleNamespace(ActivationReason=SimpleNamespace(Trigger=trigger)),
            QTimer=mock.Mock(),
        )

        tray_app._handle_activation(trigger)

        tray_app.main_window.toggle_from_tray.assert_called_once_with()
        tray_app.rebuild_menus.assert_not_called()
        tray_app.qt.QTimer.singleShot.assert_called_once_with(25, tray_app.rebuild_menus)

    def test_main_focus_style_updates_only_registered_main_surface(self):
        main_surface = mock.Mock()
        other_surface = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window._main_surface = main_surface
        window._focus_owner = "main"

        window._update_main_focus_style()

        main_surface.setStyleSheet.assert_called_once()
        other_surface.setStyleSheet.assert_not_called()

    def test_visual_only_refresh_skips_background_rclone_checks_once(self):
        remote = core.RemoteInfo("Docs__Drive", "Docs", "Drive", "drive", "/tmp/docs")
        window = object.__new__(tray.MountletWindow)
        window._skip_background_refresh_once = True
        window._current_remote_names = [remote.name]
        window._row_widgets = {remote.name: SimpleNamespace()}
        window._focus_snapshot = mock.Mock(return_value=("main", remote.name))
        window._tray_is_quitting = mock.Mock(return_value=False)
        window._license_locked = mock.Mock(return_value=False)
        window._remote_name_width = mock.Mock(return_value=120)
        window._update_purchase_license_button = mock.Mock()
        window._update_remote_row = mock.Mock()
        window._schedule_storage_load = mock.Mock()
        window._request_config_sync_metadata_check = mock.Mock()
        window._update_license_lock_state = mock.Mock()
        window._browser_layout_changed = mock.Mock()
        window._restore_focus_snapshot = mock.Mock()

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            with mock.patch.object(tray.core, "is_mounted", return_value=False):
                window.refresh()

        window._update_remote_row.assert_called_once_with(remote, False)
        window._request_config_sync_metadata_check.assert_not_called()
        window._schedule_storage_load.assert_not_called()
        self.assertFalse(window._skip_background_refresh_once)

    def test_app_settings_layout_change_reopens_through_normal_show_path(self):
        old_settings = settings.AppSettings(window_mode=settings.WINDOW_MODE_MULTIPLE)
        new_settings = settings.AppSettings(window_mode=settings.WINDOW_MODE_SINGLE)
        dialog = SimpleNamespace(dialog=object())
        tray_app = SimpleNamespace(_is_wayland=False, app=object(), rebuild_menus=mock.Mock())
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace()
        window.tray_app = tray_app
        window.window = mock.Mock()
        window.window.isVisible.return_value = True
        window._license_locked = mock.Mock(return_value=False)
        window._file_browser_embedded = mock.Mock(return_value=False)
        window._mounted_remote_names = mock.Mock(return_value=set())
        window._rebuild_file_browser_if_layout_changed = mock.Mock()
        window._remount_changes = mock.Mock(return_value=[])
        window._configuration_changed = mock.Mock()
        window._ask_remount_for_config_changes = mock.Mock()
        window._setup_remote_change_polling = mock.Mock()
        window.refresh = mock.Mock()
        window.show = mock.Mock()
        window._usage_cache = {}
        window._connection_cache = {}

        def open_child_dialog(_dialog: object, on_accepted: object) -> None:
            on_accepted()

        window._open_child_dialog = mock.Mock(side_effect=open_child_dialog)

        with mock.patch.object(tray, "load_app_settings", side_effect=[old_settings, new_settings]):
            with mock.patch.object(tray, "_load_visible_remotes", return_value=[]):
                with mock.patch.object(tray.core, "ensure_base_mount_dir", return_value=(tray.core.BASE_MOUNT_DIR, "")):
                    with mock.patch.object(tray, "_apply_theme"):
                        with mock.patch.object(tray, "AppConfigDialog", return_value=dialog):
                            window._show_app_config_editor()

        window.window.hide.assert_called_once_with()
        tray_app.rebuild_menus.assert_called_once_with()
        window.show.assert_called_once_with()
        window.refresh.assert_not_called()

    def test_tray_app_settings_shows_main_window_before_dialog(self):
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()
        tray_app.qt = mock.Mock()
        events: list[str] = []
        tray_app.main_window.show.side_effect = lambda: events.append("window")

        def single_shot(delay: int, callback: object) -> None:
            self.assertEqual(delay, 0)
            events.append("timer")
            callback()

        tray_app.qt.QTimer.singleShot.side_effect = single_shot
        tray_app.main_window._show_app_config_editor.side_effect = lambda: events.append("dialog")

        tray_app._show_app_settings_from_tray()

        self.assertEqual(events, ["window", "timer", "dialog"])

    def test_windows_file_dialogs_avoid_native_untrusted_mount_prompt(self):
        option = object()
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(
            QFileDialog=SimpleNamespace(Option=SimpleNamespace(DontUseNativeDialog=option)),
        )

        with mock.patch.object(tray.platform, "system", return_value="Windows"):
            self.assertEqual(window._file_dialog_kwargs(), {"options": option})

    def test_non_windows_file_dialogs_use_platform_default(self):
        window = object.__new__(tray.MountletWindow)
        window.qt = SimpleNamespace(QFileDialog=SimpleNamespace())

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            self.assertEqual(window._file_dialog_kwargs(), {})

    def test_app_settings_folder_picker_updates_app_folder_field(self):
        dialog = object.__new__(tray.AppConfigDialog)
        field = mock.Mock()
        field.text.return_value = "/home/tester/Mountlet"
        dialog.fields = {"mount_base": field}
        dialog.dialog = object()
        dialog.qt = SimpleNamespace(
            QFileDialog=SimpleNamespace(getExistingDirectory=mock.Mock(return_value="/home/tester/Storage/Mountlet"))
        )

        dialog._choose_app_folder()

        dialog.qt.QFileDialog.getExistingDirectory.assert_called_once_with(
            dialog.dialog,
            "Choose Mountlet app folder",
            "/home/tester/Mountlet",
        )
        field.setText.assert_called_once_with("/home/tester/Storage/Mountlet")

    def test_import_config_confirmation_includes_bundle_os_metadata(self):
        selected = "/tmp/config.mountlet"
        yes = 1
        window = object.__new__(tray.MountletWindow)
        question = mock.Mock(return_value=yes)
        window.qt = SimpleNamespace(
            QFileDialog=SimpleNamespace(getOpenFileName=mock.Mock(return_value=(selected, ""))),
            QMessageBox=SimpleNamespace(
                StandardButton=SimpleNamespace(Yes=yes, No=2),
                question=question,
            ),
        )
        window.window = object()
        window.tray_app = SimpleNamespace(_notify=mock.Mock())
        window._file_dialog_kwargs = mock.Mock(return_value={})
        window._ask_bundle_password = mock.Mock(return_value="")
        window._mounted_remote_file = mock.Mock(return_value=None)
        window._rclone_config_replaced = mock.Mock()
        window._record_config_sync_state = mock.Mock()

        metadata = {
            "created_at": "2026-06-25T10:00:00Z",
            "device": "desktop-7f3a",
            "system": "Windows",
            "system_release": "11",
        }
        with mock.patch.object(tray.bundle_file, "bundle_metadata", return_value=metadata):
            with mock.patch.object(tray.bundle_file, "import_bundle_file", return_value=None):
                window._import_config_bundle()

        message = question.call_args.args[2]
        self.assertIn("from Windows 11", message)
        self.assertIn('on device "desktop-7f3a"', message)
        window._record_config_sync_state.assert_called_once_with(metadata)
        self.assertIs(window._remote_sync_metadata, metadata)

    def test_mounted_remote_file_maps_local_mount_path_to_remote_path(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)

        with mock.patch.object(tray.core, "load_remotes", return_value=[remote]):
            self.assertEqual(
                window._mounted_remote_file(Path("/mnt/docs/Backups/config.mountlet")),
                (remote, "Backups/config.mountlet"),
            )

    def test_copy_remote_file_to_local_uses_rclone_copyto(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        result = SimpleNamespace(returncode=0, stderr="")
        destination = Path("/tmp/config.mountlet")

        with mock.patch.object(tray.core, "find_rclone", return_value="rclone"):
            with mock.patch.object(tray.subprocess, "run", return_value=result) as run:
                window._copy_remote_file_to_local(remote, "Backups/config.mountlet", destination)

        self.assertEqual(
            run.call_args.args[0],
            [
                "rclone",
                "--config",
                tray.core.CONFIG_PATH,
                "copyto",
                "Docs:/Backups/config.mountlet",
                str(destination),
            ],
        )

    def test_copy_local_file_to_remote_uses_rclone_copyto(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        result = SimpleNamespace(returncode=0, stderr="")
        source = Path("/tmp/config.mountlet")

        with mock.patch.object(tray.core, "find_rclone", return_value="rclone"):
            with mock.patch.object(tray.subprocess, "run", return_value=result) as run:
                window._copy_local_file_to_remote(source, remote, "Backups/config.mountlet")

        self.assertEqual(
            run.call_args.args[0],
            [
                "rclone",
                "--config",
                tray.core.CONFIG_PATH,
                "copyto",
                str(source),
                "Docs:/Backups/config.mountlet",
            ],
        )

    def test_bundle_export_completed_refreshes_dolphin_for_mounted_remote(self):
        window = object.__new__(tray.MountletWindow)
        window.file_browser = mock.Mock()

        with mock.patch.object(tray.platform, "system", return_value="Linux"):
            with mock.patch.object(tray, "_open_folder_in_dolphin_tab", return_value=True) as refresh:
                window._bundle_export_completed(Path("/mnt/docs/Backups/config.mountlet"), True)

        window.file_browser.invalidate.assert_called_once_with()
        refresh.assert_called_once_with(str(Path("/mnt/docs/Backups")), focus=False)

    def test_config_sync_target_resolves_remote_and_adds_bundle_suffix(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        window.tray_app = SimpleNamespace(_notify=mock.Mock())

        with mock.patch.object(
            tray,
            "load_app_settings",
            return_value=settings.AppSettings(config_sync_remote="Docs", config_sync_path="Backups/config"),
        ):
            with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
                self.assertEqual(window._config_sync_target(), (remote, "Backups/config.mountlet"))

    def test_push_config_sync_bundle_uploads_encrypted_bundle_to_remote_target(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        window.tray_app = SimpleNamespace(_notify=mock.Mock())
        window._remote_sync_metadata = None
        window._update_config_sync_buttons = mock.Mock()

        with mock.patch.object(window, "_config_sync_target", return_value=(remote, "Backups/config.mountlet")):
            with mock.patch.object(window, "_ask_bundle_password", return_value="secret"):
                with mock.patch.object(tray.bundle_file, "export_bundle_file", return_value=Path("config.mountlet")) as export:
                    with mock.patch.object(window, "_copy_local_file_to_remote") as copy:
                        window._push_config_sync_bundle()

        self.assertEqual(export.call_args.kwargs["password"], "secret")
        self.assertEqual(copy.call_args.args[1:], (remote, "Backups/config.mountlet"))
        window.tray_app._notify.assert_called()

    def test_record_config_sync_state_clears_push_and_pull_drift(self):
        window = object.__new__(tray.MountletWindow)
        saved = {}

        with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="local-hash"):
            with mock.patch.object(tray, "_load_config_sync_state", return_value={}):
                with mock.patch.object(tray, "_save_config_sync_state", side_effect=lambda state: saved.update(state)):
                    window._record_config_sync_state(
                        {"config_hash": "local-hash", "created_at": "2026-06-25T10:00:00Z", "device": "laptop"}
                    )

        self.assertEqual(saved["last_synced_hash"], "local-hash")
        self.assertEqual(saved["last_synced_hash_kind"], "operation")
        self.assertEqual(saved["last_local_config_hash"], "local-hash")
        self.assertEqual(saved["last_pushed_hash"], "local-hash")
        self.assertEqual(saved["last_pulled_hash"], "local-hash")
        self.assertEqual(saved["remote_config_hash"], "local-hash")
        self.assertEqual(saved["last_remote_config_hash"], "local-hash")

    def test_imported_cross_platform_bundle_clears_push_dot_against_local_hash(self):
        window = object.__new__(tray.MountletWindow)
        saved = {}
        push = mock.Mock()
        pull = mock.Mock()
        window._push_sync_button = push
        window._pull_sync_button = pull
        window._remote_sync_metadata = {"config_hash": "linux-bundle-hash", "created_at": "time", "device": "linux"}

        with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="windows-local-hash"):
            with mock.patch.object(tray, "_load_config_sync_state", return_value={}):
                with mock.patch.object(tray, "_save_config_sync_state", side_effect=lambda state: saved.update(state)):
                    window._record_config_sync_state(window._remote_sync_metadata)

        self.assertEqual(saved["last_synced_hash"], "windows-local-hash")
        self.assertEqual(saved["last_local_config_hash"], "windows-local-hash")
        self.assertEqual(saved["remote_config_hash"], "linux-bundle-hash")
        self.assertEqual(saved["last_remote_config_hash"], "linux-bundle-hash")

        with mock.patch.object(tray, "load_app_settings", return_value=settings.AppSettings(config_sync_remote="Docs")):
            with mock.patch.object(tray, "_load_config_sync_state", return_value=saved):
                with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="windows-local-hash"):
                    window._update_config_sync_buttons()

        push.setText.assert_called_once_with("↑")
        pull.setText.assert_called_once_with("↓")
        push.setBadgeVisible.assert_called_once_with(False)
        pull.setBadgeVisible.assert_called_once_with(False)

    def test_sync_button_push_dot_uses_last_local_hash_after_pull(self):
        window = object.__new__(tray.MountletWindow)
        push = mock.Mock()
        pull = mock.Mock()
        window._push_sync_button = push
        window._pull_sync_button = pull
        window._remote_sync_metadata = {"config_hash": "linux-bundle-hash", "created_at": "time", "device": "linux"}
        state = {
            "last_synced_hash": "windows-local-hash",
            "last_local_config_hash": "windows-local-hash",
            "remote_config_hash": "linux-bundle-hash",
            "last_remote_config_hash": "linux-bundle-hash",
        }

        with mock.patch.object(tray, "load_app_settings", return_value=settings.AppSettings(config_sync_remote="Docs")):
            with mock.patch.object(tray, "_load_config_sync_state", return_value=state):
                with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="windows-local-hash"):
                    window._update_config_sync_buttons()

        push.setBadgeVisible.assert_called_once_with(False)
        pull.setBadgeVisible.assert_called_once_with(False)

    def test_sync_button_dots_compare_against_last_synced_hash(self):
        window = object.__new__(tray.MountletWindow)
        push = mock.Mock()
        pull = mock.Mock()
        window._push_sync_button = push
        window._pull_sync_button = pull
        window._remote_sync_metadata = {"config_hash": "remote-hash", "created_at": "time", "device": "desktop"}

        with mock.patch.object(tray, "load_app_settings", return_value=settings.AppSettings(config_sync_remote="Docs")):
            with mock.patch.object(tray, "_load_config_sync_state", return_value={"last_synced_hash": "synced-hash"}):
                with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="local-hash"):
                    window._update_config_sync_buttons()

        push.setText.assert_called_once_with("↑")
        pull.setText.assert_called_once_with("↓")
        push.setBadgeVisible.assert_called_once_with(True)
        pull.setBadgeVisible.assert_called_once_with(True)

    def test_sync_button_push_dot_appears_after_semantic_local_change(self):
        window = object.__new__(tray.MountletWindow)
        push = mock.Mock()
        pull = mock.Mock()
        window._push_sync_button = push
        window._pull_sync_button = pull
        window._remote_sync_metadata = {"config_hash": "synced-hash", "created_at": "time", "device": "desktop"}

        state = {
            "last_synced_hash": "synced-hash",
            "last_synced_hash_kind": "operation",
            "remote_config_hash": "synced-hash",
        }
        with mock.patch.object(tray, "load_app_settings", return_value=settings.AppSettings(config_sync_remote="Docs")):
            with mock.patch.object(tray, "_load_config_sync_state", return_value=state):
                with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="changed-hash"):
                    window._update_config_sync_buttons()

        push.setText.assert_called_once_with("↑")
        push.setBadgeVisible.assert_called_once_with(True)
        pull.setText.assert_called_once_with("↓")
        pull.setBadgeVisible.assert_called_once_with(False)

    def test_sync_button_dots_ignore_known_pre_update_remote_hash(self):
        window = object.__new__(tray.MountletWindow)
        push = mock.Mock()
        pull = mock.Mock()
        window._push_sync_button = push
        window._pull_sync_button = pull
        window._remote_sync_metadata = {"config_hash": "old-raw-hash", "created_at": "time", "device": "desktop"}

        with mock.patch.object(tray, "load_app_settings", return_value=settings.AppSettings(config_sync_remote="Docs")):
            with mock.patch.object(tray, "_load_config_sync_state", return_value={"last_synced_hash": "old-raw-hash"}):
                with mock.patch.object(tray, "_save_config_sync_state") as save:
                    with mock.patch.object(tray.bundle_file, "current_config_fingerprint", return_value="semantic-hash"):
                        window._update_config_sync_buttons()

        push.setText.assert_called_once_with("↑")
        pull.setText.assert_called_once_with("↓")
        push.setBadgeVisible.assert_called_once_with(False)
        pull.setBadgeVisible.assert_called_once_with(False)
        self.assertEqual(save.call_args.args[0]["last_synced_hash"], "semantic-hash")
        self.assertEqual(save.call_args.args[0]["last_synced_hash_kind"], "operation")

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

    def test_auto_mount_delegates_to_threaded_main_window_runner(self):
        remote = core.RemoteInfo("Docs", "Docs", "drive", "drive", "/tmp/docs", auto_mount=True)
        tray_app = object.__new__(tray.MountletTray)
        tray_app._quitting = False
        tray_app.main_window = mock.Mock()

        tray_app._auto_mount([remote])

        tray_app.main_window._run_bulk_action_for_remotes.assert_called_once_with(
            "Auto-mount",
            [remote],
            tray.core.mount_all,
        )

    def test_remote_action_finish_invalidates_file_browser_cache(self):
        tray_app = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window.tray_app = tray_app
        window._action_pending = {"Docs"}
        window._usage_cache = {"Docs": core.StorageUsage("?")}
        window.file_browser = mock.Mock()
        window._tray_is_quitting = mock.Mock(return_value=False)
        window._request_refresh = mock.Mock()

        window._handle_action_finished("Docs", True, "[*] mounted Docs")

        self.assertNotIn("Docs", window._action_pending)
        self.assertNotIn("Docs", window._usage_cache)
        window.file_browser.invalidate.assert_called_once_with("Docs")
        tray_app.rebuild_menus.assert_called_once_with()
        window._request_refresh.assert_called_once_with()

    def test_remote_action_keeps_visible_browser_open_for_working_remote(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        window._action_pending = set()
        window._request_refresh = mock.Mock()
        window._bridge = SimpleNamespace(action_finished=SimpleNamespace(emit=mock.Mock()))
        window.file_browser = mock.Mock(
            remote=remote,
            is_visible=mock.Mock(return_value=True),
            has_focus=mock.Mock(return_value=True),
        )

        with mock.patch.object(tray.threading, "Thread") as thread:
            thread.side_effect = lambda target, daemon: mock.Mock(start=lambda: target())
            window._run_remote_action(remote, lambda _remote: (True, "done"))

        window.file_browser.close.assert_not_called()
        window._bridge.action_finished.emit.assert_called_once_with("Docs", True, "done")

    def test_remote_action_finish_does_not_force_reopen_browser(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        tray_app = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window.tray_app = tray_app
        window._action_pending = {"Docs"}
        window._usage_cache = {}
        window._selected_remote_name = "Docs"
        row = mock.Mock()
        window._row_widgets = {"Docs": SimpleNamespace(frame=row)}
        window.file_browser = mock.Mock()
        window._tray_is_quitting = mock.Mock(return_value=False)
        window._request_refresh = mock.Mock()

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            window._handle_action_finished("Docs", True, "[*] mounted Docs")

        window.file_browser.show_remote.assert_not_called()

    def test_file_browser_refreshes_when_app_regains_focus(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        window.file_browser = mock.Mock(remote=remote, is_visible=mock.Mock(return_value=True))

        window._handle_main_window_activation(active=True)

        window.file_browser.invalidate.assert_called_once_with("Docs")
        window.file_browser.close.assert_not_called()

    def test_file_browser_is_left_open_when_app_loses_focus(self):
        window = object.__new__(tray.MountletWindow)
        window.file_browser = mock.Mock()

        window._handle_main_window_activation(active=False)

        window.file_browser.close.assert_not_called()
        window.file_browser.hide.assert_not_called()

    def test_remote_action_failure_can_prompt_reauthentication(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        tray_app = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window.tray_app = tray_app
        window.window = mock.Mock()
        window._action_pending = {"Docs"}
        window._usage_cache = {}
        window.file_browser = mock.Mock()
        window._tray_is_quitting = mock.Mock(return_value=False)
        window._request_refresh = mock.Mock()
        window._run_remote_reauthentication = mock.Mock()
        yes = 1
        window.qt = SimpleNamespace(
            QMessageBox=SimpleNamespace(
                StandardButton=SimpleNamespace(Yes=yes, No=2),
                question=mock.Mock(return_value=yes),
            )
        )

        with mock.patch.object(tray, "_load_visible_remotes", return_value=[remote]):
            window._handle_action_finished("Docs", False, "token expired")

        window._run_remote_reauthentication.assert_called_once_with(remote, remount=True)

    def test_remote_reauthentication_retries_mount_after_reconnect(self):
        remote = core.RemoteInfo("Docs", "Docs", "Drive", "drive", "/mnt/docs")
        window = object.__new__(tray.MountletWindow)
        window._action_pending = set()
        window._request_refresh = mock.Mock()
        window._bridge = SimpleNamespace(action_finished=SimpleNamespace(emit=mock.Mock()))

        with mock.patch.object(tray.threading, "Thread") as thread:
            worker_holder = {}
            thread.side_effect = lambda target, daemon: worker_holder.setdefault("thread", mock.Mock(start=lambda: target()))
            with mock.patch.object(tray.core, "reconnect_remote", return_value=(True, "reauthenticated")):
                with mock.patch.object(tray.core, "mount_remote", return_value=(True, "mounted")):
                    window._run_remote_reauthentication(remote, remount=True)

        window._bridge.action_finished.emit.assert_called_once_with("Docs", True, "reauthenticated\nmounted")

    def test_bulk_action_finish_invalidates_pending_file_browser_caches(self):
        tray_app = mock.Mock()
        window = object.__new__(tray.MountletWindow)
        window.tray_app = tray_app
        window._action_pending = {"Docs", "Photos"}
        window._usage_cache = {"Docs": core.StorageUsage("?")}
        window.file_browser = mock.Mock()
        window._tray_is_quitting = mock.Mock(return_value=False)
        window._request_refresh = mock.Mock()

        window._handle_bulk_action_finished("Mount all", ["Docs"], [])

        self.assertEqual(window._action_pending, set())
        self.assertEqual(window._usage_cache, {})
        window.file_browser.invalidate.assert_has_calls([mock.call("Docs"), mock.call("Photos")], any_order=True)
        tray_app.rebuild_menus.assert_called_once_with()
        window._request_refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
