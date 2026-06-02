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
from importlib.resources import files
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
    set_start_at_login,
)


_DOLPHIN_MAIN_WINDOW_PATH = "/dolphin/Dolphin_1"
_dolphin_tab_target_cache: tuple[str, str] | None = None
_file_manager_label_cache: str | None = None
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
RCLONE_FIELD_TOOLTIPS = {
    "description": "Optional note stored in rclone.conf. Mountlet does not use this value.",
    "root_folder_id": "Limit this Drive remote to one Google Drive folder ID. rclone uses it when accessing the remote.",
    "team_drive": "Google shared drive ID. rclone uses it when this remote points at a shared drive.",
    "shared_with_me": "Show files shared with you. rclone uses this as a Drive remote setting.",
    "scope": "Google Drive permission scope. Changing it can require re-authentication.",
    "drive_id": "OneDrive drive ID. rclone uses it to choose the mapped drive.",
    "drive_type": "OneDrive drive type. rclone uses it with the drive ID.",
    "region": "Provider region. rclone uses it for providers that require a region.",
    "url": "WebDAV server URL. rclone uses it as the remote endpoint.",
    "vendor": "WebDAV vendor type. rclone uses it to choose provider-specific behavior.",
    "provider": "Storage provider name. rclone uses it for provider-specific behavior.",
    "env_auth": "Use provider credentials from the environment instead of values in rclone.conf.",
    "endpoint": "Provider endpoint URL. rclone uses it for S3-compatible services.",
    "acl": "Default access-control setting used by the remote provider.",
    "storage_class": "Default storage class used by the remote provider.",
}
RCLONE_BOOLEAN_FIELDS = {"shared_with_me", "env_auth"}
RCLONE_SELECT_FIELDS = {
    "scope": (
        ("drive", "Full Drive access"),
        ("drive.readonly", "Read-only Drive access"),
        ("drive.file", "Files created or opened by rclone"),
        ("drive.appfolder", "Rclone application data folder"),
        ("drive.metadata.readonly", "Read-only file names and metadata"),
    ),
    "drive_type": (
        ("personal", "Personal"),
        ("business", "Business"),
        ("documentLibrary", "SharePoint document library"),
    ),
}
REMOVED_MOUNT_FLAGS = {"--allow-non-empty"}
LOW_SPACE_BYTES = 100 * 1024 * 1024
FUSE_CONFIG_PATH = Path("/etc/fuse.conf")


class TrayDependencyError(RuntimeError):
    pass


def _packaged_icon_path() -> str | None:
    try:
        asset = files("mountlet").joinpath("assets/icon.png")
        if asset.is_file():
            return str(asset)
    except Exception:
        pass
    fallback = Path(__file__).resolve().parents[2] / "icon.png"
    return str(fallback) if fallback.is_file() else None


def _load_qt_bindings() -> SimpleNamespace:
    try:
        from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
        from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QIcon, QPainter
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
            QProgressBar,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QStyle,
            QSystemTrayIcon,
            QToolTip,
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
        QColor=QColor,
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
        QPainter=QPainter,
        QProgressBar=QProgressBar,
        QPushButton=QPushButton,
        QScrollArea=QScrollArea,
        QSizePolicy=QSizePolicy,
        QStyle=QStyle,
        QSystemTrayIcon=QSystemTrayIcon,
        QTimer=QTimer,
        QToolTip=QToolTip,
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


def _file_manager_label() -> str:
    global _file_manager_label_cache
    if _file_manager_label_cache is not None:
        return _file_manager_label_cache
    app = _default_directory_app()
    if not app:
        _file_manager_label_cache = "the file manager"
        return _file_manager_label_cache
    name = app.rsplit("/", 1)[-1].removesuffix(".desktop")
    name = re.sub(r"^(org|com|net)\.", "", name)
    name = name.rsplit(".", 1)[-1]
    _file_manager_label_cache = name.replace("-", " ").replace("_", " ").title() or "the file manager"
    return _file_manager_label_cache


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


def _open_text_file_focused(path: Path) -> bool:
    if platform.system() != "Linux":
        return False
    path_text = str(path)
    commands: list[list[str]] = []
    for editor in ("kate", "kwrite", "gedit", "xed", "mousepad"):
        editor_path = shutil.which(editor)
        if editor_path:
            commands.append([editor_path, path_text])
    for command in commands:
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


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


