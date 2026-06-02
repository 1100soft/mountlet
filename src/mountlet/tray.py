#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import core
from .config_tools import setup_wizard
from .config_tools.shared import app_config_file, app_mounts_file, ensure_app_directories
from .settings import (
    AppSettings,
    MountSettings,
    ensure_default_config_files,
    load_app_settings,
    load_mount_settings,
    save_app_settings,
    save_mount_settings,
)


_DOLPHIN_MAIN_WINDOW_PATH = "/dolphin/Dolphin_1"
_dolphin_tab_target_cache: tuple[str, str] | None = None
_CARDINAL_RE = re.compile(r"=\s*(\d+)")
OPEN_FOLDER_BEHAVIORS: tuple[tuple[str, str], ...] = (
    ("current_desktop", "Current desktop window"),
    ("existing_window", "Any existing file manager window"),
    ("new_window", "New file manager window"),
    ("file-manager-service", "Desktop file manager service"),
    ("default", "System default"),
)
MOUNT_FLAG_OPTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Read-only", "Mount this remote without allowing writes.", ("--read-only",)),
    ("Allow other users", "Let other local users access the mount when FUSE permits it.", ("--allow-other",)),
)
REMOVED_MOUNT_FLAGS = {"--allow-non-empty"}


class TrayDependencyError(RuntimeError):
    pass


def _load_qt_bindings() -> SimpleNamespace:
    try:
        from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
        from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFrame,
            QFormLayout,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QStyle,
            QSystemTrayIcon,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise TrayDependencyError(
            "Tray support requires PySide6. Install it with:\n"
            '  pipx install "mountlet[tray]"\n'
            "or, for an existing pipx install:\n"
            "  pipx inject mountlet PySide6"
        ) from exc

    return SimpleNamespace(
        QAction=QAction,
        QApplication=QApplication,
        QCheckBox=QCheckBox,
        QComboBox=QComboBox,
        QCursor=QCursor,
        QDialog=QDialog,
        QDialogButtonBox=QDialogButtonBox,
        QDesktopServices=QDesktopServices,
        QFormLayout=QFormLayout,
        QFrame=QFrame,
        QGridLayout=QGridLayout,
        QHBoxLayout=QHBoxLayout,
        QIcon=QIcon,
        QLabel=QLabel,
        QLineEdit=QLineEdit,
        QMainWindow=QMainWindow,
        QMenu=QMenu,
        QMessageBox=QMessageBox,
        QObject=QObject,
        QPushButton=QPushButton,
        QScrollArea=QScrollArea,
        QSizePolicy=QSizePolicy,
        QStyle=QStyle,
        QSystemTrayIcon=QSystemTrayIcon,
        QTimer=QTimer,
        Qt=Qt,
        QUrl=QUrl,
        Signal=Signal,
        QVBoxLayout=QVBoxLayout,
        QWidget=QWidget,
    )


def _clean_message(message: str) -> str:
    return message.replace("[*]", "").replace("[!]", "").replace("[✓]", "").strip()


def _remote_title(remote: core.RemoteInfo, mounted: bool) -> str:
    marker = "Mounted" if mounted else "Unmounted"
    return f"{remote.display_name} - {marker}"


def _status_tooltip(remotes: list[core.RemoteInfo], mounted_names: list[str]) -> str:
    if not remotes:
        return "Mountlet - no rclone remotes"

    unmounted_count = len(remotes) - len(mounted_names)
    if not mounted_names:
        return f"Mountlet - 0 mounted, {unmounted_count} unmounted"

    names = ", ".join(mounted_names[:3])
    if len(mounted_names) > 3:
        names += f", +{len(mounted_names) - 3} more"
    return f"Mountlet - mounted: {names}"


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

    return _open_folder_in_dolphin_new_window(path)


def _open_folder_in_dolphin_new_window(path: str) -> bool:
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
    *,
    focus: bool = True,
) -> bool:
    interface = dbus.QDBusInterface(service, object_path, "org.kde.dolphin.MainWindow", dbus.bus)
    if not interface.isValid():
        return False
    reply = interface.call("openDirectories", [uri], False)
    if reply.errorName():
        return False
    if focus:
        interface.call("activateWindow", "")
    return True


