#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
from types import SimpleNamespace
from typing import Any

from . import core
from .config_tools import setup_wizard
from .config_tools.shared import ensure_app_directories


class TrayDependencyError(RuntimeError):
    pass


def _load_qt_bindings() -> SimpleNamespace:
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QAction, QDesktopServices, QIcon
        from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon
    except ImportError as exc:
        raise TrayDependencyError(
            "Tray support requires PySide6. Install it with:\n"
            '  pipx install "cloud-mount-manager[tray]"\n'
            "or, for an existing pipx install:\n"
            "  pipx inject cloud-mount-manager PySide6"
        ) from exc

    return SimpleNamespace(
        QAction=QAction,
        QApplication=QApplication,
        QDesktopServices=QDesktopServices,
        QIcon=QIcon,
        QMenu=QMenu,
        QStyle=QStyle,
        QSystemTrayIcon=QSystemTrayIcon,
        QTimer=QTimer,
        QUrl=QUrl,
    )


def _clean_message(message: str) -> str:
    return message.replace("[*]", "").replace("[!]", "").replace("[✓]", "").strip()


def _remote_title(remote: core.RemoteInfo, mounted: bool) -> str:
    marker = "Mounted" if mounted else "Unmounted"
    return f"{remote.display_name} - {marker}"


def _can_connect_unix_socket(path: str) -> bool:
    client = socket.socket(socket.AF_UNIX)
    try:
        client.settimeout(0.2)
        client.connect(path)
        return True
    except OSError:
        return False
    finally:
        client.close()


def _desktop_session_available() -> tuple[bool, str]:
    if platform.system() != "Linux":
        return True, ""

    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if wayland_display and runtime_dir:
        path = os.path.join(runtime_dir, wayland_display)
        if os.path.exists(path) and _can_connect_unix_socket(path):
            return True, ""

    display = os.environ.get("DISPLAY", "")
    if not display:
        return False, "No graphical desktop session was detected."

    if display.startswith(":"):
        display_number = display[1:].split(".", 1)[0]
        path = f"/tmp/.X11-unix/X{display_number}"
        if os.path.exists(path) and _can_connect_unix_socket(path):
            return True, ""
        return False, f"Cannot connect to the X11 display at {display}."

    return True, ""


class CloudMountTray:
    def __init__(self, qt: SimpleNamespace, refresh_interval: int = 10) -> None:
        self.qt = qt
        self.refresh_interval = max(refresh_interval, 2)
        self.app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        self.menu = qt.QMenu()
        self.tray = qt.QSystemTrayIcon(self._icon(), self.app)
        self.tray.setToolTip("Cloud Mount Manager")
        self.tray.setContextMenu(self.menu)
        self.timer = qt.QTimer()
        self.timer.timeout.connect(self.rebuild_menu)

    def _icon(self) -> Any:
        try:
            return self.app.style().standardIcon(self.qt.QStyle.StandardPixmap.SP_DriveNetIcon)
        except Exception:
            return self.qt.QIcon()

    def run(self) -> int:
        if not self.qt.QSystemTrayIcon.isSystemTrayAvailable():
            print("[!] No system tray is available in this desktop session.", file=sys.stderr)
            print("    Use the terminal menu instead: cloud-mount-manager", file=sys.stderr)
            return 1

        self.rebuild_menu()
        self.tray.show()
        self.timer.start(self.refresh_interval * 1000)
        return int(self.app.exec() or 0)

    def rebuild_menu(self) -> None:
        self.menu.clear()
        remotes = core.load_remotes()

        if remotes:
            for remote in remotes:
                self._add_remote_menu(remote)
        else:
            action = self.menu.addAction("No rclone remotes found")
            action.setEnabled(False)

        self.menu.addSeparator()
        self._add_action(self.menu, "Mount all", lambda: self._mount_all(remotes), enabled=bool(remotes))
        self._add_action(self.menu, "Unmount all", lambda: self._unmount_all(remotes), enabled=bool(remotes))
        self._add_action(self.menu, "Refresh menu", self.rebuild_menu)
        self.menu.addSeparator()
        self._add_action(self.menu, "Quit", self.app.quit)

    def _add_remote_menu(self, remote: core.RemoteInfo) -> None:
        mounted = core.is_mounted(remote)
        submenu = self.menu.addMenu(_remote_title(remote, mounted))

        status = submenu.addAction(f"Path: {remote.mount_path}")
        status.setEnabled(False)

        if mounted:
            self._add_action(submenu, "Unmount", lambda: self._run_remote_action(remote, core.unmount_remote))
            self._add_action(submenu, "Refresh mount", lambda: self._run_remote_action(remote, core.refresh_remote))
            self._add_action(submenu, "Open folder", lambda: self._open_folder(remote))
        else:
            self._add_action(submenu, "Mount", lambda: self._run_remote_action(remote, core.mount_remote))

    def _add_action(self, menu: Any, label: str, callback: Any, *, enabled: bool = True) -> Any:
        action = self.qt.QAction(label, menu)
        action.setEnabled(enabled)
        action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        return action

    def _run_remote_action(self, remote: core.RemoteInfo, action: Any) -> None:
        success, message = action(remote)
        self._notify("Cloud Mount Manager", _clean_message(message), success=success)
        self.rebuild_menu()

    def _mount_all(self, remotes: list[core.RemoteInfo]) -> None:
        mounted, failures = core.mount_all(remotes)
        if failures:
            self._notify("Mount all", "\n".join(_clean_message(item) for item in failures), success=False)
        elif mounted:
            self._notify("Mount all", "Mounted: " + ", ".join(mounted), success=True)
        else:
            self._notify("Mount all", "Nothing to mount.", success=True)
        self.rebuild_menu()

    def _unmount_all(self, remotes: list[core.RemoteInfo]) -> None:
        unmounted, failures = core.unmount_all(remotes)
        if failures:
            self._notify("Unmount all", "\n".join(_clean_message(item) for item in failures), success=False)
        elif unmounted:
            self._notify("Unmount all", "Unmounted: " + ", ".join(unmounted), success=True)
        else:
            self._notify("Unmount all", "Nothing to unmount.", success=True)
        self.rebuild_menu()

    def _open_folder(self, remote: core.RemoteInfo) -> None:
        if not os.path.isdir(remote.mount_path):
            self._notify("Open folder", "Mount the remote before opening its folder.", success=False)
            return
        self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(remote.mount_path))

    def _notify(self, title: str, message: str, *, success: bool) -> None:
        print(f"{title}: {message}")
        icon = (
            self.qt.QSystemTrayIcon.MessageIcon.Information
            if success
            else self.qt.QSystemTrayIcon.MessageIcon.Warning
        )
        self.tray.showMessage(title, message, icon, 5000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Cloud Mount Manager desktop tray app.")
    parser.add_argument(
        "--skip-readiness-check",
        action="store_true",
        help="Start the tray without checking rclone, FUSE, and configured remotes first.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=10,
        help="Seconds between automatic tray status refreshes (default: 10).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.skip_readiness_check and not setup_wizard.ensure_ready_for_menu():
        return 1

    desktop_ready, message = _desktop_session_available()
    if not desktop_ready:
        print(f"[!] {message}", file=sys.stderr)
        print("    Use the terminal menu instead: cloud-mount-manager", file=sys.stderr)
        return 1

    try:
        qt = _load_qt_bindings()
    except TrayDependencyError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    ensure_app_directories()
    core.ensure_base_mount_dir()
    return CloudMountTray(qt, refresh_interval=args.refresh_interval).run()


if __name__ == "__main__":
    raise SystemExit(main())
