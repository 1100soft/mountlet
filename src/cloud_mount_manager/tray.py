#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import core
from .config_tools import setup_wizard
from .config_tools.shared import ensure_app_directories


_DOLPHIN_MAIN_WINDOW_PATH = "/dolphin/Dolphin_1"
_dolphin_tab_target_cache: tuple[str, str] | None = None
_CARDINAL_RE = re.compile(r"=\s*(\d+)")


class TrayDependencyError(RuntimeError):
    pass


def _load_qt_bindings() -> SimpleNamespace:
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon
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
        QCursor=QCursor,
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


def _status_tooltip(remotes: list[core.RemoteInfo], mounted_names: list[str]) -> str:
    if not remotes:
        return "Cloud Mount Manager - no rclone remotes"

    unmounted_count = len(remotes) - len(mounted_names)
    if not mounted_names:
        return f"Cloud Mount Manager - 0 mounted, {unmounted_count} unmounted"

    names = ", ".join(mounted_names[:3])
    if len(mounted_names) > 3:
        names += f", +{len(mounted_names) - 3} more"
    return f"Cloud Mount Manager - mounted: {names}"


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


def _folder_uri(path: str) -> str:
    return Path(path).expanduser().resolve().as_uri()


def _show_folder_with_file_manager(path: str) -> bool:
    if platform.system() != "Linux":
        return False

    command = [
        "dbus-send",
        "--session",
        "--type=method_call",
        "--dest=org.freedesktop.FileManager1",
        "/org/freedesktop/FileManager1",
        "org.freedesktop.FileManager1.ShowFolders",
        f"array:string:{_folder_uri(path)}",
        "string:",
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return result.returncode == 0


def _default_directory_app() -> str:
    if platform.system() != "Linux":
        return ""
    try:
        result = subprocess.run(
            ["xdg-mime", "query", "default", "inode/directory"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _open_folder_with_dolphin(path: str) -> bool:
    if _open_folder_in_dolphin_tab(path):
        return True

    dolphin = shutil.which("dolphin")
    if not dolphin:
        return False
    try:
        subprocess.Popen(
            [dolphin, "--new-window", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def _qdbus_binary() -> str | None:
    return shutil.which("qdbus6") or shutil.which("qdbus")


def _qdbus_lines(args: list[str]) -> list[str]:
    qdbus = _qdbus_binary()
    if not qdbus:
        return []
    try:
        result = subprocess.run(
            [qdbus, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _qt_dbus_session() -> SimpleNamespace | None:
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    return SimpleNamespace(QDBusInterface=QDBusInterface, bus=bus)


def _dolphin_service_sort_key(service: str) -> int:
    suffix = service.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        return -1
    return int(suffix)


def _sort_dolphin_services(services: list[str]) -> list[str]:
    return sorted(
        (service for service in services if service.startswith("org.kde.dolphin-")),
        key=_dolphin_service_sort_key,
        reverse=True,
    )


def _dolphin_dbus_services(dbus: SimpleNamespace | None = None) -> list[str]:
    if dbus:
        reply = dbus.bus.interface().registeredServiceNames()
        if reply.isValid():
            return _sort_dolphin_services(reply.value())
    return _sort_dolphin_services(_qdbus_lines([]))


def _dolphin_dbus_windows() -> list[tuple[str, str]]:
    windows: list[tuple[str, str]] = []
    for service in _dolphin_dbus_services():
        paths = _qdbus_lines([service])
        for path in paths:
            if path.startswith("/dolphin/Dolphin_"):
                windows.append((service, path))
    return windows


def _dolphin_window_is_active(qdbus: str, service: str, object_path: str) -> bool:
    try:
        result = subprocess.run(
            [qdbus, service, object_path, "org.kde.dolphin.MainWindow.isActiveWindow"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _ordered_dolphin_dbus_windows(qdbus: str) -> list[tuple[str, str]]:
    active: list[tuple[str, str]] = []
    inactive: list[tuple[str, str]] = []
    for service, object_path in _dolphin_dbus_windows():
        if _dolphin_window_is_active(qdbus, service, object_path):
            active.append((service, object_path))
        else:
            inactive.append((service, object_path))
    return [*active, *inactive]


def _parse_xprop_cardinal(output: str) -> int | None:
    match = _CARDINAL_RE.search(output)
    if not match:
        return None
    return int(match.group(1))


def _xprop_cardinal(args: list[str]) -> int | None:
    xprop = shutil.which("xprop")
    if not xprop:
        return None
    try:
        result = subprocess.run(
            [xprop, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return _parse_xprop_cardinal(result.stdout)


def _x11_current_desktop() -> int | None:
    if platform.system() != "Linux":
        return None
    if not os.environ.get("DISPLAY"):
        return None
    return _xprop_cardinal(["-root", "_NET_CURRENT_DESKTOP"])


def _x11_window_desktop(window_id: int) -> int | None:
    return _xprop_cardinal(["-id", str(window_id), "_NET_WM_DESKTOP"])


def _dolphin_window_id_with_qtdbus(
    dbus: SimpleNamespace,
    service: str,
    object_path: str,
) -> int | None:
    interface = dbus.QDBusInterface(service, object_path, "org.kde.KMainWindow", dbus.bus)
    if not interface.isValid():
        return None
    reply = interface.call("winId")
    if reply.errorName() or not reply.arguments():
        return None
    value = reply.arguments()[0]
    if not isinstance(value, int):
        return None
    return value


def _dolphin_window_id(
    dbus: SimpleNamespace | None,
    qdbus: str | None,
    service: str,
    object_path: str,
) -> int | None:
    if dbus:
        window_id = _dolphin_window_id_with_qtdbus(dbus, service, object_path)
        if window_id is not None:
            return window_id
    if not qdbus:
        return None
    try:
        result = subprocess.run(
            [qdbus, service, object_path, "org.kde.KMainWindow.winId"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _dolphin_window_is_on_desktop(
    dbus: SimpleNamespace | None,
    qdbus: str | None,
    service: str,
    object_path: str,
    desktop: int,
) -> bool:
    window_id = _dolphin_window_id(dbus, qdbus, service, object_path)
    if window_id is None:
        return False
    return _x11_window_desktop(window_id) == desktop


def _dolphin_current_desktop_targets(
    dbus: SimpleNamespace | None,
    qdbus: str | None,
    tried: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    desktop = _x11_current_desktop()
    if desktop is None:
        return []

    targets: list[tuple[str, str]] = []
    for service, object_path in _dolphin_fast_tab_targets(dbus):
        if (service, object_path) in tried:
            continue
        if _dolphin_window_is_on_desktop(dbus, qdbus, service, object_path, desktop):
            targets.append((service, object_path))
    return targets


def _open_folder_in_dolphin_window_with_qtdbus(
    dbus: SimpleNamespace,
    service: str,
    object_path: str,
    uri: str,
) -> bool:
    interface = dbus.QDBusInterface(service, object_path, "org.kde.dolphin.MainWindow", dbus.bus)
    if not interface.isValid():
        return False
    reply = interface.call("openDirectories", [uri], False)
    if reply.errorName():
        return False
    interface.call("activateWindow", "")
    return True


def _open_folder_in_dolphin_window(
    dbus: SimpleNamespace | None,
    qdbus: str | None,
    service: str,
    object_path: str,
    uri: str,
) -> bool:
    if dbus and _open_folder_in_dolphin_window_with_qtdbus(dbus, service, object_path, uri):
        return True
    if not qdbus:
        return False

    try:
        result = subprocess.run(
            [
                qdbus,
                service,
                object_path,
                "org.kde.dolphin.MainWindow.openDirectories",
                uri,
                "false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False

    try:
        subprocess.run(
            [qdbus, service, object_path, "org.kde.dolphin.MainWindow.activateWindow", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    return True


def _dolphin_fast_tab_targets(dbus: SimpleNamespace | None = None) -> list[tuple[str, str]]:
    return [(service, _DOLPHIN_MAIN_WINDOW_PATH) for service in _dolphin_dbus_services(dbus)]


def _dolphin_slow_tab_targets(qdbus: str, tried: set[tuple[str, str]]) -> list[tuple[str, str]]:
    return [target for target in _ordered_dolphin_dbus_windows(qdbus) if target not in tried]


def _open_folder_in_dolphin_tab(path: str) -> bool:
    global _dolphin_tab_target_cache

    uri = _folder_uri(path)
    dbus = _qt_dbus_session()
    qdbus = None if dbus else _qdbus_binary()
    if not dbus and not qdbus:
        return False

    tried: set[tuple[str, str]] = set()
    if _dolphin_tab_target_cache:
        service, object_path = _dolphin_tab_target_cache
        tried.add((service, object_path))
        desktop = _x11_current_desktop()
        cache_is_usable = desktop is None or _dolphin_window_is_on_desktop(
            dbus,
            qdbus,
            service,
            object_path,
            desktop,
        )
        if cache_is_usable and _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri):
            return True
        _dolphin_tab_target_cache = None

    for service, object_path in _dolphin_current_desktop_targets(dbus, qdbus, tried):
        tried.add((service, object_path))
        if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri):
            _dolphin_tab_target_cache = (service, object_path)
            return True

    if _x11_current_desktop() is not None:
        return False

    for service, object_path in _dolphin_fast_tab_targets(dbus):
        if (service, object_path) in tried:
            continue
        tried.add((service, object_path))
        if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri):
            _dolphin_tab_target_cache = (service, object_path)
            return True

    qdbus = qdbus or _qdbus_binary()
    if not qdbus:
        return False
    for service, object_path in _dolphin_slow_tab_targets(qdbus, tried):
        tried.add((service, object_path))
        if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri):
            _dolphin_tab_target_cache = (service, object_path)
            return True

    return False


def _open_folder_with_known_file_manager(path: str) -> bool:
    default_app = _default_directory_app().lower()
    if "dolphin" in default_app:
        return _open_folder_with_dolphin(path)
    return False


def _open_folder_default(qt: SimpleNamespace, path: str, strategy: str = "default") -> bool:
    if strategy == "file-manager-service" and _show_folder_with_file_manager(path):
        return True
    if strategy == "default" and _open_folder_with_known_file_manager(path):
        return True
    return bool(qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(path)))


class CloudMountTray:
    def __init__(self, qt: SimpleNamespace, refresh_interval: int = 10) -> None:
        self.qt = qt
        self.refresh_interval = max(refresh_interval, 2)
        self.app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        self.remote_menu = qt.QMenu()
        self.app_menu = qt.QMenu()
        self.tray = qt.QSystemTrayIcon(self._icon(), self.app)
        self.tray.setToolTip("Cloud Mount Manager")
        self.tray.setContextMenu(self.app_menu)
        self.tray.activated.connect(self._handle_activation)
        self.timer = qt.QTimer()
        self.timer.timeout.connect(self.rebuild_menus)

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

        self.rebuild_menus()
        self.tray.show()
        self.timer.start(self.refresh_interval * 1000)
        return int(self.app.exec() or 0)

    def _handle_activation(self, reason: Any) -> None:
        if reason != self.qt.QSystemTrayIcon.ActivationReason.Trigger:
            return
        self.rebuild_menus()
        self.remote_menu.popup(self.qt.QCursor.pos())

    def rebuild_menus(self) -> None:
        self.remote_menu.clear()
        self.app_menu.clear()
        remotes = core.load_remotes()
        mounted_names = [remote.display_name for remote in remotes if core.is_mounted(remote)]
        self.tray.setToolTip(_status_tooltip(remotes, mounted_names))

        if remotes:
            for remote in remotes:
                self._add_remote_menu(remote, self.remote_menu)
        else:
            action = self.remote_menu.addAction("No rclone remotes found")
            action.setEnabled(False)

        status = self.app_menu.addAction(_status_tooltip(remotes, mounted_names).replace("Cloud Mount Manager - ", ""))
        status.setEnabled(False)
        self.app_menu.addSeparator()
        self._add_action(self.app_menu, "Mount all", lambda: self._mount_all(remotes), enabled=bool(remotes))
        self._add_action(self.app_menu, "Unmount all", lambda: self._unmount_all(remotes), enabled=bool(remotes))
        self._add_action(self.app_menu, "Update status", self.rebuild_menus)
        self.app_menu.addSeparator()
        self._add_action(self.app_menu, "Quit", self.app.quit)

    def _add_remote_menu(self, remote: core.RemoteInfo, menu: Any) -> None:
        mounted = core.is_mounted(remote)
        submenu = menu.addMenu(_remote_title(remote, mounted))

        status = submenu.addAction(f"Path: {remote.mount_path}")
        status.setEnabled(False)

        if mounted:
            self._add_action(submenu, "Unmount", lambda: self._run_remote_action(remote, core.unmount_remote))
            self._add_action(submenu, "Restart mount", lambda: self._run_remote_action(remote, core.refresh_remote))
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
        self.rebuild_menus()

    def _mount_all(self, remotes: list[core.RemoteInfo]) -> None:
        mounted, failures = core.mount_all(remotes)
        if failures:
            self._notify("Mount all", "\n".join(_clean_message(item) for item in failures), success=False)
        elif mounted:
            self._notify("Mount all", "Mounted: " + ", ".join(mounted), success=True)
        else:
            self._notify("Mount all", "Nothing to mount.", success=True)
        self.rebuild_menus()

    def _unmount_all(self, remotes: list[core.RemoteInfo]) -> None:
        unmounted, failures = core.unmount_all(remotes)
        if failures:
            self._notify("Unmount all", "\n".join(_clean_message(item) for item in failures), success=False)
        elif unmounted:
            self._notify("Unmount all", "Unmounted: " + ", ".join(unmounted), success=True)
        else:
            self._notify("Unmount all", "Nothing to unmount.", success=True)
        self.rebuild_menus()

    def _open_folder(self, remote: core.RemoteInfo) -> None:
        if not os.path.isdir(remote.mount_path):
            self._notify("Open folder", "Mount the remote before opening its folder.", success=False)
            return
        if not _open_folder_default(self.qt, remote.mount_path):
            self._notify("Open folder", "Could not open the mount folder.", success=False)

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