def _open_folder_in_dolphin_window(
    dbus: SimpleNamespace | None,
    qdbus: str | None,
    service: str,
    object_path: str,
    uri: str,
    *,
    focus: bool = True,
) -> bool:
    if dbus and _open_folder_in_dolphin_window_with_qtdbus(dbus, service, object_path, uri, focus=focus):
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

    if focus:
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


def _open_folder_in_dolphin_tab(path: str, *, current_desktop: bool = True, focus: bool = True) -> bool:
    global _dolphin_tab_target_cache

    uri = _folder_uri(path)
    dbus = _qt_dbus_session()
    qdbus = _qdbus_binary()
    if not dbus and not qdbus:
        return False

    tried: set[tuple[str, str]] = set()
    if _dolphin_tab_target_cache:
        service, object_path = _dolphin_tab_target_cache
        tried.add((service, object_path))
        desktop = _x11_current_desktop() if current_desktop else None
        cache_is_usable = desktop is None or _dolphin_window_is_on_desktop(
            dbus,
            qdbus,
            service,
            object_path,
            desktop,
        )
        if cache_is_usable and _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri, focus=focus):
            return True
        _dolphin_tab_target_cache = None

    if current_desktop:
        for service, object_path in _dolphin_current_desktop_targets(dbus, qdbus, tried):
            tried.add((service, object_path))
            if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri, focus=focus):
                _dolphin_tab_target_cache = (service, object_path)
                return True

        if _x11_current_desktop() is not None:
            return False

    for service, object_path in _dolphin_fast_tab_targets(dbus):
        if (service, object_path) in tried:
            continue
        tried.add((service, object_path))
        if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri, focus=focus):
            _dolphin_tab_target_cache = (service, object_path)
            return True

    qdbus = qdbus or _qdbus_binary()
    if not qdbus:
        return False
    for service, object_path in _dolphin_slow_tab_targets(qdbus, tried):
        tried.add((service, object_path))
        if _open_folder_in_dolphin_window(dbus, qdbus, service, object_path, uri, focus=focus):
            _dolphin_tab_target_cache = (service, object_path)
            return True

    return False


def _open_folder_with_known_file_manager(path: str, *, behavior: str, focus: bool) -> bool:
    default_app = _default_directory_app().lower()
    if "dolphin" in default_app:
        if behavior == "new_window":
            return False
        if _open_folder_in_dolphin_tab(path, current_desktop=behavior == "current_desktop", focus=focus):
            return True
        if behavior == "current_desktop":
            return _open_folder_in_dolphin_new_window(path)
    return False


def _open_folder_default(qt: SimpleNamespace, path: str, strategy: str = "default") -> bool:
    settings = load_app_settings()
    behavior = settings.open_folder_behavior
    if strategy == "default":
        strategy = behavior
    if strategy == "file-manager-service" and _show_folder_with_file_manager(path):
        return True
    if strategy in {"current_desktop", "existing_window"} and _open_folder_with_known_file_manager(
        path,
        behavior=strategy,
        focus=settings.focus_file_manager,
    ):
        return True
    if strategy == "new_window" and _open_folder_in_dolphin_new_window(path):
        return True
    return bool(qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(path)))


def _absolute_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _path_relative_to_base(path: str | None, base_path: str) -> str:
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        return expanded
    try:
        if os.path.commonpath([_absolute_path(expanded), _absolute_path(base_path)]) == _absolute_path(base_path):
            return os.path.relpath(expanded, base_path)
    except ValueError:
        pass
    return expanded


def _muted_text_style(widget: Any) -> str:
    background = widget.palette().color(widget.backgroundRole())
    luminance = (0.2126 * background.red()) + (0.7152 * background.green()) + (0.0722 * background.blue())
    color = "#cbd5e1" if luminance < 128 else "#4b5563"
    return f"color: {color};"