def _field_label(key: str) -> str:
    return key.replace("_", " ").title()


def _rclone_field_tooltip(key: str) -> str:
    return RCLONE_FIELD_TOOLTIPS.get(
        key,
        "Safely editable rclone setting. rclone uses this value when accessing the remote.",
    )


def _config_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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

    def _editable_config_combo(self, options: tuple[tuple[str, str], ...], current: str) -> Any:
        field = self.qt.QComboBox()
        field.setEditable(True)
        field.addItem("", "")
        selected_index = 0
        current = current.strip()
        for index, (value, label) in enumerate(options, start=1):
            field.addItem(value, value)
            field.setItemData(index, label, self.qt.Qt.ItemDataRole.ToolTipRole)
            if value == current:
                selected_index = index
        if current and selected_index == 0:
            field.addItem(current, current)
            selected_index = field.count() - 1
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
        self.dialog.resize(460, 210)
        self.fields: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        frame = self.qt.QFrame()
        frame.setObjectName("remoteRow")
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        self.fields = {
            "mount_base": self._line(app_settings.mount_base, default=core.DEFAULT_HOME_MOUNT),
            "auto_mount": self._check(app_settings.auto_mount),
            "auto_mount_delay": self._line(f"{app_settings.auto_mount_delay:g}"),
            "start_at_login": self._check(app_settings.start_at_login),
            "open_folder_behavior": self._combo(OPEN_FOLDER_BEHAVIORS, app_settings.open_folder_behavior),
            "focus_file_manager": self._check(app_settings.focus_file_manager),
        }
        self.fields["auto_mount"].setText("Auto-mount by default")
        self.fields["auto_mount"].setToolTip("Mount remotes automatically unless a remote overrides it.")
        self.fields["start_at_login"].setText("Start Mountlet when I log in")
        self.fields["start_at_login"].setToolTip("Create a Linux desktop autostart entry for Mountlet.")
        self.fields["focus_file_manager"].setText("Focus file manager")
        self.fields["focus_file_manager"].setToolTip("Bring the file manager forward after opening a mount folder.")
        form.addRow(self.fields["start_at_login"])
        form.addRow(self.fields["auto_mount"])
        form.addRow("Default mount folder", self.fields["mount_base"])
        form.addRow("Open folders", self.fields["open_folder_behavior"])
        form.addRow(self.fields["focus_file_manager"])
        form.addRow("Auto-mount delay", self.fields["auto_mount_delay"])
        root.addWidget(frame)
        root.addWidget(self._buttons())
        self.dialog.adjustSize()

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
                start_at_login=self.fields["start_at_login"].isChecked(),
                open_folder_behavior=self.fields["open_folder_behavior"].currentData() or "current_desktop",
                focus_file_manager=self.fields["focus_file_manager"].isChecked(),
            )
        )
        set_start_at_login(self.fields["start_at_login"].isChecked())
        self.dialog.accept()


class MountConfigDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, remote: core.RemoteInfo, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.remote = remote
        self.dialog.setWindowTitle(f"{remote.display_name} settings")
        self.dialog.resize(520, 220)
        self.fields: dict[str, Any] = {}
        self.rclone_fields: dict[str, tuple[str, Any]] = {}
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        mount_settings = load_mount_settings().get(self.remote.name)
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

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
        self.fields["mount_path"].setToolTip(
            "Local folder name for this remote. Leave blank to use Mountlet's default name."
        )
        form.addRow("Local folder name", path_row)

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

        rclone_fields = core.editable_rclone_fields(self.remote)
        if rclone_fields:
            rclone_frame = self.qt.QFrame()
            rclone_form = self.qt.QFormLayout(rclone_frame)
            for key, value in rclone_fields.items():
                kind, field = self._rclone_config_field(key, value)
                field.setToolTip(f"{_rclone_field_tooltip(key)} Leave blank to remove this optional value.")
                self.rclone_fields[key] = (kind, field)
                rclone_form.addRow(_field_label(key), field)
            form.addRow("Advanced rclone", rclone_frame)

        root.addWidget(frame)
        root.addWidget(self._buttons())
        self.dialog.adjustSize()

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
        if self.rclone_fields:
            core.save_rclone_fields(
                self.remote.name,
                {key: self._rclone_config_value(kind, field) for key, (kind, field) in self.rclone_fields.items()},
            )
        self.dialog.accept()

    def _rclone_config_field(self, key: str, value: str) -> tuple[str, Any]:
        if key in RCLONE_BOOLEAN_FIELDS:
            return "bool", self._check(_config_bool(value))
        if key in RCLONE_SELECT_FIELDS:
            return "combo", self._editable_config_combo(RCLONE_SELECT_FIELDS[key], value)
        return "text", self._line(value)

    def _rclone_config_value(self, kind: str, field: Any) -> str:
        if kind == "bool":
            return "true" if field.isChecked() else ""
        if kind == "combo":
            return field.currentText().strip()
        return field.text().strip()


class MountletWindow:
    def __init__(self, tray_app: "CloudMountTray") -> None:
        self.tray_app = tray_app
        self.qt = tray_app.qt
        self._usage_cache: dict[str, core.StorageUsage] = {}
        self._usage_pending: set[str] = set()
        self._action_pending: set[str] = set()
        self._row_widgets: dict[str, SimpleNamespace] = {}
        self._current_remote_names: list[str] = []
        self._name_column_width = 160
        self._refresh_pending = False
        self._bridge = self._make_bridge()
        self._bridge.storage_ready.connect(self._handle_storage_ready)
        self._bridge.action_finished.connect(self._handle_action_finished)
        self._bridge.bulk_action_finished.connect(self._handle_bulk_action_finished)
        self.window = self.qt.QMainWindow()
        self.window.setWindowTitle("Mountlet")
        self.window.setWindowIcon(self.tray_app.icon)
        self._make_tray_owned_window()
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
        self.tray_app._add_action(app_menu, "Quit", self.tray_app.request_quit)

        mount_menu = self.window.menuBar().addMenu("Mount")
        self.tray_app._add_action(mount_menu, "Mount all", lambda: self._mount_all())
        self.tray_app._add_action(mount_menu, "Unmount all", lambda: self._unmount_all())

        config_menu = self.window.menuBar().addMenu("Config")
        self.tray_app._add_action(config_menu, "App settings", self._show_app_config_editor)
        config_menu.addSeparator()
        self.tray_app._add_action(config_menu, "Open app config file", self._open_app_config_file)
        self.tray_app._add_action(config_menu, "Open mount config file", self._open_mount_config_file)
        config_menu.addSeparator()
        self.tray_app._add_action(config_menu, "Open rclone config file", self._open_rclone_config_file)
        self.tray_app._add_action(config_menu, "Open FUSE config file", self._open_fuse_config_file)

    def is_visible(self) -> bool:
        return bool(self.window.isVisible())

    def show(self) -> None:
        if self._tray_is_quitting():
            return
        was_visible = self.is_visible()
        self.refresh()
        if not was_visible:
            self._position_near_tray()
        self._focus_window()

    def _make_tray_owned_window(self) -> None:
        try:
            self.window.setWindowFlag(self.qt.Qt.WindowType.Tool, True)
        except Exception:
            return

    def _focus_window(self) -> None:
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _position_near_tray(self) -> None:
        try:
            tray_geometry = self.tray_app.tray.geometry()
            anchor = tray_geometry.center() if tray_geometry.isValid() else self.qt.QCursor.pos()
            screen = self.qt.QApplication.screenAt(anchor) or self.qt.QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            size = self.window.sizeHint()
            if not size.isValid():
                size = self.window.size()
            width = size.width()
            height = size.height()
            max_x = max(available.left(), available.right() - width)
            max_y = max(available.top(), available.bottom() - height)
            x = min(max(anchor.x() - (width // 2), available.left()), max_x)
            if anchor.y() > available.center().y():
                y = anchor.y() - height - 8
            else:
                y = anchor.y() + 8
            y = min(max(y, available.top()), max_y)
            self.window.move(x, y)
        except Exception:
            return

    def refresh(self) -> None:
        if self._tray_is_quitting():
            return
        self._refresh_pending = False
        remotes = core.load_remotes()
        mounted_by_name = {remote.name: core.is_mounted(remote) for remote in remotes}
        remote_names = [remote.name for remote in remotes]
        name_width = self._remote_name_width(remotes)
        if self._current_remote_names == remote_names and self._row_widgets:
            self._name_column_width = name_width
            for remote in remotes:
                self._update_remote_row(remote, mounted_by_name[remote.name])
                if mounted_by_name[remote.name]:
                    self._schedule_storage_load(remote)
            return

        root = self.qt.QWidget()
        outer = self.qt.QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        scroll = self.qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = self.qt.QWidget()
        rows = self.qt.QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self._row_widgets = {}
        self._current_remote_names = remote_names
        self._name_column_width = name_width
        if remotes:
            for remote in remotes:
                rows.addWidget(self._remote_row(remote, mounted_by_name[remote.name]))
                if mounted_by_name[remote.name]:
                    self._schedule_storage_load(remote)
        else:
            rows.addWidget(self.qt.QLabel("No rclone remotes found"))
        scroll.setWidget(container)
        outer.addWidget(scroll)

        self.window.setCentralWidget(root)
        self._fit_to_content(root, scroll, container)

    def _request_refresh(self) -> None:
        if self._tray_is_quitting():
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.qt.QTimer.singleShot(25, self.refresh)

    def _remote_row(self, remote: core.RemoteInfo, mounted: bool) -> Any:
        usage = self._row_usage(remote, mounted)
        checking_usage = mounted and remote.name not in self._usage_cache
        action_pending = remote.name in self._action_pending
        open_tooltip = f"Open {remote.display_name} in {_file_manager_label()}"
        title_tooltip = f"{open_tooltip}\n{remote.mount_path}" if mounted else remote.mount_path

        frame = self.qt.QFrame()
        frame.setObjectName("remoteRow")
        frame.setProperty("mounted", mounted)
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        frame.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.PointingHandCursor))
        frame.setToolTip(open_tooltip)
        frame.mouseReleaseEvent = lambda event, row=frame, selected=remote: self._handle_remote_row_click(event, row, selected)
        frame.enterEvent = lambda event, row=frame, tooltip=open_tooltip: self._highlight_remote_row(
            row,
            highlighted=True,
            tooltip=tooltip,
        )
        frame.leaveEvent = lambda event, row=frame: self._highlight_remote_row(row, highlighted=False)
        frame.setStyleSheet(self._remote_row_style(frame, highlighted=False))
        layout = self.qt.QGridLayout(frame)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(0)
        layout.setColumnMinimumWidth(0, 50)
        layout.setColumnMinimumWidth(2, 126)
        layout.setColumnMinimumWidth(3, 96)
        layout.setColumnMinimumWidth(4, 36)
        layout.setColumnStretch(1, 1)

        title = self.qt.QLabel(self._display_remote_name(remote))
        title.setToolTip(title_tooltip)
        title.setFixedWidth(self._name_column_width)
        title.setSizePolicy(self.qt.QSizePolicy.Policy.Expanding, self.qt.QSizePolicy.Policy.Preferred)
        title.enterEvent = lambda event, widget=title, tooltip=title_tooltip: self._show_immediate_tooltip(widget, tooltip)
        if not mounted:
            title.setEnabled(False)

        usage_indicator = self._usage_indicator(usage, checking_usage=checking_usage)
        if not mounted:
            usage_indicator.setEnabled(False)

        toggle = self._switch()
        toggle.setProperty("rowControl", True)
        toggle.setChecked(mounted)
        toggle.setEnabled(not action_pending)
        toggle_tooltip = f"Unmount {remote.display_name}" if mounted else f"Mount {remote.display_name}"
        toggle.setToolTip(toggle_tooltip)
        toggle.enterEvent = lambda event, widget=toggle, tooltip=toggle_tooltip: self._show_immediate_tooltip(widget, tooltip)
        toggle.stateChanged.connect(
            lambda state, remote_name=remote.name: self._run_switch_action(remote_name, bool(state))
        )
        status = self.qt.QLabel()
        status.setFixedWidth(120)
        self._set_status_text(status, usage, action_pending=action_pending)
        config_button = self._icon_button("⚙", lambda: self._show_mount_config_editor(remote), enabled=not action_pending)
        config_button.setProperty("rowControl", True)
        config_tooltip = f"Configure {remote.display_name}"
        config_button.setToolTip(config_tooltip)
        config_button.enterEvent = lambda event, widget=config_button, tooltip=config_tooltip: self._show_immediate_tooltip(
            widget,
            tooltip,
        )

        layout.addWidget(toggle, 0, 0)
        layout.addWidget(title, 0, 1)
        layout.addWidget(usage_indicator, 0, 2)
        layout.addWidget(status, 0, 3)
        layout.addWidget(config_button, 0, 4)
        self._row_widgets[remote.name] = SimpleNamespace(
            frame=frame,
            title=title,
            usage_indicator=usage_indicator,
            toggle=toggle,
            status=status,
            config_button=config_button,
        )
        return frame

    def _update_remote_row(self, remote: core.RemoteInfo, mounted: bool) -> None:
        row = self._row_widgets.get(remote.name)
        if not row:
            return
        usage = self._row_usage(remote, mounted)
        checking_usage = mounted and remote.name not in self._usage_cache
        action_pending = remote.name in self._action_pending
        open_tooltip = f"Open {remote.display_name} in {_file_manager_label()}"
        title_tooltip = f"{open_tooltip}\n{remote.mount_path}" if mounted else remote.mount_path

        row.frame.setProperty("mounted", mounted)
        row.frame.setToolTip(open_tooltip)
        row.frame.mouseReleaseEvent = lambda event, frame=row.frame, selected=remote: self._handle_remote_row_click(
            event,
            frame,
            selected,
        )
        row.frame.enterEvent = lambda event, frame=row.frame, tooltip=open_tooltip: self._highlight_remote_row(
            frame,
            highlighted=True,
            tooltip=tooltip,
        )
        row.frame.setStyleSheet(self._remote_row_style(row.frame, highlighted=False))

        row.title.setText(self._display_remote_name(remote))
        row.title.setToolTip(title_tooltip)
        row.title.setFixedWidth(self._name_column_width)
        row.title.setEnabled(mounted)
        row.title.enterEvent = lambda event, widget=row.title, tooltip=title_tooltip: self._show_immediate_tooltip(
            widget,
            tooltip,
        )

        row.usage_indicator.setEnabled(mounted)
        self._apply_usage_indicator(row.usage_indicator, usage, checking_usage=checking_usage)

        row.toggle.blockSignals(True)
        row.toggle.setChecked(mounted)
        row.toggle.blockSignals(False)
        row.toggle.setEnabled(not action_pending)
        toggle_tooltip = f"Unmount {remote.display_name}" if mounted else f"Mount {remote.display_name}"
        row.toggle.setToolTip(toggle_tooltip)
        row.toggle.enterEvent = lambda event, widget=row.toggle, tooltip=toggle_tooltip: self._show_immediate_tooltip(
            widget,
            tooltip,
        )

        self._set_status_text(row.status, usage, action_pending=action_pending)
        row.config_button.setEnabled(not action_pending)
        config_tooltip = f"Configure {remote.display_name}"
        row.config_button.setToolTip(config_tooltip)
        row.config_button.enterEvent = lambda event, widget=row.config_button, tooltip=config_tooltip: (
            self._show_immediate_tooltip(widget, tooltip)
        )

    def _row_usage(self, remote: core.RemoteInfo, mounted: bool) -> core.StorageUsage:
        if not mounted:
            return core.StorageUsage("")
        return self._usage_cache.get(remote.name) or core.StorageUsage("Checking...")

    def _usage_indicator(self, usage: core.StorageUsage, *, checking_usage: bool) -> Any:
        indicator = self.qt.QProgressBar()
        indicator.setFixedSize(116, 8)
        indicator.setRange(0, 100)
        indicator.setTextVisible(False)
        self._apply_usage_indicator(indicator, usage, checking_usage=checking_usage)
        return indicator

    def _apply_usage_indicator(self, indicator: Any, usage: core.StorageUsage, *, checking_usage: bool) -> None:
        pct = max(0, min(usage.percent or 0, 100))
        fill = self._usage_color(usage, checking_usage=checking_usage)
        indicator.setValue(pct)
        indicator.setStyleSheet(
            "QProgressBar {"
            "border: 0;"
            "border-radius: 4px;"
            "background: #d1d5db;"
            "}"
            "QProgressBar::chunk {"
            f"background: {fill};"
            "border-radius: 4px;"
            "}"
        )

    def _usage_color(self, usage: core.StorageUsage, *, checking_usage: bool = False) -> str:
        if usage.percent is None:
            return "#9ca3af" if checking_usage else "#6b7280"
        remaining = None
        if usage.total is not None and usage.used is not None:
            remaining = max(usage.total - usage.used, 0)
        return "#dc2626" if remaining is not None and remaining < LOW_SPACE_BYTES else "#16a34a"

    def _usage_status_html(self, usage: core.StorageUsage, *, checking_usage: bool) -> str:
        if usage.used is not None and usage.total is not None:
            used_gb = usage.used / (1024**3)
            total_gb = usage.total / (1024**3)
            color = self._usage_color(usage, checking_usage=checking_usage)
            return (
                f'<span style="color:{color};">{used_gb:.1f}</span>'
                f'<span style="color:#ffffff;">/{total_gb:.1f} GB</span>'
            )
        if usage.text:
            return f'<span style="{_muted_text_style(self.window).removesuffix(";")}">{usage.text}</span>'
        return ""

    def _set_status_text(self, label: Any, usage: core.StorageUsage, *, action_pending: bool) -> None:
        if action_pending:
            label.setText("Working...")
            label.setStyleSheet(_muted_text_style(label))
            return
        label.setStyleSheet("")
        label.setText(self._usage_status_html(usage, checking_usage=usage.percent is None))

    def _switch(self) -> Any:
        qt = self.qt

        class Switch(qt.QCheckBox):
            def __init__(self) -> None:
                super().__init__()
                self.setText("")
                self.setFixedSize(42, 22)
                self.setCursor(qt.QCursor(qt.Qt.CursorShape.PointingHandCursor))

            def paintEvent(self, event: Any) -> None:
                painter = qt.QPainter(self)
                painter.setRenderHint(qt.QPainter.RenderHint.Antialiasing)
                painter.setPen(qt.Qt.PenStyle.NoPen)
                track = qt.QColor("#16a34a" if self.isChecked() else "#9ca3af")
                if not self.isEnabled():
                    track = qt.QColor("#6b7280")
                painter.setBrush(track)
                painter.drawRoundedRect(1, 2, 40, 18, 9, 9)
                painter.setBrush(qt.QColor("#ffffff"))
                knob_x = 22 if self.isChecked() else 4
                painter.drawEllipse(knob_x, 4, 14, 14)

            def hitButton(self, position: Any) -> bool:
                return bool(self.rect().contains(position))

        return Switch()

    def _show_immediate_tooltip(self, widget: Any, tooltip: str) -> None:
        if tooltip:
            self.qt.QToolTip.showText(self.qt.QCursor.pos(), tooltip, widget)

    def _handle_remote_row_click(self, event: Any, row: Any, remote: core.RemoteInfo) -> None:
        child = row.childAt(event.position().toPoint()) if hasattr(event, "position") else None
        while child is not None and child is not row:
            if child.property("rowControl"):
                return
            child = child.parentWidget()
        if not row.property("mounted"):
            return
        self._open_folder(remote)

    def _remote_row_style(self, row: Any, *, highlighted: bool) -> str:
        mounted = bool(row.property("mounted"))
        if not mounted:
            return (
                "QFrame#remoteRow {"
                "border: 1px solid rgba(107, 114, 128, 90);"
                "background: rgba(107, 114, 128, 24);"
                "}"
            )
        if highlighted:
            return (
                "QFrame#remoteRow {"
                "border: 1px solid rgba(22, 163, 74, 190);"
                "background: rgba(22, 163, 74, 36);"
                "}"
            )
        return ""

    def _highlight_remote_row(self, row: Any, *, highlighted: bool, tooltip: str | None = None) -> None:
        row.setStyleSheet(self._remote_row_style(row, highlighted=highlighted))
        if highlighted and row.property("mounted") and tooltip:
            self.qt.QToolTip.showText(self.qt.QCursor.pos(), tooltip, row)

    def _display_remote_name(self, remote: core.RemoteInfo) -> str:
        name = remote.display_name
        return name if len(name) <= 20 else name[:17] + "..."

    def _remote_name_width(self, remotes: list[core.RemoteInfo]) -> int:
        displayed = [self._display_remote_name(remote) for remote in remotes]
        longest = max(displayed, key=len, default="Remote")
        metrics = self.window.fontMetrics()
        return min(max(metrics.horizontalAdvance(longest) + 10, 88), metrics.horizontalAdvance("W" * 20) + 10)

    def _fit_to_content(self, root: Any, scroll: Any, container: Any) -> None:
        root.layout().activate()
        container.layout().activate()
        content_size = container.sizeHint()
        root_margins = root.layout().contentsMargins()
        scroll_frame = scroll.frameWidth() * 2
        menu_height = self.window.menuBar().sizeHint().height()
        width = root_margins.left() + root_margins.right() + scroll_frame + content_size.width() + 2
        height = menu_height + root_margins.top() + root_margins.bottom() + scroll_frame + content_size.height() + 2

        screen = self.window.screen() or self.qt.QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            max_width = max(360, available.width() - 96)
            max_height = max(220, available.height() - 96)
        else:
            max_width = 960
            max_height = 720

        if height > max_height:
            width += scroll.verticalScrollBar().sizeHint().width()
            height = max_height

        self.window.resize(min(max(width, 360), max_width), min(max(height, 132), max_height))

    def _button(self, label: str, callback: Any, *, enabled: bool = True) -> Any:
        button = self.qt.QPushButton(label)
        button.setEnabled(enabled)
        button.clicked.connect(lambda checked=False: callback())
        return button

    def _icon_button(self, label: str, callback: Any, *, enabled: bool = True) -> Any:
        button = self._button(label, callback, enabled=enabled)
        button.setFixedSize(30, 26)
        font = button.font()
        font.setPointSize(max(font.pointSize() + 4, 14))
        button.setFont(font)
        return button

    def _run_switch_action(self, remote_name: str, want_mounted: bool) -> None:
        remote = next((candidate for candidate in core.load_remotes() if candidate.name == remote_name), None)
        if remote is None:
            self.tray_app._notify("Mountlet", f"{remote_name} is no longer available.", success=False)
            self._request_refresh()
            return
        self._run_remote_action(remote, core.mount_remote if want_mounted else core.unmount_remote)

    def _run_remote_action(self, remote: core.RemoteInfo, action: Any) -> None:
        if remote.name in self._action_pending:
            return
        self._action_pending.add(remote.name)
        self._request_refresh()

        def worker() -> None:
            success, message = action(remote)
            self._bridge.action_finished.emit(remote.name, success, message)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_action_finished(self, remote_name: str, success: bool, message: str) -> None:
        if self._tray_is_quitting():
            return
        self._action_pending.discard(remote_name)
        self._usage_cache.pop(remote_name, None)
        self.tray_app._notify("Mountlet", _clean_message(message), success=success)
        self.tray_app.rebuild_menus()
        self._request_refresh()

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
        self._request_refresh()

        def worker() -> None:
            completed, failures = action(remotes)
            self._bridge.bulk_action_finished.emit(title, completed, failures)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_bulk_action_finished(self, title: str, completed: object, failures: object) -> None:
        if self._tray_is_quitting():
            return
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
        self._request_refresh()

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
            rclone_changed = old_remote.extra_info != new_remote.extra_info
            if path_changed or flags_changed or rclone_changed:
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
        self._request_refresh()

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

    def _open_app_config_file(self) -> None:
        self._open_text_config(app_config_file(), ensure_mountlet_config=True)

    def _open_mount_config_file(self) -> None:
        self._open_text_config(app_mounts_file(), ensure_mountlet_config=True)

    def _open_rclone_config_file(self) -> None:
        self._open_text_config(Path(core.CONFIG_PATH))

    def _open_fuse_config_file(self) -> None:
        self._open_text_config(FUSE_CONFIG_PATH)

    def _open_text_config(self, path: Path, *, ensure_mountlet_config: bool = False) -> None:
        if ensure_mountlet_config:
            ensure_default_config_files()
        if not path.exists():
            self.tray_app._notify("Open config", f"{path} does not exist.", success=False)
            return
        if _open_text_file_focused(path):
            return
        if not self.qt.QDesktopServices.openUrl(self.qt.QUrl.fromLocalFile(str(path))):
            self.tray_app._notify("Open config", f"Could not open {path}.", success=False)

    def _schedule_storage_load(self, remote: core.RemoteInfo) -> None:
        if self._tray_is_quitting():
            return
        if remote.name in self._usage_cache or remote.name in self._usage_pending:
            return
        self._usage_pending.add(remote.name)

        def worker() -> None:
            usage = core.get_storage_usage_details(remote)
            self._bridge.storage_ready.emit(remote.name, usage)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_storage_ready(self, remote_name: str, usage: core.StorageUsage) -> None:
        if self._tray_is_quitting():
            return
        self._usage_pending.discard(remote_name)
        self._usage_cache[remote_name] = usage
        if self.is_visible():
            self._request_refresh()

    def prepare_quit(self) -> None:
        self._refresh_pending = False
        self._usage_pending.clear()
        self._action_pending.clear()
        try:
            self.window.hide()
        except Exception:
            pass

    def _tray_is_quitting(self) -> bool:
        return bool(getattr(getattr(self, "tray_app", None), "_quitting", False))


class CloudMountTray:
    def __init__(self, qt: SimpleNamespace, refresh_interval: int = 10) -> None:
        self.qt = qt
        self.refresh_interval = max(refresh_interval, 2)
        self.app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        self._quitting = False
        self.remote_menu = qt.QMenu()
        self.app_menu = qt.QMenu()
        self.icon = self._icon()
        self.app.setWindowIcon(self.icon)
        self.main_window = MountletWindow(self)
        self.tray = qt.QSystemTrayIcon(self.icon, self.app)
        self.tray.setToolTip("Mountlet")
        self.tray.setContextMenu(self.app_menu)
        self.tray.activated.connect(self._handle_activation)
        self.timer = qt.QTimer()
        self.timer.timeout.connect(self.rebuild_menus)
        try:
            self.app.aboutToQuit.connect(self._prepare_quit)
        except Exception:
            pass

    def _icon(self) -> Any:
        icon_path = _packaged_icon_path()
        if icon_path:
            icon = self.qt.QIcon(icon_path)
            try:
                if not icon.isNull():
                    return icon
            except Exception:
                return icon
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
        if getattr(self, "_quitting", False):
            return
        if reason != self.qt.QSystemTrayIcon.ActivationReason.Trigger:
            return
        self.rebuild_menus()
        self.main_window.show()

    def rebuild_menus(self) -> None:
        if getattr(self, "_quitting", False):
            return
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
        self._add_action(self.app_menu, "Open app config file", self.main_window._open_app_config_file)
        self._add_action(self.app_menu, "Open mount config file", self.main_window._open_mount_config_file)
        self._add_action(self.app_menu, "Open rclone config file", self.main_window._open_rclone_config_file)
        self._add_action(self.app_menu, "Open FUSE config file", self.main_window._open_fuse_config_file)
        self.app_menu.addSeparator()
        self._add_action(self.app_menu, "Quit", self.request_quit)

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
        if getattr(self, "_quitting", False):
            return
        success, message = action(remote)
        self._notify("Mountlet", _clean_message(message), success=success)
        self.rebuild_menus()

    def _mount_all(self, remotes: list[core.RemoteInfo]) -> None:
        if getattr(self, "_quitting", False):
            return
        mounted, failures = core.mount_all(remotes)
        self._report_mount_results("Mount all", mounted, failures)
        self.rebuild_menus()

    def _schedule_auto_mounts(self) -> None:
        if getattr(self, "_quitting", False):
            return
        remotes = [remote for remote in core.load_remotes() if remote.auto_mount and not core.is_mounted(remote)]
        if not remotes:
            return
        delay_ms = int(load_app_settings().auto_mount_delay * 1000)
        self.qt.QTimer.singleShot(delay_ms, lambda: self._auto_mount(remotes))

    def _auto_mount(self, remotes: list[core.RemoteInfo]) -> None:
        if getattr(self, "_quitting", False):
            return
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
        if getattr(self, "_quitting", False):
            return
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
        if getattr(self, "_quitting", False):
            return
        print(f"{title}: {message}")
        icon = (
            self.qt.QSystemTrayIcon.MessageIcon.Information
            if success
            else self.qt.QSystemTrayIcon.MessageIcon.Warning
        )
        self.tray.showMessage(title, message, icon, 5000)

    def request_quit(self) -> None:
        self._prepare_quit()
        try:
            self.app.exit(0)
        except Exception:
            self.app.quit()

    def _prepare_quit(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        try:
            self.timer.stop()
        except Exception:
            pass
        self.main_window.prepare_quit()
        try:
            self.tray.hide()
        except Exception:
            pass


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