class _ConfigDialogBase:
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        self.qt = qt
        self.dialog = qt.QDialog(parent)

    def exec(self) -> int:
        return int(self.dialog.exec() or 0)

    def _line(self, text: str | None, *, default: str | None = None) -> Any:
        field = self.qt.QLineEdit()
        field.setText(text or "")
        if default:
            field.setPlaceholderText(default)
            field.setToolTip(f"Leave blank to use the default: {default}")
        return field

    def _check(self, checked: bool) -> Any:
        field = self.qt.QCheckBox()
        field.setChecked(checked)
        return field

    def _combo(self, options: tuple[tuple[str, str], ...], current: str) -> Any:
        field = self.qt.QComboBox()
        selected_index = 0
        for index, (value, label) in enumerate(options):
            field.addItem(label, value)
            if value == current:
                selected_index = index
        field.setCurrentIndex(selected_index)
        return field

    def _buttons(self) -> Any:
        buttons = self.qt.QDialogButtonBox(
            self.qt.QDialogButtonBox.StandardButton.Save | self.qt.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.dialog.reject)
        return buttons

    def _save(self) -> None:
        raise NotImplementedError


class AppConfigDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.dialog.setWindowTitle("App settings")
        self.dialog.resize(520, 260)
        self.fields: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        frame = self.qt.QFrame()
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        self.fields = {
            "mount_base": self._line(app_settings.mount_base, default=core.DEFAULT_HOME_MOUNT),
            "auto_mount": self._check(app_settings.auto_mount),
            "auto_mount_delay": self._line(f"{app_settings.auto_mount_delay:g}"),
            "open_folder_behavior": self._combo(OPEN_FOLDER_BEHAVIORS, app_settings.open_folder_behavior),
            "focus_file_manager": self._check(app_settings.focus_file_manager),
        }
        form.addRow("Mount base", self.fields["mount_base"])
        form.addRow("Auto-mount by default", self.fields["auto_mount"])
        form.addRow("Auto-mount delay", self.fields["auto_mount_delay"])
        form.addRow("Open folder behavior", self.fields["open_folder_behavior"])
        form.addRow("Focus file manager", self.fields["focus_file_manager"])
        root.addWidget(frame)
        root.addWidget(self._buttons())

    def _save(self) -> None:
        try:
            delay = float(self.fields["auto_mount_delay"].text().strip() or "0")
        except ValueError:
            delay = 0.0

        save_app_settings(
            AppSettings(
                mount_base=self.fields["mount_base"].text().strip() or None,
                auto_mount=self.fields["auto_mount"].isChecked(),
                auto_mount_delay=max(delay, 0.0),
                open_folder_behavior=self.fields["open_folder_behavior"].currentData() or "current_desktop",
                focus_file_manager=self.fields["focus_file_manager"].isChecked(),
            )
        )
        self.dialog.accept()


class MountConfigDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, remote: core.RemoteInfo, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.remote = remote
        self.dialog.setWindowTitle(f"{remote.display_name} settings")
        self.dialog.resize(560, 260)
        self.fields: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        mount_settings = load_mount_settings().get(self.remote.name)
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        frame = self.qt.QFrame()
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        auto_mount = (
            mount_settings.auto_mount
            if mount_settings and mount_settings.auto_mount is not None
            else app_settings.auto_mount
        )
        mount_flags = mount_settings.mount_flags if mount_settings else []
        option_tokens = {token for _label, _tooltip, tokens in MOUNT_FLAG_OPTIONS for token in tokens}
        self._preserved_mount_flags = [
            flag
            for flag in mount_flags
            if flag not in option_tokens and flag not in REMOVED_MOUNT_FLAGS
        ]
        self.flag_fields: list[tuple[Any, tuple[str, ...]]] = []
        self._saved_enabled = mount_settings.enabled if mount_settings else True
        self._mount_base = core.BASE_MOUNT_DIR
        default_relative_path = _path_relative_to_base(self.remote.mount_path, self._mount_base)
        self.fields = {
            "auto_mount": self._check(bool(auto_mount)),
            "mount_path": self._line(
                _path_relative_to_base(mount_settings.mount_path if mount_settings else None, self._mount_base),
                default=default_relative_path,
            ),
        }

        path_row = self.qt.QWidget()
        path_layout = self.qt.QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(4)
        base_label = self.qt.QLabel(os.path.join(self._mount_base, ""))
        base_label.setStyleSheet(_muted_text_style(base_label))
        path_layout.addWidget(base_label)
        path_layout.addWidget(self.fields["mount_path"], 1)
        form.addRow("Mount path", path_row)

        options_frame = self.qt.QFrame()
        options_layout = self.qt.QVBoxLayout(options_frame)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(4)
        self.fields["auto_mount"].setText("Auto-mount")
        self.fields["auto_mount"].setToolTip("Mount this remote automatically when Mountlet starts.")
        options_layout.addWidget(self.fields["auto_mount"])
        for label, tooltip, tokens in MOUNT_FLAG_OPTIONS:
            field = self._check(all(token in mount_flags for token in tokens))
            field.setText(label)
            field.setToolTip(tooltip)
            self.flag_fields.append((field, tokens))
            options_layout.addWidget(field)
        if self._preserved_mount_flags:
            custom = self.qt.QLabel("Additional existing rclone flags will be preserved.")
            custom.setToolTip("Preserved flags: " + shlex.join(self._preserved_mount_flags))
            custom.setStyleSheet(_muted_text_style(custom))
            options_layout.addWidget(custom)
        form.addRow("Options", options_frame)

        root.addWidget(frame)
        root.addWidget(self._buttons())

    def _save(self) -> None:
        settings = load_mount_settings()
        settings[self.remote.name] = MountSettings(
            mount_path=self.fields["mount_path"].text().strip() or None,
            mount_flags=[
                flag
                for field, tokens in self.flag_fields
                if field.isChecked()
                for flag in tokens
            ]
            + self._preserved_mount_flags,
            auto_mount=self.fields["auto_mount"].isChecked(),
            enabled=self._saved_enabled,
        )
        save_mount_settings(settings)
        self.dialog.accept()


class MountletWindow:
    def __init__(self, tray_app: "CloudMountTray") -> None:
        self.tray_app = tray_app
        self.qt = tray_app.qt
        self._usage_cache: dict[str, core.StorageUsage] = {}
        self._usage_pending: set[str] = set()
        self._action_pending: set[str] = set()
        self._bridge = self._make_bridge()
        self._bridge.storage_ready.connect(self._handle_storage_ready)
        self._bridge.action_finished.connect(self._handle_action_finished)
        self._bridge.bulk_action_finished.connect(self._handle_bulk_action_finished)
        self.window = self.qt.QMainWindow()
        self.window.setWindowTitle("Mountlet")
        self.window.resize(720, 260)
        self._build_app_menu()

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            storage_ready = qt.Signal(str, object)
            action_finished = qt.Signal(str, bool, str)
            bulk_action_finished = qt.Signal(str, object, object)

        return Bridge()

    def _build_app_menu(self) -> None:
        app_menu = self.window.menuBar().addMenu("App")
        self.tray_app._add_action(app_menu, "Update status", self.refresh)
        app_menu.addSeparator()
        self.tray_app._add_action(app_menu, "Quit", self.tray_app.app.quit)

        mount_menu = self.window.menuBar().addMenu("Mount")
        self.tray_app._add_action(mount_menu, "Mount all", lambda: self._mount_all())
        self.tray_app._add_action(mount_menu, "Unmount all", lambda: self._unmount_all())

        config_menu = self.window.menuBar().addMenu("Config")
        self.tray_app._add_action(config_menu, "App settings", self._show_app_config_editor)
        config_menu.addSeparator()
        self.tray_app._add_action(config_menu, "Open app config file", lambda: self._open_text_config(app_config_file()))
        self.tray_app._add_action(config_menu, "Open mount config file", lambda: self._open_text_config(app_mounts_file()))

    def is_visible(self) -> bool:
        return bool(self.window.isVisible())

    def show(self) -> None:
        self.refresh()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def refresh(self) -> None:
        remotes = core.load_remotes()
        mounted_by_name = {remote.name: core.is_mounted(remote) for remote in remotes}

        root = self.qt.QWidget()
        outer = self.qt.QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        scroll = self.qt.QScrollArea()
        scroll.setWidgetResizable(True)
        container = self.qt.QWidget()
        rows = self.qt.QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        if remotes:
            for remote in remotes:
                rows.addWidget(self._remote_row(remote, mounted_by_name[remote.name]))
                self._schedule_storage_load(remote)
            rows.addStretch(1)
        else:
            rows.addWidget(self.qt.QLabel("No rclone remotes found"))
        scroll.setWidget(container)
        outer.addWidget(scroll)

        self.window.setCentralWidget(root)
        self._fit_to_remote_count(len(remotes))

    def _remote_row(self, remote: core.RemoteInfo, mounted: bool) -> Any:
        usage = self._usage_cache.get(remote.name)
        checking_usage = usage is None
        if usage is None:
            usage = core.StorageUsage("Checking...")
        action_pending = remote.name in self._action_pending

        frame = self.qt.QFrame()
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        frame.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.PointingHandCursor))
        frame.mouseReleaseEvent = lambda event, selected=remote: self._open_folder(selected)
        layout = self.qt.QGridLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)

        title = self.qt.QLabel(remote.display_name)
        title.setSizePolicy(self.qt.QSizePolicy.Policy.Expanding, self.qt.QSizePolicy.Policy.Preferred)
        path = self.qt.QLabel(remote.mount_path)
        path.setStyleSheet(_muted_text_style(path))
        path.setTextInteractionFlags(path.textInteractionFlags())

        usage_label = self.qt.QLabel(usage.text)
        usage_indicator = self._usage_indicator(usage, checking_usage=checking_usage)

        toggle_action = core.unmount_remote if mounted else core.mount_remote
        toggle = self.qt.QCheckBox("Mounted" if mounted else "Mount")
        toggle.setChecked(mounted)
        toggle.setEnabled(not action_pending)
        toggle.stateChanged.connect(lambda state, selected=remote, action=toggle_action: self._run_remote_action(selected, action))
        working = self.qt.QLabel("Working..." if action_pending else "")
        config_button = self._button("Config", lambda: self._show_mount_config_editor(remote), enabled=not action_pending)

        layout.addWidget(toggle, 0, 0, 2, 1)
        layout.addWidget(title, 0, 1)
        layout.addWidget(path, 1, 1)
        layout.addWidget(usage_indicator, 0, 2)
        layout.addWidget(usage_label, 1, 2)
        layout.addWidget(working, 0, 3)
        layout.addWidget(config_button, 0, 4, 2, 1)
        return frame

    def _usage_indicator(self, usage: core.StorageUsage, *, checking_usage: bool) -> Any:
        indicator = self.qt.QFrame()
        indicator.setFixedSize(116, 8)
        if usage.percent is None:
            color = "#9ca3af" if checking_usage else "#6b7280"
            indicator.setStyleSheet(f"background: {color}; border-radius: 4px;")
            return indicator

        pct = max(0, min(usage.percent, 100))
        if pct >= 90:
            fill = "#dc2626"
        elif pct >= 75:
            fill = "#d97706"
        else:
            fill = "#2563eb"
        stop = pct / 100
        indicator.setStyleSheet(
            "border-radius: 4px;"
            "background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {fill}, stop:{stop:.3f} {fill}, "
            f"stop:{stop:.3f} #d1d5db, stop:1 #d1d5db"
            ");"
        )
        return indicator

    def _fit_to_remote_count(self, remote_count: int) -> None:
        screen = self.window.screen() or self.qt.QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 720
        target_height = 92 + max(remote_count, 1) * 58
        capped_height = min(max(target_height, 150), max(220, available_height - 96))
        self.window.resize(self.window.width(), capped_height)

    def _button(self, label: str, callback: Any, *, enabled: bool = True) -> Any:
        button = self.qt.QPushButton(label)
        button.setEnabled(enabled)
        button.clicked.connect(lambda checked=False: callback())
        return button

    def _run_remote_action(self, remote: core.RemoteInfo, action: Any) -> None:
        if remote.name in self._action_pending:
            return
        self._action_pending.add(remote.name)
        self.refresh()

        def worker() -> None:
            success, message = action(remote)
            self._bridge.action_finished.emit(remote.name, success, message)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_action_finished(self, remote_name: str, success: bool, message: str) -> None:
        self._action_pending.discard(remote_name)
        self._usage_cache.pop(remote_name, None)
        self.tray_app._notify("Mountlet", _clean_message(message), success=success)
        self.tray_app.rebuild_menus()
        self.refresh()

    def _mount_all(self) -> None:
        self._run_bulk_action("Mount all", core.mount_all)

    def _unmount_all(self) -> None:
        self._run_bulk_action("Unmount all", core.unmount_all)

    def _run_bulk_action(self, title: str, action: Any) -> None:
        remotes = core.load_remotes()
        if not remotes:
            return
        for remote in remotes:
            self._action_pending.add(remote.name)
        self.refresh()

        def worker() -> None:
            completed, failures = action(remotes)
            self._bridge.bulk_action_finished.emit(title, completed, failures)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_bulk_action_finished(self, title: str, completed: object, failures: object) -> None:
        self._action_pending.clear()
        self._usage_cache.clear()
        if isinstance(completed, list) and isinstance(failures, list):
            if failures:
                self.tray_app._notify(title, "\n".join(_clean_message(item) for item in failures), success=False)
            elif completed:
                verb = "Unmounted" if title == "Unmount all" else "Mounted"
                self.tray_app._notify(title, f"{verb}: " + ", ".join(completed), success=True)
            else:
                self.tray_app._notify(title, "Nothing to do.", success=True)
        self.tray_app.rebuild_menus()
        self.refresh()

    def _open_folder(self, remote: core.RemoteInfo) -> None:
        self.tray_app._open_folder(remote)

    def _show_app_config_editor(self) -> None:
        old_remotes = core.load_remotes()
        mounted_before = self._mounted_remote_names(old_remotes)
        old_base = core.BASE_MOUNT_DIR
        dialog = AppConfigDialog(self.qt, self.window)
        if dialog.exec() == int(self.qt.QDialog.DialogCode.Accepted):
            new_base, _note = core.ensure_base_mount_dir()
            changes = self._remount_changes(old_remotes, mounted_before)
            base_changed = _absolute_path(old_base) != _absolute_path(new_base)
            self._usage_cache.clear()
            self.tray_app.rebuild_menus()
            self.refresh()
            self._ask_remount_for_config_changes(changes, old_base=old_base if base_changed else None)

    def _show_mount_config_editor(self, remote: core.RemoteInfo) -> None:
        old_remotes = core.load_remotes()
        mounted_before = self._mounted_remote_names(old_remotes)
        dialog = MountConfigDialog(self.qt, remote, self.window)
        if dialog.exec() == int(self.qt.QDialog.DialogCode.Accepted):
            core.ensure_base_mount_dir()
            changes = self._remount_changes(old_remotes, mounted_before)
            self._usage_cache.clear()
            self.tray_app.rebuild_menus()
            self.refresh()
            self._ask_remount_for_config_changes(changes)

    def _mounted_remote_names(self, remotes: list[core.RemoteInfo]) -> set[str]:
        return {remote.name for remote in remotes if core.is_mounted(remote)}

    def _remount_changes(
        self,
        old_remotes: list[core.RemoteInfo],
        mounted_before: set[str],
    ) -> list[tuple[core.RemoteInfo, core.RemoteInfo]]:
        old_by_name = {remote.name: remote for remote in old_remotes}
        changes: list[tuple[core.RemoteInfo, core.RemoteInfo]] = []
        for new_remote in core.load_remotes():
            old_remote = old_by_name.get(new_remote.name)
            if not old_remote or old_remote.name not in mounted_before:
                continue
            path_changed = _absolute_path(old_remote.mount_path) != _absolute_path(new_remote.mount_path)
            flags_changed = old_remote.flags != new_remote.flags
            if path_changed or flags_changed:
                changes.append((old_remote, new_remote))
        return changes

    def _ask_remount_for_config_changes(
        self,
        changes: list[tuple[core.RemoteInfo, core.RemoteInfo]],
        *,
        old_base: str | None = None,
    ) -> None:
        if not changes:
            if old_base:
                cleanup_message = self._cleanup_old_mount_base(old_base)
                if cleanup_message:
                    self.tray_app._notify("Mount folder cleanup", cleanup_message, success=False)
            return
        names = ", ".join(new_remote.display_name for _old_remote, new_remote in changes[:3])
        if len(changes) > 3:
            names += f", and {len(changes) - 3} more"
        reply = self.qt.QMessageBox.question(
            self.window,
            "Remount now?",
            "Settings changed for mounted remotes: "
            f"{names}.\n\nRemount them now and remove old empty mount folders?",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.Yes,
        )
        if reply == self.qt.QMessageBox.StandardButton.Yes:
            self._remount_changed_remotes(changes, old_base=old_base)

    def _remount_changed_remotes(
        self,
        changes: list[tuple[core.RemoteInfo, core.RemoteInfo]],
        *,
        old_base: str | None = None,
    ) -> None:
        if not changes:
            return
        for _old_remote, new_remote in changes:
            self._action_pending.add(new_remote.name)
        self.refresh()

        def worker() -> None:
            completed: list[str] = []
            failures: list[str] = []
            for old_remote, new_remote in changes:
                if core.is_mounted(old_remote):
                    success, message = core.unmount_remote(old_remote)
                    if not success or not core.wait_for(old_remote, False):
                        failures.append(message)
                        continue
                self._remove_empty_mount_folder(old_remote.mount_path, stop_at=old_base)
                success, message = core.mount_remote(new_remote)
                if success:
                    completed.append(new_remote.name)
                else:
                    failures.append(message)
            if old_base:
                cleanup_message = self._cleanup_old_mount_base(old_base)
                if cleanup_message:
                    failures.append(cleanup_message)
            self._bridge.bulk_action_finished.emit("Remount", completed, failures)

        threading.Thread(target=worker, daemon=True).start()

    def _remove_empty_mount_folder(self, path: str, *, stop_at: str | None = None) -> None:
        current = Path(os.path.expanduser(path))
        if stop_at is None:
            try:
                current.rmdir()
            except OSError:
                return
            return

        stop = Path(os.path.expanduser(stop_at)).resolve()
        if not current.exists():
            current = current.parent
        try:
            if os.path.commonpath([str(current.resolve()), str(stop)]) != str(stop):
                return
        except ValueError:
            return
        while current.resolve() != stop:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _cleanup_old_mount_base(self, old_base: str) -> str | None:
        old_base_path = Path(os.path.expanduser(old_base))
        if not old_base_path.exists():
            return None
        try:
            old_base_path.rmdir()
        except OSError:
            return (
                f"{old_base_path} is not empty. Empty it when you are ready so Mountlet can manage "
                "mount folders automatically."
            )
        return None

    def _open_text_config(self, path: Path) -> None:
        ensure_default_config_files()
        if not self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))):
            self.tray_app._notify("Open config", f"Could not open {path}.", success=False)

    def _schedule_storage_load(self, remote: core.RemoteInfo) -> None:
        if remote.name in self._usage_cache or remote.name in self._usage_pending:
            return
        self._usage_pending.add(remote.name)

        def worker() -> None:
            usage = core.get_storage_usage_details(remote)
            self._bridge.storage_ready.emit(remote.name, usage)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_storage_ready(self, remote_name: str, usage: core.StorageUsage) -> None:
        self._usage_pending.discard(remote_name)
        self._usage_cache[remote_name] = usage
        if self.is_visible():
            self.refresh()


class CloudMountTray:
    def __init__(self, qt: SimpleNamespace, refresh_interval: int = 10) -> None:
        self.qt = qt
        self.refresh_interval = max(refresh_interval, 2)
        self.app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        self.remote_menu = qt.QMenu()
        self.app_menu = qt.QMenu()
        self.main_window = MountletWindow(self)
        self.tray = qt.QSystemTrayIcon(self._icon(), self.app)
        self.tray.setToolTip("Mountlet")
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
            print("    Use the terminal menu instead: mountlet", file=sys.stderr)
            return 1

        self.rebuild_menus()
        self.tray.show()
        self.timer.start(self.refresh_interval * 1000)
        self._schedule_auto_mounts()
        return int(self.app.exec() or 0)

    def _handle_activation(self, reason: Any) -> None:
        if reason != self.qt.QSystemTrayIcon.ActivationReason.Trigger:
            return
        self.rebuild_menus()
        self.main_window.show()

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

        status = self.app_menu.addAction(_status_tooltip(remotes, mounted_names).replace("Mountlet - ", ""))
        status.setEnabled(False)
        self.app_menu.addSeparator()
        self._add_action(self.app_menu, "Mount all", lambda: self._mount_all(remotes), enabled=bool(remotes))
        self._add_action(self.app_menu, "Unmount all", lambda: self._unmount_all(remotes), enabled=bool(remotes))
        self._add_action(self.app_menu, "Update status", self.rebuild_menus)
        self._add_action(self.app_menu, "App settings", self.main_window._show_app_config_editor)
        self._add_action(self.app_menu, "Open app config file", lambda: self.main_window._open_text_config(app_config_file()))
        self._add_action(self.app_menu, "Open mount config file", lambda: self.main_window._open_text_config(app_mounts_file()))
        self.app_menu.addSeparator()
        self._add_action(self.app_menu, "Quit", self.app.quit)

        if self.main_window.is_visible():
            self.main_window.refresh()

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
        self._add_action(submenu, "Settings", lambda: self.main_window._show_mount_config_editor(remote))

    def _add_action(self, menu: Any, label: str, callback: Any, *, enabled: bool = True) -> Any:
        action = self.qt.QAction(label, menu)
        action.setEnabled(enabled)
        action.triggered.connect(lambda checked=False: callback())
        menu.addAction(action)
        return action

    def _run_remote_action(self, remote: core.RemoteInfo, action: Any) -> None:
        success, message = action(remote)
        self._notify("Mountlet", _clean_message(message), success=success)
        self.rebuild_menus()

    def _mount_all(self, remotes: list[core.RemoteInfo]) -> None:
        mounted, failures = core.mount_all(remotes)
        self._report_mount_results("Mount all", mounted, failures)
        self.rebuild_menus()

    def _schedule_auto_mounts(self) -> None:
        remotes = [remote for remote in core.load_remotes() if remote.auto_mount and not core.is_mounted(remote)]
        if not remotes:
            return
        delay_ms = int(load_app_settings().auto_mount_delay * 1000)
        self.qt.QTimer.singleShot(delay_ms, lambda: self._auto_mount(remotes))

    def _auto_mount(self, remotes: list[core.RemoteInfo]) -> None:
        mounted, failures = core.mount_all(remotes)
        self._report_mount_results("Auto-mount", mounted, failures)
        self.rebuild_menus()

    def _report_mount_results(self, title: str, mounted: list[str], failures: list[str]) -> None:
        if failures:
            self._notify(title, "\n".join(_clean_message(item) for item in failures), success=False)
        elif mounted:
            self._notify(title, "Mounted: " + ", ".join(mounted), success=True)
        else:
            self._notify(title, "Nothing to mount.", success=True)

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
    parser = argparse.ArgumentParser(description="Run the Mountlet desktop tray app.")
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
        print("    Use the terminal menu instead: mountlet", file=sys.stderr)
        return 1

    try:
        qt = _load_qt_bindings()
    except TrayDependencyError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    ensure_app_directories()
    ensure_default_config_files()
    core.ensure_base_mount_dir()
    return CloudMountTray(qt, refresh_interval=args.refresh_interval).run()


if __name__ == "__main__":
    raise SystemExit(main())
