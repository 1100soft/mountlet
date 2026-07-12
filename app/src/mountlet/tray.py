#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import html
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import __version__, core, license_control, rclone_wizard
from .badged_button import create_badged_button, set_badge, set_checkmark
from .cloud_browser import normalize_browser_path, parent_browser_path, remote_target
from .cloud_browser_ui import CompactCloudBrowser, MIME_TYPE
from .config_tools import bundle_file
from .config_tools import setup_wizard
from .config_tools.shared import app_config_file, app_mounts_file, app_state_dir, ensure_app_directories
from .platform_services import get_platform
from .platform_services.desktop import DesktopServices
from .platform_services.file_managers import (
    SYSTEM_FILE_MANAGER_ID,
    discover_file_managers,
    open_with_file_manager,
    resolve_file_manager,
)
from .platform_services.processes import external_process_environment
from .settings import (
    AppSettings,
    DEFAULT_SHORTCUTS,
    MountSettings,
    default_app_folder,
    ensure_default_config_files,
    load_app_settings,
    load_mount_settings,
    save_app_settings,
    save_mount_settings,
    set_start_at_login,
)
from .shortcuts import matches_shortcut, normalize_shortcut_text, shortcut_values
from .ui_icons import apply_button_icon, mountlet_icon


_DOLPHIN_MAIN_WINDOW_PATH = "/dolphin/Dolphin_1"
_dolphin_tab_target_cache: tuple[str, str] | None = None
_file_manager_label_cache: str | None = None
_CARDINAL_RE = re.compile(r"=\s*(\d+)")
LICENSE_REQUIRE_ENV = "MOUNTLET_REQUIRE_LICENSE"
OPEN_FOLDER_BEHAVIORS: tuple[tuple[str, str], ...] = (
    ("current_desktop", "Current desktop window"),
    ("existing_window", "Any existing file manager window"),
    ("new_window", "New file manager window"),
    ("file-manager-service", "Desktop file manager service"),
    ("default", "System default"),
)
REMOTE_SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("registration", "Registration time"),
    ("name", "Name"),
    ("provider", "Provider"),
    ("size", "Total size, largest first"),
    ("used", "Used space, largest first"),
    ("remaining", "Remaining space, lowest first"),
)
STORAGE_SORT_MODES = {"size", "used", "remaining"}
REMOTE_ROW_HEIGHT = 40
REMOTE_LIST_MIN_HEIGHT = 180
EMBEDDED_BROWSER_MIN_WIDTH = 540
EMBEDDED_BROWSER_MIN_HEIGHT = 340
REMOTE_CACHE_CHECK_BATCH_SIZE = 20
FIXED_SHORTCUT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Common",
        (
            ("Up / Down", "Move selection in the focused list"),
            ("Left / Right", "Move between the remote list and file browser when the key points toward the other pane"),
            ("Enter", "Select the focused remote or open the focused file item"),
            ("Esc", "Return from the file browser to the remote list"),
        ),
    ),
    (
        "File operations",
        (
            ("Ctrl+C", "Copy selected file-browser items"),
            ("Ctrl+X", "Cut selected file-browser items"),
            ("Ctrl+V", "Paste into the current file-browser folder"),
            ("Delete", "Delete selected file-browser items"),
        ),
    ),
)
COMMON_SHORTCUT_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("common_previous", "Previous item"),
    ("common_next", "Next item"),
)
REMOTE_SHORTCUT_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("remote_enter_browser", "Enter file browser"),
    ("remote_move_up", "Move remote up"),
    ("remote_move_down", "Move remote down"),
    ("remote_toggle_mount", "Mount or unmount remote"),
    ("remote_config", "Open remote settings"),
    ("remote_open_browser", "Open provider website"),
)
BROWSER_SHORTCUT_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("browser_open", "Open selected item"),
    ("browser_parent", "Parent folder"),
    ("browser_root", "Remote root"),
    ("browser_refresh", "Refresh folder"),
    ("browser_zoom_in", "Zoom in"),
    ("browser_zoom_out", "Zoom out"),
    ("browser_open_folder", "Open folder in file manager"),
    ("browser_copy", "Copy selected items"),
    ("browser_cut", "Cut selected items"),
    ("browser_paste", "Paste into current folder"),
    ("browser_delete", "Delete selected items"),
    ("browser_new_folder", "Create new folder"),
)
SHORTCUT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Common alternatives", COMMON_SHORTCUT_CONFIG_FIELDS),
    ("Remote list", REMOTE_SHORTCUT_CONFIG_FIELDS),
    ("File browser", BROWSER_SHORTCUT_CONFIG_FIELDS),
)
SHORTCUT_CONTEXTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Remote list", COMMON_SHORTCUT_CONFIG_FIELDS + REMOTE_SHORTCUT_CONFIG_FIELDS),
    ("File browser", COMMON_SHORTCUT_CONFIG_FIELDS + BROWSER_SHORTCUT_CONFIG_FIELDS),
)
MOUNT_FLAG_OPTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Read-only", "Mount this remote without allowing writes.", ("--read-only",)),
    ("Allow other users", "Let other local users access the mount when FUSE permits it.", ("--allow-other",)),
)
RCLONE_FIELD_TOOLTIPS = {
    "client_id": "Google OAuth client ID used by this Drive remote. Changing it may require reconnecting the account.",
    "client_secret": "Google OAuth client secret used by this Drive remote. Changing it may require reconnecting the account.",
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
    "username": "Proton account username used by this remote.",
    "2fa": "Current Proton 2FA code. Usually only needed while configuring the remote.",
    "mailbox_password": "Mailbox password for two-password Proton accounts.",
    "enable_caching": "Proton Drive metadata cache. Mountlet disables it by default to avoid stale mounted folders.",
}
RCLONE_BOOLEAN_FIELDS = {"shared_with_me", "env_auth", "enable_caching"}
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
DRIVE_USAGE_NOTE = "Google Drive usage excludes Photos and other Google account data."
DRIVE_CREDENTIAL_SOURCE_BUILTIN = "builtin"
DRIVE_CREDENTIAL_SOURCE_CUSTOM = "custom"
RCLONE_OAUTH_LOCAL_PORT = 53682
FORCED_QUIT_SECONDS = 3.0
EXTERNAL_RCLONE_CONFIG_PROVIDER = "__external__"
REMOTE_PROVIDER_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Google Drive", "drive"),
    ("Google Photos (limited)", "gphotos"),
    ("Dropbox", "dropbox"),
    ("Microsoft OneDrive", "onedrive"),
    ("Box", "box"),
    ("pCloud", "pcloud"),
    ("iCloud Drive / Photos", "iclouddrive"),
    ("Koofr", "koofr"),
    ("Proton Drive", "protondrive"),
    ("S3-compatible storage", "s3"),
    ("WebDAV", "webdav"),
    ("Other provider (rclone terminal)", EXTERNAL_RCLONE_CONFIG_PROVIDER),
)
REMOTE_PROVIDER_STATUSES = {
    "drive": "tested",
    "gphotos": "untested",
    "dropbox": "tested",
    "onedrive": "tested",
    "box": "tested",
    "pcloud": "tested",
    "iclouddrive": "untested",
    "koofr": "tested",
    "protondrive": "tested",
    "s3": "partial",
    "webdav": "untested",
    EXTERNAL_RCLONE_CONFIG_PROVIDER: "untested",
}
OAUTH_REMOTE_TYPES = {"drive", "gphotos", "dropbox", "onedrive", "box", "pcloud"}
REMOTE_CONFIG_SUFFIXES = {
    "drive": "Drive",
    "gphotos": "Google Photos",
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
    "box": "Box",
    "pcloud": "pCloud",
    "iclouddrive": "iCloud",
    "koofr": "Koofr",
    "protondrive": "Proton Drive",
    "s3": "S3",
    "webdav": "WebDAV",
    EXTERNAL_RCLONE_CONFIG_PROVIDER: "Remote",
}
S3_PROVIDER_CONFIG_SUFFIXES = {
    "cloudflare": "Cloudflare R2",
    "minio": "MinIO",
    "aws": "Amazon S3",
    "wasabi": "Wasabi",
    "other": "S3",
}
S3_PROVIDER_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "label": "Cloudflare R2",
        "status": "tested",
        "provider": "Cloudflare",
        "config_name": "Cloudflare R2",
        "endpoint": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com",
        "region": "auto",
        "access_key": "R2 access key ID",
        "secret_key": "R2 secret access key",
        "bucket": "Bucket name or bucket/folder",
        "endpoint_tip": "Use the default or jurisdiction-specific R2 endpoint from the token page.",
        "region_tip": "Cloudflare R2 uses auto. us-east-1 also aliases to auto.",
        "bucket_tip": "Enter the bucket name, especially for bucket-scoped R2 tokens.",
        "instructions": (
            '<a href="https://developers.cloudflare.com/r2/api/tokens/">Cloudflare R2 token guide</a> | '
            '<a href="https://developers.cloudflare.com/r2/api/s3/api/">R2 S3 endpoint guide</a>'
        ),
    },
    {
        "label": "MinIO / S3-compatible",
        "status": "untested",
        "provider": "Minio",
        "config_name": "MinIO",
        "endpoint": "http://127.0.0.1:9000",
        "region": "us-east-1",
        "access_key": "MinIO access key",
        "secret_key": "MinIO secret key",
        "bucket": "Bucket name or bucket/folder",
        "endpoint_tip": "Use your MinIO server endpoint.",
        "region_tip": "MinIO commonly accepts us-east-1 unless your server is configured otherwise.",
        "bucket_tip": "Enter the bucket name to mount.",
        "instructions": '<a href="https://min.io/docs/minio/linux/reference/minio-mc/mc-admin-user-svcacct-add.html">MinIO access key guide</a>',
    },
    {
        "label": "Amazon S3",
        "status": "untested",
        "provider": "AWS",
        "config_name": "Amazon S3",
        "endpoint": "",
        "region": "us-east-1",
        "access_key": "AWS access key ID",
        "secret_key": "AWS secret access key",
        "bucket": "Bucket name or bucket/folder",
        "endpoint_tip": "Leave blank for AWS so rclone uses the endpoint for the chosen region.",
        "region_tip": "Use the AWS region where the bucket lives.",
        "bucket_tip": "Enter the bucket name to mount.",
        "hide_endpoint": "true",
        "instructions": (
            '<a href="https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html">'
            "AWS access key guide</a>"
        ),
    },
    {
        "label": "Wasabi",
        "status": "untested",
        "provider": "Wasabi",
        "config_name": "Wasabi",
        "endpoint": "https://s3.wasabisys.com",
        "region": "us-east-1",
        "access_key": "Wasabi access key ID",
        "secret_key": "Wasabi secret access key",
        "bucket": "Bucket name or bucket/folder",
        "endpoint_tip": "Use the Wasabi service URL for the bucket's storage region.",
        "region_tip": "Use the Wasabi region where the bucket lives.",
        "bucket_tip": "Enter the bucket name to mount.",
        "instructions": (
            '<a href="https://docs.wasabi.com/docs/creating-a-user-account-and-access-key">Wasabi access key guide</a> | '
            '<a href="https://docs.wasabi.com/docs/what-are-the-service-urls-for-wasabi-s-different-storage-regions">'
            "Wasabi service URLs</a>"
        ),
    },
    {
        "label": "Other S3-compatible",
        "status": "untested",
        "provider": "Other",
        "config_name": "S3",
        "endpoint": "https://s3.example.com",
        "region": "us-east-1",
        "access_key": "Access key",
        "secret_key": "Secret key",
        "bucket": "Bucket name or bucket/folder",
        "endpoint_tip": "Use the S3-compatible endpoint from your storage provider.",
        "region_tip": "Use the provider's region, or us-east-1 when the provider does not require one.",
        "bucket_tip": "Enter the bucket name to mount.",
        "instructions": '<a href="https://rclone.org/s3/">rclone S3 provider guide</a>',
    },
)
WEBDAV_VENDOR_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "label": "Nextcloud",
        "status": "untested",
        "vendor": "nextcloud",
        "config_name": "Nextcloud",
        "url": "https://cloud.example.com/remote.php/dav/files/user",
        "user": "Nextcloud username",
        "password": "Password or app password",
        "url_tip": "Your Nextcloud WebDAV endpoint.",
        "user_tip": "Your Nextcloud username.",
        "password_tip": "Use your password or an app password if your server requires one.",
        "instructions": '<a href="https://docs.nextcloud.com/server/latest/user_manual/en/files/access_webdav.html">Nextcloud WebDAV guide</a>',
    },
    {
        "label": "ownCloud",
        "status": "untested",
        "vendor": "owncloud",
        "config_name": "ownCloud",
        "url": "https://cloud.example.com/remote.php/webdav/",
        "user": "ownCloud username",
        "password": "Password or app password",
        "url_tip": "Your ownCloud WebDAV endpoint.",
        "user_tip": "Your ownCloud username.",
        "password_tip": "Use your password or an app password if your server requires one.",
        "instructions": (
            '<a href="https://doc.owncloud.com/server/latest/user_manual/en/files/access_webdav.html">'
            "ownCloud WebDAV guide</a>"
        ),
    },
    {
        "label": "SharePoint Online",
        "status": "untested",
        "vendor": "sharepoint",
        "config_name": "SharePoint WebDAV",
        "url": "https://tenant.sharepoint.com/sites/site/Shared%20Documents",
        "user": "Microsoft account email",
        "password": "Password",
        "url_tip": "The SharePoint document library WebDAV URL.",
        "user_tip": "The Microsoft account used for this SharePoint library.",
        "password_tip": "Your SharePoint or Microsoft account password.",
        "instructions": '<a href="https://rclone.org/webdav/#sharepoint-online">rclone SharePoint WebDAV guide</a>',
    },
    {
        "label": "SharePoint NTLM",
        "status": "untested",
        "vendor": "sharepoint-ntlm",
        "config_name": "SharePoint NTLM",
        "url": "https://sharepoint.example.com/sites/site/Documents",
        "user": "DOMAIN\\username",
        "password": "Domain password",
        "url_tip": "The on-premises SharePoint WebDAV URL.",
        "user_tip": "Use the DOMAIN\\username format for NTLM authentication.",
        "password_tip": "Your domain password.",
        "instructions": (
            '<a href="https://rclone.org/webdav/#sharepoint-with-ntlm-authentication">'
            "rclone SharePoint NTLM guide</a>"
        ),
    },
    {
        "label": "Fastmail Files",
        "status": "untested",
        "vendor": "fastmail",
        "config_name": "Fastmail Files",
        "url": "https://webdav.fastmail.com/",
        "user": "Fastmail email address",
        "password": "Fastmail app password",
        "url_tip": "Fastmail Files WebDAV endpoint.",
        "user_tip": "Your Fastmail email address.",
        "password_tip": "Use a Fastmail app password with Files access.",
        "instructions": (
            '<a href="https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords">'
            "Fastmail app password guide</a>"
        ),
    },
    {
        "label": "rclone WebDAV server",
        "status": "untested",
        "vendor": "rclone",
        "config_name": "rclone WebDAV",
        "url": "http://127.0.0.1:8080/",
        "user": "Optional",
        "password": "Optional",
        "url_tip": "The URL of an rclone serve webdav endpoint.",
        "user_tip": "Leave blank if the server does not require a username.",
        "password_tip": "Leave blank if the server does not require a password.",
        "instructions": '<a href="https://rclone.org/commands/rclone_serve_webdav/">rclone serve WebDAV guide</a>',
    },
    {
        "label": "Other WebDAV",
        "status": "untested",
        "vendor": "other",
        "config_name": "WebDAV",
        "url": "https://cloud.example.com/webdav",
        "user": "Optional",
        "password": "Optional",
        "url_tip": "The WebDAV endpoint URL.",
        "user_tip": "Leave blank if the server does not require a username.",
        "password_tip": "Leave blank if the server does not require a password.",
        "instructions": '<a href="https://rclone.org/webdav/">rclone WebDAV guide</a>',
    },
)
PROVIDER_COLORS = {
    "drive": "#34a853",
    "gphotos": "#ea4335",
    "dropbox": "#0061ff",
    "onedrive": "#0078d4",
    "box": "#0057c2",
    "pcloud": "#17a2d4",
    "iclouddrive": "#0a84ff",
    "koofr": "#f59e0b",
    "protondrive": "#6d4aff",
    "s3": "#ff9900",
    "webdav": "#64748b",
}
REMOTE_BROWSER_URLS = {
    "drive": "https://drive.google.com/drive/my-drive",
    "gphotos": "https://photos.google.com/",
    "dropbox": "https://www.dropbox.com/home",
    "onedrive": "https://onedrive.live.com/",
    "box": "https://app.box.com/files",
    "pcloud": "https://my.pcloud.com/",
    "iclouddrive": "https://www.icloud.com/iclouddrive/",
    "koofr": "https://app.koofr.net/",
    "protondrive": "https://drive.proton.me/",
}
_wizard_pending_remote_names: set[str] = set()


class TrayDependencyError(RuntimeError):
    pass


def _packaged_icon_path() -> str | None:
    try:
        asset = files("mountlet").joinpath("assets/icon.png")
        if asset.is_file():
            return str(asset)
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent / "assets" / "icon.png"
    return str(fallback) if fallback.is_file() else None


def _packaged_asset_path(name: str) -> str | None:
    try:
        asset = files("mountlet").joinpath(f"assets/{name}")
        if asset.is_file():
            return str(asset)
    except Exception:
        pass
    fallback = Path(__file__).resolve().parent / "assets" / name
    return str(fallback) if fallback.is_file() else None


def _is_gnome_wayland() -> bool:
    if get_platform().system_name != "Linux" or not os.environ.get("WAYLAND_DISPLAY"):
        return False
    desktop = ":".join(
        (os.environ.get("XDG_CURRENT_DESKTOP", ""), os.environ.get("DESKTOP_SESSION", ""))
    ).casefold()
    return "gnome" in desktop


def _acquire_instance_lock(qt: SimpleNamespace) -> Any | None:
    lock_type = getattr(qt, "QLockFile", None)
    if lock_type is None:
        return SimpleNamespace()
    user_id = str(os.getuid()) if hasattr(os, "getuid") else os.environ.get("USERNAME", "user")
    lock = lock_type(str(Path(tempfile.gettempdir()) / f"mountlet-desktop-{user_id}.lock"))
    lock.setStaleLockTime(30_000)
    return lock if lock.tryLock(0) else None


def _load_qt_bindings() -> SimpleNamespace:
    try:
        from PySide6.QtCore import (
            QEvent,
            QFileInfo,
            QItemSelectionModel,
            QKeyCombination,
            QLockFile,
            QMimeData,
            QObject,
            QPoint,
            QSize,
            Qt,
            QTimer,
            QUrl,
            Signal,
            qVersion,
        )
        from PySide6.QtGui import (
            QAction,
            QBrush,
            QColor,
            QCursor,
            QDesktopServices,
            QDrag,
            QIcon,
            QKeySequence,
            QPainter,
            QPainterPath,
            QPen,
            QPixmap,
        )
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QButtonGroup,
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QFileIconProvider,
            QFrame,
            QFormLayout,
            QGridLayout,
            QGraphicsOpacityEffect,
            QGroupBox,
            QHBoxLayout,
            QInputDialog,
            QKeySequenceEdit,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QRadioButton,
            QScrollArea,
            QSizePolicy,
            QStyle,
            QSystemTrayIcon,
            QToolTip,
            QTreeWidget,
            QTreeWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise TrayDependencyError(
            "The Mountlet desktop app requires PySide6. Install it with:\n"
            '  pipx install "mountlet[desktop]"\n'
            "or, for an existing pipx install:\n"
            "  pipx inject mountlet PySide6\n\n"
            f"Import error: {exc}"
        ) from exc

    return SimpleNamespace(
        QAction=QAction,
        QAbstractItemView=QAbstractItemView,
        QApplication=QApplication,
        QBrush=QBrush,
        QButtonGroup=QButtonGroup,
        QColor=QColor,
        QCheckBox=QCheckBox,
        QComboBox=QComboBox,
        QCursor=QCursor,
        QDialog=QDialog,
        QDialogButtonBox=QDialogButtonBox,
        QDesktopServices=QDesktopServices,
        QFileDialog=QFileDialog,
        QDrag=QDrag,
        QFormLayout=QFormLayout,
        QFrame=QFrame,
        QGridLayout=QGridLayout,
        QGraphicsOpacityEffect=QGraphicsOpacityEffect,
        QGroupBox=QGroupBox,
        QHBoxLayout=QHBoxLayout,
        QInputDialog=QInputDialog,
        QItemSelectionModel=QItemSelectionModel,
        QIcon=QIcon,
        QKeyCombination=QKeyCombination,
        QKeySequence=QKeySequence,
        QKeySequenceEdit=QKeySequenceEdit,
        QLabel=QLabel,
        QLineEdit=QLineEdit,
        QLockFile=QLockFile,
        QMimeData=QMimeData,
        QEvent=QEvent,
        QFileIconProvider=QFileIconProvider,
        QFileInfo=QFileInfo,
        QMainWindow=QMainWindow,
        QMenu=QMenu,
        QMessageBox=QMessageBox,
        QPlainTextEdit=QPlainTextEdit,
        QObject=QObject,
        QPoint=QPoint,
        QPainter=QPainter,
        QPainterPath=QPainterPath,
        QPen=QPen,
        QPixmap=QPixmap,
        QProgressBar=QProgressBar,
        QPushButton=QPushButton,
        QRadioButton=QRadioButton,
        QScrollArea=QScrollArea,
        QSize=QSize,
        QSizePolicy=QSizePolicy,
        QStyle=QStyle,
        QSystemTrayIcon=QSystemTrayIcon,
        QTimer=QTimer,
        QToolTip=QToolTip,
        QTreeWidget=QTreeWidget,
        QTreeWidgetItem=QTreeWidgetItem,
        Qt=Qt,
        qVersion=qVersion,
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


def _remote_browser_url(remote: core.RemoteInfo) -> str | None:
    backend_type = remote.backend_type.casefold()
    if backend_type in REMOTE_BROWSER_URLS:
        return REMOTE_BROWSER_URLS[backend_type]
    if backend_type == "webdav":
        url = remote.extra_info.get("url", "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return None


def _provider_color(remote: core.RemoteInfo) -> str:
    return PROVIDER_COLORS.get(remote.backend_type.casefold(), "#64748b")


def _remote_service_label(remote: core.RemoteInfo) -> str:
    if remote.provider:
        return remote.provider
    return REMOTE_CONFIG_SUFFIXES.get(remote.backend_type.casefold(), remote.backend_type or "remote")


def _remote_browser_tooltip(remote: core.RemoteInfo) -> str:
    return f"Open {_remote_service_label(remote)} in browser"


def _is_google_drive_remote(remote: core.RemoteInfo) -> bool:
    return remote.backend_type.casefold() == "drive"


def _shortcut_hint(action: str) -> str:
    for value in shortcut_values(action):
        if value:
            return f"\nShortcut: {value}"
    return ""


def _apply_external_link_button(
    qt: SimpleNamespace,
    button: Any,
    *,
    text: str,
    prominent: bool = False,
    compact: bool = False,
) -> None:
    button.setText(text)
    with suppress(Exception):
        icon_size = 18 if compact else 20
        icon = mountlet_icon(qt, "ui-external-link", size=icon_size, color="#ffffff")
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(qt.QSize(icon_size, icon_size))
            button.setLayoutDirection(qt.Qt.LayoutDirection.RightToLeft)
    with suppress(Exception):
        font = button.font()
        if prominent:
            font.setBold(True)
            font.setPointSize(max(font.pointSize() + 2, 12))
        button.setFont(font)
    style = (
        "QPushButton { background: #16a34a; color: #ffffff; border: 1px solid #15803d; "
        "border-radius: 5px; padding: 6px 12px; } "
        "QPushButton:hover { background: #15803d; } "
        "QPushButton:pressed { background: #166534; }"
        if prominent
        else (
            "QPushButton { background: #2563eb; color: #ffffff; border: 1px solid #1d4ed8; "
            "border-radius: 5px; padding: 4px 10px; } "
            "QPushButton:hover { background: #1d4ed8; } "
            "QPushButton:pressed { background: #1e40af; }"
        )
    )
    with suppress(Exception):
        button.setStyleSheet(style)


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


def _config_sync_state_path() -> Path:
    return app_state_dir() / "config-sync.json"


def _load_config_sync_state() -> dict[str, Any]:
    path = _config_sync_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config_sync_state(state: dict[str, Any]) -> None:
    path = _config_sync_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _sync_metadata_summary(metadata: dict[str, object]) -> str:
    return (
        f"Updated {_human_sync_time(metadata.get('created_at'))} "
        f"{_human_sync_platform(metadata)} {_human_sync_device(metadata.get('device'))}."
    )


def _human_sync_platform(metadata: dict[str, object]) -> str:
    system = str(metadata.get("system") or "").strip()
    release = str(metadata.get("system_release") or "").strip()
    raw_platform = str(metadata.get("platform") or "").strip()
    if system:
        label = _friendly_system_name(system)
        if release and release.casefold() not in label.casefold():
            label = f"{label} {release}"
        return f"from {label}"
    if raw_platform:
        return f"from {raw_platform}"
    return "from an unknown OS"


def _friendly_system_name(system: str) -> str:
    names = {
        "darwin": "macOS",
        "linux": "Linux",
        "windows": "Windows",
    }
    return names.get(system.casefold(), system)


def _human_sync_device(value: object) -> str:
    device = str(value or "").strip()
    if not device:
        return "on an unknown device"
    local_device = platform.node()
    if local_device and device.casefold() == local_device.casefold():
        return "on this device"
    return f'on device "{device}"'


def _human_sync_time(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "at an unknown time"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"at {raw}"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone()
    month = local.strftime("%b")
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"on {month} {local.day}, {local.year} at {hour}:{local:%M} {local:%p}"


def _message_might_be_auth_failure(message: str) -> bool:
    text = message.casefold()
    indicators = (
        "token expired",
        "expired token",
        "refresh token",
        "invalid_grant",
        "unauthorized",
        "401",
        "403",
        "access_denied",
        "authentication",
        "authorization",
        "oauth",
        "login required",
        "reauth",
    )
    return any(indicator in text for indicator in indicators)


def _drive_credential_option_label(credentials: core.DriveOAuthCredentials, unique_count: int) -> str:
    if unique_count <= 1:
        return "Existing credentials (recommended)"
    return f"Existing: {credentials.remote_name}"


def _fixed_webdav_urls() -> set[str]:
    return {
        option["url"]
        for option in WEBDAV_VENDOR_OPTIONS
        if option.get("fixed_url", "").casefold() in {"true", "1", "yes"}
    }


def _default_s3_endpoints() -> set[str]:
    return {option.get("endpoint", "") for option in S3_PROVIDER_OPTIONS}


def _default_s3_regions() -> set[str]:
    return {option.get("region", "") for option in S3_PROVIDER_OPTIONS}


def _color_luminance(color: Any) -> float:
    return (0.2126 * color.red()) + (0.7152 * color.green()) + (0.0722 * color.blue())


def _palette_text_color(widget: Any) -> str:
    color = widget.palette().color(widget.foregroundRole())
    return color.name()


def _provider_status_color(status: str, widget: Any) -> str:
    if status != "untested":
        return _palette_text_color(widget)
    background = widget.palette().color(widget.backgroundRole())
    return "#facc15" if _color_luminance(background) < 128 else "#92400e"


def _command_version_line(command: list[str], *, timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            **core.PLATFORM.command_process_options(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"version check failed: {exc}"
    output = (result.stdout or "").strip()
    if not output:
        return f"version check exited with code {result.returncode}"
    return output.splitlines()[0].strip()


def _rclone_about_line() -> str:
    binary = core.find_rclone()
    if not binary:
        return "rclone: not found"
    return f"rclone: {_command_version_line([binary, 'version'])} ({binary})"


def _mount_driver_about_line() -> str:
    platform_services = get_platform()
    if platform_services.system_name == "Linux":
        tool = shutil.which("fusermount3") or shutil.which("fusermount")
        if not tool:
            return "FUSE: not found"
        return f"FUSE: {_command_version_line([tool, '--version'])} ({tool})"
    if platform_services.system_name == "Darwin":
        for package_id in ("io.macfuse.filesystems.macfuse", "com.github.osxfuse.pkg.Core"):
            version = _macos_package_version(package_id)
            if version:
                return f"macFUSE: {version}"
        return "macFUSE: detected" if platform_services.mount_driver_available() else "macFUSE: not found"
    if platform_services.system_name == "Windows":
        version = _windows_winfsp_version()
        if version:
            return f"WinFsp: {version}"
        return "WinFsp: detected" if platform_services.mount_driver_available() else "WinFsp: not found"
    return "Filesystem driver: detected" if platform_services.mount_driver_available() else "Filesystem driver: not found"


def _macos_package_version(package_id: str) -> str:
    try:
        result = subprocess.run(
            ["pkgutil", "--pkg-info", package_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            **core.PLATFORM.command_process_options(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    for line in (result.stdout or "").splitlines():
        key, _separator, value = line.partition(":")
        if key.strip() == "version":
            return value.strip()
    return ""


def _windows_winfsp_version() -> str:
    if platform.system() != "Windows":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for root, key_path in roots:
        try:
            with winreg.OpenKey(root, key_path) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        subkey_name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(parent, subkey_name) as subkey:
                            name = str(winreg.QueryValueEx(subkey, "DisplayName")[0])
                            if "WinFsp" not in name:
                                continue
                            return str(winreg.QueryValueEx(subkey, "DisplayVersion")[0])
                    except OSError:
                        continue
        except OSError:
            continue
    return ""


def _qt_about_line(qt: SimpleNamespace) -> str:
    qversion = getattr(qt, "qVersion", None)
    if callable(qversion):
        try:
            return f"Qt: {qversion()}"
        except Exception:
            pass
    return "Qt: unknown"


def _about_text(qt: SimpleNamespace) -> str:
    return "\n".join(
        [
            f"Mountlet: {__version__}",
            f"License: {license_control.status_summary()}",
            f"Python: {platform.python_version()}",
            _qt_about_line(qt),
            _rclone_about_line(),
            _mount_driver_about_line(),
            f"Platform: {platform.platform()}",
            f"rclone config: {core.CONFIG_PATH}",
            f"Mount folder: {core.BASE_MOUNT_DIR}",
        ]
    )


def _popup_position(
    anchor_x: int,
    anchor_y: int,
    available: tuple[int, int, int, int],
    window_size: tuple[int, int],
) -> tuple[int, int]:
    left, top, available_width, available_height = available
    width, height = window_size
    max_x = max(left, left + available_width - width)
    max_y = max(top, top + available_height - height)
    x = min(max(anchor_x - (width // 2), left), max_x)
    if anchor_y > top + (available_height // 2):
        y = anchor_y - height - 8
    else:
        y = anchor_y + 8
    return x, min(max(y, top), max_y)


def _set_combo_item_color(qt: SimpleNamespace, combo: Any, index: int, color: str) -> None:
    try:
        value = qt.QColor(color)
    except Exception:
        value = color
    try:
        combo.setItemData(index, value, 9)
    except Exception:
        try:
            combo.setItemData(index, value)
        except Exception:
            pass


def _frameless_window_flags(qt: SimpleNamespace, base_name: str) -> Any | None:
    try:
        window_type = qt.Qt.WindowType
        return getattr(window_type, base_name) | window_type.FramelessWindowHint
    except Exception:
        return None


def _native_dialog_flags(qt: SimpleNamespace) -> Any | None:
    try:
        window_type = qt.Qt.WindowType
        flags = window_type.Dialog
    except Exception:
        return None
    for name in (
        "WindowTitleHint",
        "WindowSystemMenuHint",
        "WindowMinMaxButtonsHint",
        "WindowCloseButtonHint",
    ):
        try:
            flags |= getattr(window_type, name)
        except Exception:
            pass
    return flags


def _main_window_type_name(is_macos: bool, is_wayland: bool = False) -> str:
    return "Window" if is_macos or is_wayland else "Tool"


def _main_window_uses_native_frame(is_wayland: bool) -> bool:
    return bool(is_wayland)


def _windows_foreground_is_tray() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        user32 = ctypes.windll.user32
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        buffer = ctypes.create_unicode_buffer(256)
        if not user32.GetClassNameW(foreground, buffer, len(buffer)):
            return False
    except (AttributeError, OSError):
        return False
    return buffer.value in {
        "Shell_TrayWnd",
        "NotifyIconOverflowWindow",
        "TopLevelWindowForOverflowXamlIsland",
    }


def _create_child_dialog(qt: SimpleNamespace, parent: Any | None = None) -> Any:
    flags = _native_dialog_flags(qt)
    if flags is not None:
        try:
            return qt.QDialog(parent, flags)
        except Exception:
            pass
    return qt.QDialog(parent)


class PrerequisiteWizard:
    """Keep the graphical startup alive while external prerequisites are installed."""

    def __init__(self, qt: SimpleNamespace) -> None:
        self.qt = qt
        self.dialog = qt.QDialog()
        self.dialog.setWindowTitle("Mountlet setup")
        icon_path = _packaged_icon_path()
        if icon_path:
            self.dialog.setWindowIcon(qt.QIcon(icon_path))
        self.dialog.setMinimumWidth(480)

        layout = qt.QVBoxLayout(self.dialog)
        heading = qt.QLabel("Prepare Mountlet")
        font = heading.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        heading.setFont(font)
        layout.addWidget(heading)

        description = qt.QLabel(
            "Mountlet needs rclone to access cloud storage. Filesystem mount "
            "support is optional and only needed for native folders."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.rows = qt.QGridLayout()
        layout.addLayout(self.rows)
        self._row_widgets: dict[str, tuple[Any, Any, Any]] = {}

        actions = qt.QHBoxLayout()
        actions.addStretch(1)
        self.recheck_button = qt.QPushButton("Check again")
        self.close_button = qt.QPushButton("Close")
        actions.addWidget(self.recheck_button)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)

        self.recheck_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.dialog.reject)
        self.timer = qt.QTimer(self.dialog)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self._accept_pending = False
        self.refresh()
        self.timer.start()

    def refresh(self) -> None:
        prerequisites = setup_wizard.check_prerequisites()
        for row, item in enumerate(prerequisites):
            widgets = self._row_widgets.get(item.key)
            if widgets is None:
                name = self.qt.QLabel()
                status = self.qt.QLabel()
                help_button = self.qt.QPushButton("Installation instructions")
                help_button.clicked.connect(
                    lambda _checked=False, url=item.help_url: self.qt.QDesktopServices.openUrl(self.qt.QUrl(url))
                )
                self.rows.addWidget(name, row, 0)
                self.rows.addWidget(status, row, 1)
                self.rows.addWidget(help_button, row, 2)
                widgets = (name, status, help_button)
                self._row_widgets[item.key] = widgets
            name, status, help_button = widgets
            name.setText(item.label)
            required = item.key == "rclone"
            status.setText("Ready" if item.ready else item.detail if required else f"Optional: {item.detail}")
            status.setToolTip(item.detail)
            help_button.setVisible(not item.ready)

        ready = all(item.ready for item in prerequisites if item.key == "rclone")
        self.recheck_button.setEnabled(not ready)
        if ready and not self._accept_pending:
            self._accept_pending = True
            self.timer.stop()
            self.qt.QTimer.singleShot(150, self.dialog.accept)

    def run(self) -> bool:
        accepted = int(self.dialog.exec() or 0)
        try:
            return accepted == int(self.qt.QDialog.DialogCode.Accepted)
        except Exception:
            return accepted != 0


def _run_prerequisite_wizard(qt: SimpleNamespace) -> bool:
    if get_platform().system_name == "Darwin":
        _set_macos_accessory_mode()
    app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    return PrerequisiteWizard(qt).run()


def _apply_frameless_window_flags(qt: SimpleNamespace, window: Any, *, base_name: str = "Window") -> None:
    flags = _frameless_window_flags(qt, base_name)
    if flags is not None:
        try:
            window.setWindowFlags(flags)
            return
        except Exception:
            pass
    try:
        window.setWindowFlag(qt.Qt.WindowType.FramelessWindowHint, True)
    except Exception:
        pass


def _load_visible_remotes() -> list[core.RemoteInfo]:
    return [
        remote
        for remote in core.load_remotes(include_incomplete=False)
        if remote.name not in _wizard_pending_remote_names
    ]


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


def _local_port_available(port: int, host: str = "127.0.0.1") -> bool:
    available, _owner_hint = _local_port_status(port, host)
    return available


def _local_port_status(port: int, host: str = "127.0.0.1") -> tuple[bool, str]:
    for family, address in _loopback_bind_addresses(port, host):
        server = socket.socket(family, socket.SOCK_STREAM)
        try:
            server.bind(address)
        except OSError as exc:
            if family == socket.AF_INET6 and exc.errno not in {errno.EADDRINUSE, errno.EACCES}:
                continue
            return False, _local_port_owner_hint(port)
        finally:
            server.close()
    return True, ""


def _loopback_bind_addresses(port: int, host: str) -> list[tuple[int, tuple[Any, ...]]]:
    addresses: list[tuple[int, tuple[Any, ...]]] = [(socket.AF_INET, (host, port))]
    if host in {"127.0.0.1", "localhost"} and socket.has_ipv6:
        addresses.append((socket.AF_INET6, ("::1", port)))
    return addresses


def _local_port_owner_hint(port: int) -> str:
    if platform.system() != "Linux":
        return ""
    for command in (
        ["ss", "-ltnp", f"sport = :{port}"],
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
    ):
        if not shutil.which(command[0]):
            continue
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        output = completed.stdout.strip()
        if completed.returncode == 0 and output:
            return _summarize_port_owner(output)
    return _proc_local_port_owner_hint(port)


def _summarize_port_owner(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""
    owner = lines[-1]
    process_match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', owner)
    if process_match:
        name, pid = process_match.groups()
        return f"Process using the port: {name} (PID {pid})."
    command_match = re.match(r"(\S+)\s+(\d+)\s+", owner)
    if command_match:
        name, pid = command_match.groups()
        return f"Process using the port: {name} (PID {pid})."
    return f"Port owner: {owner}"


def _proc_local_port_owner_hint(port: int) -> str:
    inodes = _proc_listening_socket_inodes(port)
    if not inodes:
        return ""
    for proc_entry in Path("/proc").iterdir():
        if not proc_entry.name.isdigit():
            continue
        fd_dir = proc_entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if not match or match.group(1) not in inodes:
                continue
            return f"Process using the port: {_proc_comm(proc_entry)} (PID {proc_entry.name})."
    return ""


def _proc_listening_socket_inodes(port: int) -> set[str]:
    inodes: set[str] = set()
    port_hex = f"{port:04X}"
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            columns = line.split()
            if len(columns) <= 9:
                continue
            local_address = columns[1]
            state = columns[3]
            if state != "0A":
                continue
            if not local_address.upper().endswith(f":{port_hex}"):
                continue
            inodes.add(columns[9])
    return inodes


def _proc_comm(proc_entry: Path) -> str:
    try:
        return (proc_entry / "comm").read_text(encoding="utf-8").strip() or proc_entry.name
    except OSError:
        return proc_entry.name


def _port_owner_from_hint(owner_hint: str) -> tuple[str, int] | None:
    match = re.search(r"Process using the port: (.+?) \(PID (\d+)\)", owner_hint)
    if not match:
        return None
    name, pid = match.groups()
    return name, int(pid)


def _rclone_port_owner_pid(owner_hint: str) -> int | None:
    owner = _port_owner_from_hint(owner_hint)
    if owner is None:
        return None
    name, pid = owner
    return pid if "rclone" in name.lower() else None


def _is_rclone_auth_port_error(message: str) -> bool:
    text = message.lower()
    return "auth webserver" in text and "53682" in text and (
        "already in use" in text or "operation not permitted" in text
    )


def _terminate_process_id(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _attempt in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def _desktop_session_available() -> tuple[bool, str]:
    system = platform.system()
    if system != "Linux":
        result = get_platform(system).graphical_session_available()
        return result.success, result.detail

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


def _desktop_services(qt: SimpleNamespace) -> DesktopServices:
    return DesktopServices(
        qt,
        folder_opener=lambda bindings, path, strategy: _open_folder_default(bindings, path, strategy),
        text_opener=lambda path: _open_text_file_focused(path),
        file_manager_name=lambda: _file_manager_label(),
        window_workspace_check=lambda window: _x11_qt_window_is_on_current_desktop(window),
        window_workspace_mover=lambda window: _move_x11_window_to_current_desktop(window),
        keep_above_setter=lambda window, enabled: _set_x11_keep_above(window, enabled),
    )


def _has_mount_driver_config() -> bool:
    return bool(get_platform().mount_driver_config_paths())


def _set_macos_accessory_mode() -> bool:
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        return bool(
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        )
    except Exception:
        return False


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
    settings = load_app_settings()
    manager = resolve_file_manager(get_platform(), settings.file_manager)
    label = manager.label.strip()
    match = re.fullmatch(r"System default \((?P<name>[^)]+)\)", label, flags=re.IGNORECASE)
    _file_manager_label_cache = match.group("name") if match else label
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
            env=external_process_environment(),
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


def _x11_qt_window_id(window: Any) -> int | None:
    try:
        return int(window.winId())
    except (AttributeError, TypeError, ValueError):
        return None


def _x11_qt_window_is_on_current_desktop(window: Any) -> bool | None:
    current_desktop = _x11_current_desktop()
    window_id = _x11_qt_window_id(window)
    if current_desktop is None or window_id is None:
        return None
    window_desktop = _x11_window_desktop(window_id)
    if window_desktop is None:
        return None
    return window_desktop in {current_desktop, 0xFFFFFFFF}


class _XClientMessageData(ctypes.Union):
    _fields_ = [
        ("b", ctypes.c_char * 20),
        ("s", ctypes.c_short * 10),
        ("l", ctypes.c_long * 5),
    ]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", _XClientMessageData),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("xclient", _XClientMessageEvent),
        ("pad", ctypes.c_long * 24),
    ]


def _send_x11_client_message(
    window_id: int,
    message_name: bytes,
    data: list[int],
    *,
    atom_data: dict[int, bytes] | None = None,
) -> bool:
    library = ctypes.util.find_library("X11")
    if not library:
        return False
    try:
        x11 = ctypes.CDLL(library)
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.POINTER(_XEvent),
        ]
        x11.XSendEvent.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = x11.XOpenDisplay(None)
    except (AttributeError, OSError):
        return False
    if not display:
        return False
    try:
        root = x11.XDefaultRootWindow(display)
        message_type = x11.XInternAtom(display, message_name, 0)
        if not root or not message_type:
            return False
        event = _XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.send_event = 1
        event.xclient.display = display
        event.xclient.window = window_id
        event.xclient.message_type = message_type
        event.xclient.format = 32
        for index, value in enumerate(data[:5]):
            event.xclient.data.l[index] = value
        for index, atom_name in (atom_data or {}).items():
            atom = x11.XInternAtom(display, atom_name, 0)
            if not atom:
                return False
            event.xclient.data.l[index] = atom
        event_mask = (1 << 20) | (1 << 19)  # SubstructureRedirect | SubstructureNotify
        sent = x11.XSendEvent(display, root, 0, event_mask, ctypes.byref(event))
        x11.XFlush(display)
        return bool(sent)
    except (AttributeError, OSError):
        return False
    finally:
        x11.XCloseDisplay(display)


def _send_x11_window_desktop_request(window_id: int, desktop: int) -> bool:
    return _send_x11_client_message(
        window_id,
        b"_NET_WM_DESKTOP",
        [desktop, 2],
    )


def _send_x11_keep_above_request(window_id: int, enabled: bool) -> bool:
    return _send_x11_client_message(
        window_id,
        b"_NET_WM_STATE",
        [1 if enabled else 0, 0, 0, 2],
        atom_data={1: b"_NET_WM_STATE_ABOVE"},
    )


def _set_x11_keep_above(window: Any, enabled: bool) -> bool:
    if platform.system() != "Linux":
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return False
    if not os.environ.get("DISPLAY"):
        return False
    window_id = _x11_qt_window_id(window)
    if window_id is None:
        return False
    return _send_x11_keep_above_request(window_id, enabled)


def _set_x11_window_desktop(window: Any, desktop: int) -> bool:
    if platform.system() != "Linux":
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return False
    if not os.environ.get("DISPLAY"):
        return False
    window_id = _x11_qt_window_id(window)
    if window_id is None:
        return False
    if _send_x11_window_desktop_request(window_id, desktop):
        return True
    xprop = shutil.which("xprop")
    if not xprop:
        return False
    try:
        result = subprocess.run(
            [
                xprop,
                "-id",
                str(window_id),
                "-f",
                "_NET_WM_DESKTOP",
                "32c",
                "-set",
                "_NET_WM_DESKTOP",
                str(desktop),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _move_x11_window_to_current_desktop(window: Any) -> bool:
    desktop = _x11_current_desktop()
    if desktop is None:
        return False
    return _set_x11_window_desktop(window, desktop)


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


def _open_folder_with_known_file_manager(
    path: str,
    *,
    behavior: str,
    focus: bool,
    manager_id: str | None = None,
) -> bool:
    selected = (manager_id or _default_directory_app()).lower()
    if "dolphin" in selected:
        if behavior == "new_window":
            return False
        if _open_folder_in_dolphin_tab(path, current_desktop=behavior == "current_desktop", focus=focus):
            return True
        if behavior == "current_desktop":
            return _open_folder_in_dolphin_new_window(path)
    return False


def _open_folder_default(qt: SimpleNamespace, path: str, strategy: str = "default") -> bool:
    settings = load_app_settings()
    manager = resolve_file_manager(get_platform(), settings.file_manager)
    behavior = settings.open_folder_behavior
    if strategy == "default":
        strategy = behavior
    if (
        strategy == "file-manager-service"
        and manager.identifier == SYSTEM_FILE_MANAGER_ID
        and _show_folder_with_file_manager(path)
    ):
        return True
    if strategy in {"current_desktop", "existing_window"} and _open_folder_with_known_file_manager(
        path,
        behavior=strategy,
        focus=settings.focus_file_manager,
        manager_id=(
            _default_directory_app()
            if manager.identifier == SYSTEM_FILE_MANAGER_ID
            else manager.identifier
        ),
    ):
        return True
    if (platform.system() == "Windows" or manager.identifier != SYSTEM_FILE_MANAGER_ID) and open_with_file_manager(
        manager,
        path,
        new_window=strategy == "new_window",
    ):
        return True
    return bool(qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(path)))


def _open_external_url(qt: SimpleNamespace, parent: Any, url: str, *, title: str = "Open link") -> bool:
    try:
        opened = bool(qt.QDesktopServices.openUrl(qt.QUrl(url)))
    except Exception:
        opened = False
    if not opened:
        qt.QMessageBox.warning(parent, title, f"Could not open:\n{url}")
    return opened


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
            subprocess.Popen(
                command,
                env=external_process_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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
    luminance = _color_luminance(background)
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


def _license_required() -> bool:
    return os.environ.get(LICENSE_REQUIRE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _license_locked() -> bool:
    return _license_required() and not license_control.current_status().allowed


def _license_lock_message() -> str:
    return "Mountlet is not activated, and the trial has ended."


class _ConfigDialogBase:
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        self.qt = qt
        self.dialog = _create_child_dialog(qt, parent)

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
        self.dialog.resize(460, 280)
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
            "mount_base": self._line(app_settings.mount_base, default=str(default_app_folder())),
            "auto_mount": self._check(app_settings.auto_mount),
            "auto_mount_delay": self._line(f"{app_settings.auto_mount_delay:g}"),
            "start_at_login": self._check(app_settings.start_at_login),
            "file_manager": self._combo(
                tuple(
                    (manager.identifier, manager.label)
                    for manager in discover_file_managers(get_platform(), refresh=True)
                ),
                app_settings.file_manager,
            ),
            "open_folder_behavior": self._combo(OPEN_FOLDER_BEHAVIORS, app_settings.open_folder_behavior),
            "focus_file_manager": self._check(app_settings.focus_file_manager),
            "integrated_file_edits": self._check(app_settings.integrated_file_edits),
            "remote_sync_interval": self._line(f"{app_settings.remote_sync_interval_seconds:g}"),
            "config_sync_remote": self._combo(
                (("", "Not set"), *((remote.name, remote.display_name) for remote in _load_visible_remotes())),
                app_settings.config_sync_remote,
            ),
            "config_sync_path": self._line(app_settings.config_sync_path),
        }
        self.fields["auto_mount"].setText("Auto-mount by default")
        self.fields["auto_mount"].setToolTip("Mount remotes automatically unless a remote overrides it.")
        self.fields["start_at_login"].setText("Start Mountlet when I log in")
        self.fields["start_at_login"].setToolTip("Start Mountlet automatically after signing in.")
        self.fields["focus_file_manager"].setText("Focus file manager")
        self.fields["focus_file_manager"].setToolTip("Bring the file manager forward after opening a mount folder.")
        self.fields["integrated_file_edits"].setText("Allow edits in Mountlet Files")
        self.fields["integrated_file_edits"].setToolTip(
            "Allow direct copy, move, delete, drag-and-drop, and folder creation in Mountlet Files."
        )
        self.fields["remote_sync_interval"].setToolTip(
            "Seconds between background checks for cloud-side changes in cached and offline files. Use 0 for manual sync only."
        )
        form.addRow(self.fields["start_at_login"])
        form.addRow(self.fields["auto_mount"])
        form.addRow("App folder", self._app_folder_selector())
        form.addRow("File manager", self.fields["file_manager"])
        form.addRow("Open folders", self.fields["open_folder_behavior"])
        form.addRow(self.fields["focus_file_manager"])
        form.addRow("Auto-mount delay", self.fields["auto_mount_delay"])
        form.addRow(self.fields["integrated_file_edits"])
        form.addRow("Cloud check interval", self.fields["remote_sync_interval"])
        form.addRow("Config sync remote", self.fields["config_sync_remote"])
        form.addRow("Config sync path", self.fields["config_sync_path"])
        warning = self.qt.QLabel("Mountlet file edits are direct, permanent, and not undoable.")
        warning.setWordWrap(True)
        warning.setStyleSheet(_muted_text_style(warning))
        form.addRow("", warning)
        root.addWidget(frame)
        root.addWidget(self._buttons())
        self.dialog.adjustSize()

    def _save(self) -> None:
        current = load_app_settings()
        try:
            delay = float(self.fields["auto_mount_delay"].text().strip() or "0")
        except ValueError:
            delay = 0.0
        try:
            remote_sync_interval = float(self.fields["remote_sync_interval"].text().strip() or "0")
        except ValueError:
            remote_sync_interval = 30.0
        if self.fields["integrated_file_edits"].isChecked() and not current.integrated_file_edits:
            self.qt.QMessageBox.warning(
                self.dialog,
                "Direct file edits",
                "Edits made in Mountlet Files are direct and not undoable. Deleted files are not sent to "
                "the system trash. Use the system file manager when you want buffered file-manager behavior.",
            )

        save_app_settings(
            AppSettings(
                mount_base=self.fields["mount_base"].text().strip() or None,
                auto_mount=self.fields["auto_mount"].isChecked(),
                auto_mount_delay=max(delay, 0.0),
                start_at_login=self.fields["start_at_login"].isChecked(),
                file_manager=self.fields["file_manager"].currentData() or "",
                open_folder_behavior=self.fields["open_folder_behavior"].currentData() or "current_desktop",
                focus_file_manager=self.fields["focus_file_manager"].isChecked(),
                integrated_file_edits=self.fields["integrated_file_edits"].isChecked(),
                remote_sync_interval_seconds=max(remote_sync_interval, 0.0),
                config_sync_remote=self.fields["config_sync_remote"].currentData() or "",
                config_sync_path=self.fields["config_sync_path"].text().strip() or "Mountlet/config.mountlet",
                shortcuts=current.shortcuts,
            )
        )
        global _file_manager_label_cache
        _file_manager_label_cache = None
        set_start_at_login(self.fields["start_at_login"].isChecked())
        self.dialog.accept()

    def _app_folder_selector(self) -> Any:
        container = self.qt.QWidget()
        layout = self.qt.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.fields["mount_base"], 1)
        button = self.qt.QPushButton("Browse")
        button.setToolTip("Choose the Mountlet app folder. Mounted remotes use its mounted folder; offline files use its offline folder.")
        button.clicked.connect(self._choose_app_folder)
        layout.addWidget(button)
        return container

    def _choose_app_folder(self) -> None:
        file_dialog = getattr(self.qt, "QFileDialog", None)
        if file_dialog is None:
            return
        current = self.fields["mount_base"].text().strip() or str(default_app_folder())
        selected = file_dialog.getExistingDirectory(self.dialog, "Choose Mountlet app folder", current)
        if selected:
            self.fields["mount_base"].setText(selected)


class ShortcutConfigDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.dialog.setWindowTitle("Keyboard shortcuts")
        self.dialog.resize(680, 620)
        self.fields: dict[str, list[Any]] = {}
        self.conflict_label: Any | None = None
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        scroll = self.qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(self.qt.QFrame.Shape.NoFrame)
        content = self.qt.QWidget()
        content_layout = self.qt.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        content_layout.addWidget(self._fixed_shortcut_box())
        alternatives = self.qt.QGroupBox("Alternative inputs")
        alternatives_layout = self.qt.QVBoxLayout(alternatives)
        alternatives_layout.setSpacing(6)
        for title, fields in SHORTCUT_GROUPS:
            alternatives_layout.addWidget(self._shortcut_group(title, fields, app_settings.shortcuts))
        content_layout.addWidget(alternatives)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.conflict_label = self.qt.QLabel("")
        self.conflict_label.setStyleSheet("color: #dc2626;")
        root.addWidget(self.conflict_label)
        restore = self.qt.QPushButton("Restore defaults")
        restore.clicked.connect(lambda _checked=False: self._restore_defaults())
        root.addWidget(restore)
        self._button_box = self._buttons()
        root.addWidget(self._button_box)
        self._update_conflicts()
        self._resize_to_content(content)

    def _resize_to_content(self, content: Any) -> None:
        try:
            content.adjustSize()
            self.dialog.adjustSize()
            screen = self.dialog.screen() or self.qt.QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            content_hint = content.sizeHint()
            preferred_width = max(680, content_hint.width() + 44)
            preferred_height = max(520, content_hint.height() + 120)
            max_width = max(360, int(available.width() * 0.92))
            max_height = max(320, int(available.height() * 0.92))
            self.dialog.resize(min(preferred_width, max_width), min(preferred_height, max_height))
        except Exception:
            self.dialog.adjustSize()

    def _fixed_shortcut_box(self) -> Any:
        group = self.qt.QGroupBox("Fixed inputs")
        layout = self.qt.QVBoxLayout(group)
        layout.setSpacing(6)
        for title, rows in FIXED_SHORTCUT_GROUPS:
            layout.addWidget(self._fixed_shortcut_group(title, rows))
        return group

    def _fixed_shortcut_group(self, title: str, rows: tuple[tuple[str, str], ...]) -> Any:
        group = self.qt.QGroupBox(title)
        form = self.qt.QFormLayout(group)
        for keys, description in rows:
            label = self.qt.QLabel(description)
            label.setWordWrap(True)
            label.setStyleSheet(_muted_text_style(label))
            form.addRow(keys, label)
        return group

    def _shortcut_group(
        self,
        title: str,
        fields: tuple[tuple[str, str], ...],
        shortcuts: dict[str, tuple[str, ...]],
    ) -> Any:
        group = self.qt.QGroupBox(title)
        form = self.qt.QFormLayout(group)
        for key, label in fields:
            row = self.qt.QWidget()
            row_layout = self.qt.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            values = list(shortcuts.get(key, DEFAULT_SHORTCUTS[key]))[:3]
            values.extend([""] * (3 - len(values)))
            self.fields[key] = []
            for index, value in enumerate(values):
                field = self.qt.QKeySequenceEdit(self.qt.QKeySequence(value))
                field.setToolTip(
                    f"Alternative shortcut {index + 1}"
                )
                field.keySequenceChanged.connect(lambda _sequence=None: self._update_conflicts())
                self.fields[key].append(field)
                row_layout.addWidget(field)
            form.addRow(label, row)
        return group

    def _save(self) -> None:
        conflicts = self._shortcut_conflicts(self._current_shortcuts())
        if conflicts:
            self.qt.QMessageBox.warning(
                self.dialog,
                "Shortcut conflict",
                "\n".join(conflicts),
            )
            self._update_conflicts()
            return
        current = load_app_settings()
        shortcuts = self._current_shortcuts()
        save_app_settings(
            AppSettings(
                mount_base=current.mount_base,
                auto_mount=current.auto_mount,
                auto_mount_delay=current.auto_mount_delay,
                start_at_login=current.start_at_login,
                file_manager=current.file_manager,
                open_folder_behavior=current.open_folder_behavior,
                focus_file_manager=current.focus_file_manager,
                integrated_file_edits=current.integrated_file_edits,
                remote_sync_interval_seconds=current.remote_sync_interval_seconds,
                config_sync_remote=current.config_sync_remote,
                config_sync_path=current.config_sync_path,
                shortcuts=shortcuts,
            )
        )
        self.dialog.accept()

    def _restore_defaults(self) -> None:
        for key, fields in self.fields.items():
            values = list(DEFAULT_SHORTCUTS[key])
            values.extend([""] * (3 - len(values)))
            for field, value in zip(fields, values, strict=False):
                field.setKeySequence(self.qt.QKeySequence(value))
        self._update_conflicts()

    def _current_shortcuts(self) -> dict[str, tuple[str, ...]]:
        shortcuts: dict[str, tuple[str, ...]] = {}
        for key, fields in self.fields.items():
            values = []
            for field in fields:
                value = field.keySequence().toString(self.qt.QKeySequence.SequenceFormat.PortableText).strip()
                if value:
                    values.append(value)
            shortcuts[key] = tuple(values[:3]) or DEFAULT_SHORTCUTS[key]
        return shortcuts

    def _update_conflicts(self) -> None:
        conflicts = self._shortcut_conflicts(self._current_shortcuts())
        conflict_keys = self._conflict_keys(self._current_shortcuts())
        for key, fields in self.fields.items():
            for field in fields:
                value = field.keySequence().toString(self.qt.QKeySequence.SequenceFormat.PortableText).strip()
                normalized = normalize_shortcut_text(value)
                field.setStyleSheet("border: 1px solid #dc2626;" if normalized in conflict_keys else "")
        if self.conflict_label is not None:
            self.conflict_label.setText("\n".join(conflicts[:2]))
        try:
            self._button_box.button(self.qt.QDialogButtonBox.StandardButton.Save).setEnabled(not conflicts)
        except Exception:
            pass

    def _shortcut_conflicts(self, shortcuts: dict[str, tuple[str, ...]]) -> list[str]:
        conflicts = []
        for title, fields in SHORTCUT_CONTEXTS:
            names = dict(fields)
            seen: dict[str, str] = {}
            context_conflicts: set[str] = set()
            for key, _label in fields:
                for value in shortcuts.get(key, ()):
                    normalized = normalize_shortcut_text(value)
                    if not normalized:
                        continue
                    if normalized in seen:
                        context_conflicts.add(
                            f"{title}: {value} is assigned to {seen[normalized]} and {names[key]}."
                        )
                    else:
                        seen[normalized] = names[key]
            conflicts.extend(sorted(context_conflicts))
        return conflicts

    def _conflict_keys(self, shortcuts: dict[str, tuple[str, ...]]) -> set[str]:
        result: set[str] = set()
        for _title, fields in SHORTCUT_CONTEXTS:
            seen: set[str] = set()
            for key, _label in fields:
                for value in shortcuts.get(key, ()):
                    normalized = normalize_shortcut_text(value)
                    if not normalized:
                        continue
                    if normalized in seen:
                        result.add(normalized)
                    seen.add(normalized)
        return result


class ConfigSyncDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.dialog.setWindowTitle("Config sync")
        self.dialog.resize(420, 160)
        self.fields: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        app_settings = load_app_settings()
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        frame = self.qt.QFrame()
        frame.setObjectName("remoteRow")
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        self.fields = {
            "remote": self._combo(
                (("", "Not set"), *((remote.name, remote.display_name) for remote in _load_visible_remotes())),
                app_settings.config_sync_remote,
            ),
            "path": self._line(app_settings.config_sync_path),
        }
        self.fields["path"].setPlaceholderText("Mountlet/config.mountlet")
        self.fields["path"].setToolTip("Remote path for the encrypted config bundle.")
        form.addRow("Remote", self.fields["remote"])
        form.addRow("Bundle path", self.fields["path"])
        note = self.qt.QLabel("The sync password is not stored. Mountlet will ask before each push or pull.")
        note.setWordWrap(True)
        note.setStyleSheet(_muted_text_style(note))
        form.addRow("", note)
        root.addWidget(frame)
        root.addWidget(self._buttons())
        self.dialog.adjustSize()

    def _save(self) -> None:
        current = load_app_settings()
        save_app_settings(
            AppSettings(
                mount_base=current.mount_base,
                auto_mount=current.auto_mount,
                auto_mount_delay=current.auto_mount_delay,
                start_at_login=current.start_at_login,
                file_manager=current.file_manager,
                open_folder_behavior=current.open_folder_behavior,
                focus_file_manager=current.focus_file_manager,
                integrated_file_edits=current.integrated_file_edits,
                remote_sync_interval_seconds=current.remote_sync_interval_seconds,
                config_sync_remote=self.fields["remote"].currentData() or "",
                config_sync_path=self.fields["path"].text().strip() or "Mountlet/config.mountlet",
                shortcuts=current.shortcuts,
            )
        )
        self.dialog.accept()


class LicenseDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.dialog.setWindowTitle("License")
        self.dialog.resize(520, 420)
        self.key_field = self.qt.QLineEdit()
        self.key_field.setText(license_control.load_license_key())
        self.key_field.setPlaceholderText("MNT-...")
        self.key_field.setToolTip("Your Mountlet license key. Keep this key somewhere safe.")
        self.device_field = self.qt.QLineEdit()
        self.device_field.setText(license_control.default_device_label())
        self.status_label = self.qt.QLabel()
        self.expiry_label = self.qt.QLabel()
        self.devices_label = self.qt.QLabel("Activated devices")
        self.devices_text = self.qt.QPlainTextEdit()
        self.devices_text.setReadOnly(True)
        self.activate_button = None
        self.copy_key_button = None
        self.paste_key_button = None
        self.refresh_devices_button = None
        self.deactivate_button = None
        self.buy_license_button = None
        self.add_devices_button = None
        self._clipboard = None
        self._build()
        self._refresh_status()
        self.key_field.setFocus()

    def _build(self) -> None:
        layout = self.qt.QVBoxLayout(self.dialog)
        status_row = self.qt.QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        self.buy_license_button = self.qt.QPushButton()
        _apply_external_link_button(self.qt, self.buy_license_button, text="Buy license", prominent=True)
        self.buy_license_button.clicked.connect(self._open_purchase_page)
        status_row.addWidget(self.buy_license_button)
        layout.addLayout(status_row)
        layout.addWidget(self.expiry_label)

        frame = self.qt.QFrame()
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        key_row = self.qt.QWidget()
        key_layout = self.qt.QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_field, 1)
        self.copy_key_button = create_badged_button(self.qt)
        self.copy_key_button.setFixedWidth(34)
        apply_button_icon(self.qt, self.copy_key_button, "ui-copy", fallback_text="Copy", size=22)
        self.copy_key_button.clicked.connect(self._copy_license_key)
        self.paste_key_button = create_badged_button(self.qt)
        self.paste_key_button.setFixedWidth(34)
        apply_button_icon(self.qt, self.paste_key_button, "ui-paste", fallback_text="Paste", size=22)
        self.paste_key_button.clicked.connect(self._paste_license_key)
        key_layout.addWidget(self.copy_key_button)
        key_layout.addWidget(self.paste_key_button)
        form.addRow("License key", key_row)
        form.addRow("Device name", self.device_field)
        layout.addWidget(frame)

        button_row = self.qt.QHBoxLayout()
        self.activate_button = self.qt.QPushButton("Activate")
        self.activate_button.clicked.connect(self._activate)
        self.refresh_devices_button = self.qt.QPushButton("Refresh devices")
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        self.deactivate_button = self.qt.QPushButton("Deactivate this device")
        self.deactivate_button.clicked.connect(self._deactivate_this_device)
        button_row.addWidget(self.activate_button)
        button_row.addWidget(self.refresh_devices_button)
        button_row.addWidget(self.deactivate_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        self.key_field.textChanged.connect(self._update_button_state)
        self._clipboard = self.qt.QApplication.clipboard()
        with suppress(Exception):
            self._clipboard.dataChanged.connect(self._update_copy_button_state)

        devices_header = self.qt.QHBoxLayout()
        devices_header.addWidget(self.devices_label)
        self.add_devices_button = self.qt.QPushButton()
        _apply_external_link_button(self.qt, self.add_devices_button, text="+ Add devices", compact=True)
        self.add_devices_button.clicked.connect(self._open_add_devices_page)
        devices_header.addWidget(self.add_devices_button)
        devices_header.addStretch(1)
        layout.addLayout(devices_header)
        layout.addWidget(self.devices_text)

        buttons = self.qt.QDialogButtonBox(self.qt.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.dialog.reject)
        layout.addWidget(buttons)

    def _refresh_status(self) -> None:
        status = license_control.current_status()
        if _license_required() and not status.allowed:
            self.status_label.setText(_license_lock_message())
            self.status_label.setStyleSheet("color: #ef4444; font-weight: 700;")
        else:
            self.status_label.setText(status.summary)
            self.status_label.setStyleSheet("")
        self._set_expiry_label(status)
        self._update_button_state(status)
        if status.state == "licensed":
            if status.license_key and self.key_field.text().strip() != status.license_key:
                self.key_field.setText(status.license_key)
            self._refresh_devices(show_errors=False)
        elif status.state == "trial":
            self.devices_label.setText("Activated devices")
            self.devices_text.setPlainText("Activate Mountlet to manage devices.")
        else:
            self.devices_label.setText("Activated devices")
            self.devices_text.setPlainText(f"{_license_lock_message()} Enter a license key to activate this device.")

    def _update_button_state(self, status: license_control.LicenseStatus | None = None) -> None:
        if not isinstance(status, license_control.LicenseStatus):
            status = None
        status = status or license_control.current_status()
        licensed = status.state == "licensed"
        has_key = bool(self.key_field.text().strip())
        self.key_field.setReadOnly(licensed)
        self.key_field.setToolTip(
            "This device is activated with this license key."
            if licensed
            else "Enter your Mountlet license key."
        )
        if self.activate_button is not None:
            self.activate_button.setEnabled(has_key and not licensed)
            self.activate_button.setToolTip(
                "This device is already activated."
                if licensed
                else (
                    "Activate this device with the entered license key."
                    if has_key
                    else "Enter a license key to activate this device."
                )
            )
        if self.copy_key_button is not None:
            self.copy_key_button.setEnabled(has_key)
            self._update_copy_button_state()
        if self.paste_key_button is not None:
            self.paste_key_button.setEnabled(not licensed)
            self.paste_key_button.setToolTip(
                "Paste a license key from the clipboard."
                if not licensed
                else "Deactivate this device before changing the license key."
            )
        if self.refresh_devices_button is not None:
            self.refresh_devices_button.setEnabled(licensed)
            self.refresh_devices_button.setToolTip(
                "Show devices activated with this license."
                if licensed
                else "Activate this device before managing license devices."
            )
        if self.deactivate_button is not None:
            self.deactivate_button.setEnabled(licensed)
            self.deactivate_button.setToolTip(
                "Deactivate this device and free one device slot."
                if licensed
                else "This device is not activated."
            )
        if self.buy_license_button is not None:
            self.buy_license_button.setVisible(not licensed)
            self.buy_license_button.setToolTip("Open the Mountlet license purchase page.")
        if self.add_devices_button is not None:
            self.add_devices_button.setVisible(licensed)
            self.add_devices_button.setToolTip("Open the purchase page with this license key prefilled.")

    def _activate(self) -> None:
        try:
            status = license_control.activate_license(
                self.key_field.text(),
                device_label=self.device_field.text(),
            )
        except RuntimeError as exc:
            self.qt.QMessageBox.warning(self.dialog, "License activation", str(exc))
            return
        self.status_label.setText(status.summary)
        self.status_label.setStyleSheet("")
        self._set_expiry_label(status)
        self.key_field.setText(status.license_key or self.key_field.text().strip())
        self._refresh_devices(show_errors=False)
        self._update_button_state(status)
        self.qt.QMessageBox.information(self.dialog, "License activation", "Mountlet is activated on this device.")

    def _set_expiry_label(self, status: license_control.LicenseStatus) -> None:
        if status.state == "licensed" and status.expires_at:
            self.expiry_label.setText(f"Renews: {status.expires_at}")
        elif status.state == "trial" and status.expires_at:
            self.expiry_label.setText(f"Trial ends: {status.expires_at}")
        elif status.state == "expired" and status.expires_at:
            self.expiry_label.setText(f"Expired: {status.expires_at}")
        else:
            self.expiry_label.setText("")
        self.expiry_label.setVisible(bool(self.expiry_label.text()))

    def _copy_license_key(self) -> None:
        text = self.key_field.text().strip()
        if text:
            self.qt.QApplication.clipboard().setText(text)
            self._update_copy_button_state()

    def _paste_license_key(self) -> None:
        if self.key_field.isReadOnly():
            return
        text = self.qt.QApplication.clipboard().text().strip()
        if text:
            self.key_field.setText(text)

    def _update_copy_button_state(self) -> None:
        if self.copy_key_button is None:
            return
        text = self.key_field.text().strip()
        copied = False
        if text:
            with suppress(Exception):
                copied = self.qt.QApplication.clipboard().text().strip() == text
        set_checkmark(self.copy_key_button, copied)
        self.copy_key_button.setToolTip(
            "Copied to clipboard." if copied else "Copy the license key."
        )

    def _open_purchase_page(self) -> None:
        _open_external_url(
            self.qt,
            self.dialog,
            license_control.license_purchase_url(),
            title="Buy license",
        )

    def _open_add_devices_page(self) -> None:
        _open_external_url(
            self.qt,
            self.dialog,
            license_control.license_purchase_url(add_devices=True, license_key=self.key_field.text()),
            title="Add devices",
        )

    def _refresh_devices(self, *, show_errors: bool = True) -> None:
        try:
            device_info = license_control.license_devices()
        except RuntimeError as exc:
            if show_errors:
                self.qt.QMessageBox.warning(self.dialog, "License devices", str(exc))
            return
        devices = list(device_info.get("devices") or [])
        used_devices = int(device_info.get("usedDevices") or len(devices))
        max_devices = int(device_info.get("maxDevices") or 0)
        expires_at = license_control.display_timestamp(str(device_info.get("expiresAt") or ""))
        if expires_at:
            self.expiry_label.setText(f"Renews: {expires_at}")
            self.expiry_label.setVisible(True)
        if max_devices:
            self.devices_label.setText(f"Activated devices ({used_devices}/{max_devices})")
        else:
            self.devices_label.setText(f"Activated devices ({used_devices})")
        if not devices:
            self.devices_text.setPlainText("No activated devices were returned.")
            return
        lines = []
        for device in devices:
            label = str(device.get("device_label") or device.get("deviceLabel") or "Unnamed device")
            platform_name = str(device.get("platform") or "")
            activated = str(device.get("activated_at") or device.get("activatedAt") or "")
            marker = "current" if device.get("current") else ""
            details = " - ".join(part for part in (platform_name, activated, marker) if part)
            lines.append(f"{label}{f' ({details})' if details else ''}")
        self.devices_text.setPlainText("\n".join(lines))
        self._update_button_state()

    def _deactivate_this_device(self) -> None:
        answer = self.qt.QMessageBox.question(
            self.dialog,
            "Deactivate this device?",
            "Deactivate this Mountlet installation and free one device slot?",
        )
        if answer != self.qt.QMessageBox.StandardButton.Yes:
            return
        try:
            license_control.deactivate_device()
        except RuntimeError as exc:
            self.qt.QMessageBox.warning(self.dialog, "Deactivate device", str(exc))
            return
        self._refresh_status()

    def _save(self) -> None:
        self.dialog.accept()


class MountConfigDialog(_ConfigDialogBase):
    def __init__(self, qt: SimpleNamespace, remote: core.RemoteInfo, parent: Any | None = None) -> None:
        super().__init__(qt, parent)
        self.remote = remote
        self.dialog.setWindowTitle(f"{remote.display_name} settings")
        self.dialog.resize(520, 220)
        self.fields: dict[str, Any] = {}
        self.rclone_fields: dict[str, tuple[str, Any]] = {}
        self.deleted = False
        self.renamed_from = ""
        self.renamed_to = remote.name
        self.remount_after_rename = False
        self._build()

    def _build(self) -> None:
        ensure_default_config_files()
        app_settings = load_app_settings()
        mount_settings = load_mount_settings().get(self.remote.name)
        self._saved_order = mount_settings.order if mount_settings else None
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
            "remote_alias": self._line(self.remote.alias),
            "auto_mount": self._check(bool(auto_mount)),
            "mount_path": self._line(
                _path_relative_to_base(mount_settings.mount_path if mount_settings else None, self._mount_base),
                default=default_relative_path,
            ),
            "remote_path": self._line(
                mount_settings.remote_path if mount_settings and mount_settings.remote_path else "",
                default="bucket or bucket/folder",
            ),
        }

        self.fields["remote_alias"].setToolTip(
            "Display name for this remote. Mountlet keeps the provider suffix in rclone.conf."
        )
        form.addRow("Remote name", self.fields["remote_alias"])

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
        self.fields["remote_path"].setToolTip(
            "Optional path inside the rclone remote. For S3/R2, use a bucket name or bucket/folder."
        )
        form.addRow("Remote path", self.fields["remote_path"])

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
        remote_name = self.remote.name
        alias = self.fields["remote_alias"].text().strip()
        if not core.valid_remote_alias(alias):
            self.qt.QMessageBox.warning(
                self.dialog,
                "Remote name",
                "Use a name without ':', '@', line breaks, or path separators.",
            )
            return
        try:
            new_remote_name = core.remote_name_with_alias(remote_name, alias)
        except ValueError as exc:
            self.qt.QMessageBox.warning(self.dialog, "Remote name", str(exc))
            return
        if new_remote_name != remote_name:
            if core.is_mounted(self.remote):
                new_display_name = f"{alias} ({self.remote.provider})" if self.remote.provider else alias
                if not self._confirm_unmount_for_change(
                    "Rename remote?",
                    f"Rename {self.remote.display_name} to {new_display_name}?\n\n"
                    "Mountlet will unmount it, rename it, and mount it again.",
                ):
                    return
                ok, message = core.unmount_remote(self.remote)
                if not ok:
                    self.qt.QMessageBox.warning(self.dialog, "Rename remote", _clean_message(message))
                    return
                self.remount_after_rename = True
            try:
                new_remote_name = core.rename_rclone_remote_alias(remote_name, alias)
            except ValueError as exc:
                self.qt.QMessageBox.warning(self.dialog, "Remote name", str(exc))
                return

        settings = load_mount_settings()
        if new_remote_name != remote_name:
            settings.pop(remote_name, None)
            self.renamed_from = remote_name
            self.renamed_to = new_remote_name
        settings[new_remote_name] = MountSettings(
            mount_path=self.fields["mount_path"].text().strip() or None,
            remote_path=self.fields["remote_path"].text().strip().strip("/") or None,
            mount_flags=[
                flag
                for field, tokens in self.flag_fields
                if field.isChecked()
                for flag in tokens
            ]
            + self._preserved_mount_flags,
            auto_mount=self.fields["auto_mount"].isChecked(),
            enabled=self._saved_enabled,
            order=self._saved_order,
        )
        save_mount_settings(settings)
        if self.rclone_fields:
            core.save_rclone_fields(
                new_remote_name,
                {key: self._rclone_config_value(kind, field) for key, (kind, field) in self.rclone_fields.items()},
            )
        self.dialog.accept()

    def _buttons(self) -> Any:
        buttons = self.qt.QDialogButtonBox(
            self.qt.QDialogButtonBox.StandardButton.Save | self.qt.QDialogButtonBox.StandardButton.Cancel
        )
        delete_button = buttons.addButton("Delete remote", self.qt.QDialogButtonBox.ButtonRole.DestructiveRole)
        delete_button.setStyleSheet("QPushButton { color: #ffffff; background: #dc2626; }")
        delete_button.clicked.connect(self._delete_remote)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.dialog.reject)
        return buttons

    def _delete_remote(self) -> None:
        already_confirmed = False
        if core.is_mounted(self.remote):
            if not self._confirm_unmount_for_change(
                "Delete remote?",
                f"Delete {self.remote.display_name} from rclone.conf?\n\n"
                "Mountlet will unmount it first. This does not delete files in cloud storage.",
            ):
                return
            ok, message = core.unmount_remote(self.remote)
            if not ok:
                self.qt.QMessageBox.warning(self.dialog, "Delete remote", _clean_message(message))
                return
            already_confirmed = True
        if not already_confirmed:
            reply = self.qt.QMessageBox.question(
                self.dialog,
                "Delete remote?",
                f"Delete {self.remote.display_name} from rclone.conf?\n\nThis does not delete files in cloud storage.",
                self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
                self.qt.QMessageBox.StandardButton.No,
            )
            if reply != self.qt.QMessageBox.StandardButton.Yes:
                return
        if not core.delete_rclone_remote(self.remote.name):
            self.qt.QMessageBox.warning(self.dialog, "Delete remote", f"{self.remote.name} was not found in rclone.conf.")
            return
        settings = load_mount_settings()
        if self.remote.name in settings:
            settings.pop(self.remote.name)
            save_mount_settings(settings)
        self.deleted = True
        self.dialog.accept()

    def _confirm_unmount_for_change(self, title: str, message: str) -> bool:
        reply = self.qt.QMessageBox.question(
            self.dialog,
            title,
            message,
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.Yes,
        )
        return reply == self.qt.QMessageBox.StandardButton.Yes

    def _rclone_config_field(self, key: str, value: str) -> tuple[str, Any]:
        if key in RCLONE_BOOLEAN_FIELDS:
            return "bool", self._check(_config_bool(value))
        if key in RCLONE_SELECT_FIELDS:
            return "combo", self._editable_config_combo(RCLONE_SELECT_FIELDS[key], value)
        field = self._line(value)
        if key == "client_secret":
            field.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        return "text", field

    def _rclone_config_value(self, kind: str, field: Any) -> str:
        if kind == "bool":
            return "true" if field.isChecked() else ""
        if kind == "combo":
            return field.currentText().strip()
        return field.text().strip()


class NewRemoteWizard:
    def __init__(self, qt: SimpleNamespace, parent: Any | None = None) -> None:
        self.qt = qt
        self.dialog = _create_child_dialog(qt, parent)
        self.dialog.setWindowTitle("Add remote")
        self.dialog.resize(520, 280)
        self.fields: dict[str, Any] = {}
        self._remote_name = ""
        self._remote_alias = ""
        self._state = ""
        self._drive_client_id = ""
        self._drive_client_secret = ""
        self._drive_local_auth = True
        self._drive_shared_drive = False
        self._drive_team_drive = ""
        self._initial_remote_path = ""
        self._remote_type = "drive"
        self._connect_after_create = False
        self._question: rclone_wizard.RcloneConfigStep | None = None
        self._answer_kind = ""
        self._answer_field: Any | None = None
        self._answer_group: Any | None = None
        self._completed = False
        self._cancelled = False
        self._last_rclone_action: Any | None = None
        self._port_retry_attempted = False
        self._browser_port_available = True
        self._browser_port_owner_hint = ""
        self._waiting_for_browser_auth = False
        self._message_boxes: list[Any] = []
        self._bridge = self._make_bridge()
        self._bridge.command_finished.connect(self._handle_command_finished)
        self._bridge.mount_finished.connect(self._handle_mount_finished)
        self._bridge.remote_checked.connect(self._handle_remote_checked)
        self._build()

    def exec(self) -> int:
        return int(self.dialog.exec() or 0)

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            command_finished = qt.Signal(object, object)
            mount_finished = qt.Signal(bool, str)
            remote_checked = qt.Signal(bool, str)

        return Bridge()

    def _focus_widget(self, widget: Any | None) -> None:
        if widget is None:
            return
        reason = None
        try:
            reason = self.qt.Qt.FocusReason.OtherFocusReason
        except Exception:
            pass
        try:
            if reason is None:
                widget.setFocus()
            else:
                widget.setFocus(reason)
        except TypeError:
            try:
                widget.setFocus()
            except Exception:
                pass
        except Exception:
            pass

    def _schedule_focus_widget(self, widget: Any | None) -> None:
        self._focus_widget(widget)
        try:
            self.qt.QTimer.singleShot(0, lambda target=widget: self._focus_widget(target))
        except Exception:
            pass

    def _answer_focus_widget(self) -> Any | None:
        if self._answer_kind == "radio" and self._answer_group is not None:
            try:
                checked = self._answer_group.checkedButton()
                if checked is not None:
                    return checked
            except Exception:
                pass
        return self._answer_field

    def _radio_group(self, options: list[tuple[str, str]], *, selected: str) -> tuple[Any, Any, Any]:
        widget = self.qt.QWidget()
        layout = self.qt.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        group = self.qt.QButtonGroup(widget)
        buttons = []
        for index, (value, label) in enumerate(options):
            button = self.qt.QRadioButton(label)
            button.setProperty("answerValue", value)
            button.setChecked(value == selected)
            group.addButton(button, index)
            layout.addWidget(button)
            buttons.append(button)
        return widget, buttons[0], buttons[1]

    def _build(self) -> None:
        root = self.qt.QVBoxLayout(self.dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)
        self.root = root

        frame = self.qt.QFrame()
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        form = self.qt.QFormLayout(frame)
        self.initial_frame = frame
        self.form = form

        provider = self.qt.QComboBox()
        for index, (label, backend_type) in enumerate(REMOTE_PROVIDER_OPTIONS):
            provider.addItem(label, backend_type)
            _set_combo_item_color(
                self.qt,
                provider,
                index,
                _provider_status_color(
                    REMOTE_PROVIDER_STATUSES.get(backend_type, "untested"),
                    provider,
                ),
            )
        provider.currentIndexChanged.connect(lambda _index=0: self._apply_provider_choice())

        name = self.qt.QLineEdit()
        name.setPlaceholderText("Personal Drive")
        name.textChanged.connect(self._update_action_button)

        client_id = self.qt.QLineEdit()
        client_id.setPlaceholderText("Optional")
        client_id.setToolTip("Google OAuth client ID. Leave blank to let rclone use its built-in client.")
        client_secret = self.qt.QLineEdit()
        client_secret.setPlaceholderText("Optional")
        client_secret.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        client_secret.setToolTip("Google OAuth client secret. Use the secret that matches the client ID.")

        credential_source = self.qt.QComboBox()
        existing_credentials = core.drive_oauth_credentials()
        if existing_credentials:
            for credentials in existing_credentials:
                credential_source.addItem(_drive_credential_option_label(credentials, len(existing_credentials)), credentials)
            credential_source.addItem("Built-in rclone client", DRIVE_CREDENTIAL_SOURCE_BUILTIN)
        else:
            credential_source.addItem("Built-in rclone client", DRIVE_CREDENTIAL_SOURCE_BUILTIN)
        credential_source.addItem("Enter client ID and secret", DRIVE_CREDENTIAL_SOURCE_CUSTOM)
        credential_source.currentIndexChanged.connect(lambda _index=0: self._apply_credential_choice())
        credential_help = self.qt.QLabel(
            "The built-in rclone client is usually easiest. Custom Google OAuth clients can fail with 403 "
            "unless the consent screen, scopes, redirect URI, and test users are configured. "
            '<a href="https://rclone.org/drive/#making-your-own-client-id">rclone guide</a> | '
            '<a href="https://developers.google.com/workspace/guides/configure-oauth-consent">'
            "Google OAuth setup</a>"
        )
        credential_help.setWordWrap(True)
        credential_help.setOpenExternalLinks(True)
        credential_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        credential_help.setStyleSheet(_muted_text_style(credential_help))

        auth_group, local_auth, remote_auth = self._radio_group(
            [
                ("local", "Open browser on this computer"),
                ("remote", "Paste a token from another computer"),
            ],
            selected="local",
        )
        local_auth.toggled.connect(lambda _checked=False: self._update_browser_port_status())
        remote_auth.toggled.connect(lambda _checked=False: self._update_browser_port_status())
        drive_group, my_drive, shared_drive = self._radio_group(
            [
                ("my_drive", "My Drive"),
                ("shared_drive", "Shared drive"),
            ],
            selected="my_drive",
        )
        shared_drive_id = self.qt.QLineEdit()
        shared_drive_id.setPlaceholderText("Shared drive ID")
        shared_drive_id.setEnabled(False)
        shared_drive_id.setToolTip("Only needed when using a Google shared drive.")
        shared_drive.toggled.connect(lambda _checked=False: shared_drive_id.setEnabled(shared_drive.isChecked()))
        connect_after_create = self.qt.QCheckBox("Mount this remote after creating it")
        connect_after_create.setChecked(False)
        connect_after_create.setToolTip("Mount the new remote as an operating-system folder after setup succeeds.")
        gphotos_help = self.qt.QLabel(
            "Google Photos is limited by Google's API. Recent rclone versions can only download media uploaded "
            'through rclone. <a href="https://rclone.org/googlephotos/#limitations">rclone Google Photos limits</a>'
        )
        gphotos_help.setWordWrap(True)
        gphotos_help.setOpenExternalLinks(True)
        gphotos_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        gphotos_help.setStyleSheet(_muted_text_style(gphotos_help))
        gphotos_read_only = self.qt.QCheckBox("Read only")
        gphotos_read_only.setToolTip(
            "Request read-only Google Photos access. Leave off if you want Mountlet/rclone to upload media."
        )

        s3_provider = self.qt.QComboBox()
        for index, option in enumerate(S3_PROVIDER_OPTIONS):
            s3_provider.addItem(option["label"], option)
            _set_combo_item_color(
                self.qt,
                s3_provider,
                index,
                _provider_status_color(option.get("status", "untested"), s3_provider),
            )
        s3_provider.currentIndexChanged.connect(lambda _index=0: self._apply_s3_provider_choice())
        s3_endpoint = self.qt.QLineEdit()
        s3_region = self.qt.QLineEdit()
        s3_access_key_id = self.qt.QLineEdit()
        s3_secret_access_key = self.qt.QLineEdit()
        s3_secret_access_key.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        s3_remote_path = self.qt.QLineEdit()
        s3_help = self.qt.QLabel("")
        s3_help.setOpenExternalLinks(True)
        s3_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        s3_help.setStyleSheet(_muted_text_style(s3_help))

        koofr_user = self.qt.QLineEdit()
        koofr_user.setPlaceholderText("Koofr email address")
        koofr_user.setToolTip("Your Koofr account email address.")
        koofr_pass = self.qt.QLineEdit()
        koofr_pass.setPlaceholderText("Koofr app password")
        koofr_pass.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        koofr_pass.setToolTip("Generate an app password in Koofr and paste it here.")
        koofr_help = self.qt.QLabel(
            '<a href="https://rclone.org/koofr/">rclone Koofr guide</a> | '
            '<a href="https://app.koofr.net/app/admin/preferences/password">Koofr app password</a>'
        )
        koofr_help.setOpenExternalLinks(True)
        koofr_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        koofr_help.setStyleSheet(_muted_text_style(koofr_help))

        proton_user = self.qt.QLineEdit()
        proton_user.setPlaceholderText("Proton email address")
        proton_user.setToolTip("Your Proton account username or email address.")
        proton_pass = self.qt.QLineEdit()
        proton_pass.setPlaceholderText("Proton password")
        proton_pass.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        proton_pass.setToolTip("Your Proton account password. rclone stores this obscured in rclone.conf.")
        proton_2fa = self.qt.QLineEdit()
        proton_2fa.setPlaceholderText("Optional")
        proton_2fa.setToolTip("Current 2FA code, if Proton asks for one during setup.")
        proton_mailbox_pass = self.qt.QLineEdit()
        proton_mailbox_pass.setPlaceholderText("Optional")
        proton_mailbox_pass.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        proton_mailbox_pass.setToolTip("Only needed for two-password Proton accounts.")
        proton_help = self.qt.QLabel(
            "Proton Drive support is beta in rclone. Log in to Proton Drive in a browser first so Proton has generated "
            'your Drive encryption keys. <a href="https://rclone.org/protondrive/">rclone Proton Drive guide</a>'
        )
        proton_help.setWordWrap(True)
        proton_help.setOpenExternalLinks(True)
        proton_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        proton_help.setStyleSheet(_muted_text_style(proton_help))

        icloud_service = self.qt.QComboBox()
        icloud_service.addItem("iCloud Drive", "drive")
        icloud_service.addItem("iCloud Photos", "photos")
        icloud_user = self.qt.QLineEdit()
        icloud_user.setPlaceholderText("Apple ID email address")
        icloud_user.setToolTip("The Apple ID used for iCloud Drive or Photos.")
        icloud_pass = self.qt.QLineEdit()
        icloud_pass.setPlaceholderText("Apple ID password")
        icloud_pass.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        icloud_pass.setToolTip("Your Apple ID password. rclone stores this obscured in rclone.conf.")
        icloud_help = self.qt.QLabel(
            "iCloud support depends on recent rclone versions and may ask follow-up security questions. "
            '<a href="https://rclone.org/iclouddrive/">rclone iCloud guide</a>'
        )
        icloud_help.setWordWrap(True)
        icloud_help.setOpenExternalLinks(True)
        icloud_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        icloud_help.setStyleSheet(_muted_text_style(icloud_help))

        webdav_url = self.qt.QLineEdit()
        webdav_vendor = self.qt.QComboBox()
        for index, option in enumerate(WEBDAV_VENDOR_OPTIONS):
            webdav_vendor.addItem(option["label"], option)
            _set_combo_item_color(
                self.qt,
                webdav_vendor,
                index,
                _provider_status_color(option.get("status", "untested"), webdav_vendor),
            )
        webdav_vendor.currentIndexChanged.connect(lambda _index=0: self._apply_webdav_vendor_choice())
        webdav_user = self.qt.QLineEdit()
        webdav_pass = self.qt.QLineEdit()
        webdav_pass.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
        webdav_help = self.qt.QLabel("")
        webdav_help.setOpenExternalLinks(True)
        webdav_help.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
        webdav_help.setStyleSheet(_muted_text_style(webdav_help))

        for field in (
            s3_endpoint,
            s3_region,
            s3_access_key_id,
            s3_secret_access_key,
            s3_remote_path,
            koofr_user,
            koofr_pass,
            proton_user,
            proton_pass,
            proton_2fa,
            proton_mailbox_pass,
            icloud_user,
            icloud_pass,
            webdav_url,
            webdav_user,
            webdav_pass,
        ):
            field.textChanged.connect(self._update_action_button)
        icloud_service.currentIndexChanged.connect(lambda _index=0: self._update_action_button())

        self.fields = {
            "provider": provider,
            "name": name,
            "credential_source": credential_source,
            "credential_help": credential_help,
            "gphotos_help": gphotos_help,
            "client_id": client_id,
            "client_secret": client_secret,
            "gphotos_read_only": gphotos_read_only,
            "auth_group": auth_group,
            "local_auth": local_auth,
            "remote_auth": remote_auth,
            "drive_group": drive_group,
            "my_drive": my_drive,
            "shared_drive": shared_drive,
            "shared_drive_id": shared_drive_id,
            "connect_after_create": connect_after_create,
            "s3_provider": s3_provider,
            "s3_endpoint": s3_endpoint,
            "s3_region": s3_region,
            "s3_access_key_id": s3_access_key_id,
            "s3_secret_access_key": s3_secret_access_key,
            "s3_remote_path": s3_remote_path,
            "s3_help": s3_help,
            "koofr_user": koofr_user,
            "koofr_pass": koofr_pass,
            "koofr_help": koofr_help,
            "proton_user": proton_user,
            "proton_pass": proton_pass,
            "proton_2fa": proton_2fa,
            "proton_mailbox_pass": proton_mailbox_pass,
            "proton_help": proton_help,
            "icloud_service": icloud_service,
            "icloud_user": icloud_user,
            "icloud_pass": icloud_pass,
            "icloud_help": icloud_help,
            "webdav_url": webdav_url,
            "webdav_vendor": webdav_vendor,
            "webdav_user": webdav_user,
            "webdav_pass": webdav_pass,
            "webdav_help": webdav_help,
        }
        form.addRow("Storage type", provider)
        form.addRow("Name", name)
        form.addRow("Google credentials", credential_source)
        form.addRow(credential_help)
        form.addRow(gphotos_help)
        form.addRow("Google client ID", client_id)
        form.addRow("Google client secret", client_secret)
        form.addRow("Google Photos access", gphotos_read_only)
        form.addRow("Authorization", auth_group)
        form.addRow("Drive", drive_group)
        form.addRow("Shared drive ID", shared_drive_id)
        form.addRow("S3 provider", s3_provider)
        form.addRow("S3 endpoint", s3_endpoint)
        form.addRow("S3 region", s3_region)
        form.addRow("S3 access key", s3_access_key_id)
        form.addRow("S3 secret key", s3_secret_access_key)
        form.addRow("S3 bucket/path", s3_remote_path)
        form.addRow(s3_help)
        form.addRow("Koofr user", koofr_user)
        form.addRow("Koofr app password", koofr_pass)
        form.addRow(koofr_help)
        form.addRow("Proton user", proton_user)
        form.addRow("Proton password", proton_pass)
        form.addRow("Proton 2FA code", proton_2fa)
        form.addRow("Proton mailbox password", proton_mailbox_pass)
        form.addRow(proton_help)
        form.addRow("iCloud service", icloud_service)
        form.addRow("Apple ID", icloud_user)
        form.addRow("Apple password", icloud_pass)
        form.addRow(icloud_help)
        form.addRow("WebDAV URL", webdav_url)
        form.addRow("WebDAV vendor", webdav_vendor)
        form.addRow(webdav_help)
        form.addRow("WebDAV user", webdav_user)
        form.addRow("WebDAV password", webdav_pass)
        form.addRow(connect_after_create)

        self.question_frame = self.qt.QFrame()
        self.question_frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        self.question_layout = self.qt.QFormLayout(self.question_frame)
        self.question_frame.hide()

        self.status = self.qt.QLabel("")
        self.status.setStyleSheet(_muted_text_style(self.status))
        self.status.setWordWrap(True)

        buttons = self.qt.QDialogButtonBox(self.qt.QDialogButtonBox.StandardButton.Close)
        self.action_button = buttons.addButton("Create remote", self.qt.QDialogButtonBox.ButtonRole.AcceptRole)
        self.action_button.clicked.connect(self._next)
        buttons.rejected.connect(self._reject)

        root.addWidget(frame)
        root.addWidget(self.question_frame)
        root.addWidget(self.status)
        root.addWidget(buttons)
        self._port_timer = self.qt.QTimer(self.dialog)
        self._port_timer.setInterval(1000)
        self._port_timer.timeout.connect(self._update_browser_port_status)
        self._port_timer.start()
        self._apply_credential_choice()
        self._apply_provider_choice()
        self._apply_s3_provider_choice()
        self._apply_webdav_vendor_choice()
        self._update_browser_port_status()
        self._update_action_button()
        self.dialog.adjustSize()
        self._schedule_focus_widget(name)

    def _update_action_button(self) -> None:
        if not hasattr(self, "action_button"):
            return
        if self._question is not None:
            self.action_button.setEnabled(True)
            self.action_button.setText(self._question_button_text())
            return
        remote_type = self.fields["provider"].currentData() if getattr(self, "fields", None) else ""
        self.action_button.setText(
            "Open rclone config" if remote_type == EXTERNAL_RCLONE_CONFIG_PROVIDER else "Create remote"
        )
        self.action_button.setEnabled(self._setup_fields_are_valid() and self._browser_port_available)

    def _setup_fields_are_valid(self) -> bool:
        remote_type = self.fields["provider"].currentData() or "drive"
        if remote_type == EXTERNAL_RCLONE_CONFIG_PROVIDER:
            return True
        if not self.fields["name"].text().strip():
            return False
        if remote_type == "s3":
            return self._s3_fields_are_valid()
        if remote_type == "koofr":
            return self._koofr_fields_are_valid()
        if remote_type == "protondrive":
            return self._proton_fields_are_valid()
        if remote_type == "iclouddrive":
            return self._icloud_fields_are_valid()
        if remote_type == "webdav":
            return self._webdav_fields_are_valid()
        return True

    def _s3_fields_are_valid(self) -> bool:
        provider = self._s3_provider_value()
        endpoint = self.fields["s3_endpoint"].text().strip()
        has_keys = bool(
            self.fields["s3_access_key_id"].text().strip()
            and self.fields["s3_secret_access_key"].text().strip()
        )
        if not has_keys:
            return False
        if provider.lower() != "aws" and not endpoint:
            return False
        return True

    def _koofr_fields_are_valid(self) -> bool:
        return bool(self.fields["koofr_user"].text().strip() and self.fields["koofr_pass"].text().strip())

    def _proton_fields_are_valid(self) -> bool:
        return bool(self.fields["proton_user"].text().strip() and self.fields["proton_pass"].text().strip())

    def _icloud_fields_are_valid(self) -> bool:
        return bool(self.fields["icloud_user"].text().strip() and self.fields["icloud_pass"].text())

    def _webdav_fields_are_valid(self) -> bool:
        url = self.fields["webdav_url"].text().strip()
        return url.startswith(("http://", "https://"))

    def _next(self) -> None:
        if self._question is None:
            self._start()
            return
        self._continue()

    def _start(self) -> None:
        selected_type = self.fields["provider"].currentData() or "drive"
        if selected_type == EXTERNAL_RCLONE_CONFIG_PROVIDER:
            self._open_external_rclone_config()
            return
        alias = self.fields["name"].text().strip()
        if not self._valid_remote_name(alias):
            self._warning("Add remote", "Use a name without ':', '@', or path separators.")
            return
        self._remote_type = self.fields["provider"].currentData() or "drive"
        provider_name = self._selected_provider_config_name(self._remote_type)
        existing_remotes = core.load_remotes()
        if self._display_name_exists(alias, self._remote_type, existing_remotes, provider_name=provider_name):
            self._warning("Add remote", f"{alias} already exists for {provider_name}.")
            return
        name = self._config_remote_name(alias, self._remote_type, existing_remotes, provider_name=provider_name)
        if any(remote.name == name for remote in existing_remotes):
            self._warning("Add remote", f"{self._provider_display_name(alias, self._remote_type, provider_name)} already exists.")
            return
        self._remote_name = name
        self._remote_alias = alias
        self._cancelled = False
        self._drive_client_id = self.fields["client_id"].text()
        self._drive_client_secret = self.fields["client_secret"].text()
        self._drive_local_auth = self.fields["local_auth"].isChecked()
        self._drive_shared_drive = self.fields["shared_drive"].isChecked()
        self._drive_team_drive = self.fields["shared_drive_id"].text()
        self._initial_remote_path = self._initial_mount_remote_path()
        self._connect_after_create = self.fields["connect_after_create"].isChecked()
        if self._remote_type == "drive" and self._drive_shared_drive and not self._drive_team_drive.strip():
            self._warning("Add remote", "Enter the shared drive ID before setup, or choose My Drive.")
            return
        if self._remote_type == "gphotos" and rclone_wizard.backend_is_available("gphotos") is False:
            self._warning(
                "Add remote",
                "This rclone installation does not include Google Photos support.\n\n"
                "Update rclone, then try again.",
            )
            self._remote_name = ""
            self._remote_alias = ""
            return
        if self._remote_type == "s3" and not self._s3_fields_are_valid():
            self._warning("Add remote", "Enter the S3 endpoint, access key, and secret key before creating the remote.")
            return
        if self._remote_type == "koofr" and not self._koofr_fields_are_valid():
            self._warning("Add remote", "Enter the Koofr user and app password before creating the remote.")
            return
        if self._remote_type == "protondrive" and not self._proton_fields_are_valid():
            self._warning("Add remote", "Enter the Proton user and password before creating the remote.")
            return
        if self._remote_type == "protondrive" and rclone_wizard.backend_is_available("protondrive") is False:
            self._warning(
                "Add remote",
                "This rclone installation does not include Proton Drive support.\n\n"
                "Update rclone to v1.64.0 or newer, then try again.",
            )
            self._remote_name = ""
            self._remote_alias = ""
            return
        if self._remote_type == "iclouddrive" and not self._icloud_fields_are_valid():
            self._warning("Add remote", "Enter the Apple ID and password before creating the remote.")
            return
        if self._remote_type == "iclouddrive" and rclone_wizard.backend_is_available("iclouddrive") is False:
            self._warning(
                "Add remote",
                "This rclone installation does not include iCloud Drive support.\n\n"
                "Update rclone, then try again.",
            )
            self._remote_name = ""
            self._remote_alias = ""
            return
        if self._remote_type == "webdav" and not self._webdav_fields_are_valid():
            self._warning("Add remote", "Enter a WebDAV URL that starts with http:// or https://.")
            return
        if not self._browser_auth_port_ready():
            self._remote_name = ""
            self._remote_alias = ""
            return
        _wizard_pending_remote_names.add(name)
        self._show_setup_view(False)
        self._run_rclone(
            lambda: rclone_wizard.start_remote(
                name,
                self._remote_type,
                self._initial_config_args(),
            ),
            browser_auth=self._uses_browser_auth() and self._drive_local_auth,
        )

    def _open_external_rclone_config(self) -> None:
        try:
            config_path = rclone_wizard.open_config_in_external_terminal()
        except Exception as exc:
            self._warning("Add remote", str(exc))
            return
        self.status.setText(f"Opened rclone config in an external terminal.\nConfig file: {config_path}")
        self._completed = True
        self._stop_port_timer()
        self.dialog.accept()

    def _continue(self) -> None:
        answer = self._answer_value()
        if self._answer_opens_browser_auth(answer) and not self._browser_auth_port_ready():
            return
        opens_browser_auth = self._answer_opens_browser_auth(answer)
        self._run_rclone(
            lambda: rclone_wizard.continue_remote(
                self._remote_name,
                self._remote_type,
                self._state,
                answer,
                self._initial_config_args(),
            ),
            browser_auth=opens_browser_auth,
        )

    def _run_rclone(self, action: Any, *, reset_port_retry: bool = True, browser_auth: bool = False) -> None:
        self._last_rclone_action = action
        if reset_port_retry:
            self._port_retry_attempted = False
        self._waiting_for_browser_auth = browser_auth
        self._set_busy(True)

        def worker() -> None:
            try:
                self._bridge.command_finished.emit(action(), None)
            except Exception as exc:
                self._bridge.command_finished.emit(None, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_command_finished(self, step: object, error: object) -> None:
        if getattr(self, "_cancelled", False):
            return
        self._waiting_for_browser_auth = False
        self._set_busy(False)
        if error is not None:
            if self._recover_from_rclone_port_error(str(error)):
                return
            self._cleanup_incomplete_remote()
            self._warning("Add remote", str(error))
            return
        if not isinstance(step, rclone_wizard.RcloneConfigStep):
            self._warning("Add remote", "rclone returned an unexpected response.")
            return
        if step.error:
            self.status.setText(step.error)
        if step.complete:
            if not self._created_remote_has_credentials():
                self._cleanup_incomplete_remote()
                self._warning(
                    "Add remote",
                    f"{self._provider_label(self._remote_type)} authorization did not finish. No remote was added.",
                )
                return
            self._save_initial_mount_settings()
            if self._connect_after_create:
                self._mount_created_remote()
                return
            self._check_created_remote()
            return
        automatic_answer = self._automatic_answer(step)
        if automatic_answer is not None:
            if self._answer_opens_browser_auth(automatic_answer, step.option) and not self._browser_auth_port_ready():
                return
            self._state = step.state
            self._run_rclone(
                lambda: rclone_wizard.continue_remote(
                    self._remote_name,
                    self._remote_type,
                    self._state,
                    automatic_answer,
                    self._initial_config_args(),
                ),
                browser_auth=self._answer_opens_browser_auth(automatic_answer, step.option),
            )
            return
        self._show_question(step)

    def _uses_browser_auth(self) -> bool:
        return getattr(self, "_remote_type", "") in OAUTH_REMOTE_TYPES

    def _setup_uses_local_browser_auth(self) -> bool:
        if not self.fields or self._question is not None or self._remote_name:
            return False
        remote_type = self.fields["provider"].currentData() or "drive"
        return remote_type in OAUTH_REMOTE_TYPES and self.fields["local_auth"].isChecked()

    def _update_browser_port_status(self) -> None:
        if not hasattr(self, "action_button"):
            return
        if not self._setup_uses_local_browser_auth():
            self._browser_port_available = True
            self._browser_port_owner_hint = ""
            if self._question is None and not self._remote_name:
                self.status.setText("")
            self._update_action_button()
            return
        available, owner_hint = _local_port_status(RCLONE_OAUTH_LOCAL_PORT)
        self._browser_port_available = available
        self._browser_port_owner_hint = owner_hint
        self.status.setText("" if available else self._browser_port_wait_message(owner_hint))
        self._update_action_button()

    def _browser_port_wait_message(self, owner_hint: str = "") -> str:
        detail = f" {owner_hint}" if owner_hint else ""
        return (
            "Waiting for rclone's browser sign-in port to become available. "
            "A previous browser sign-in is probably still finishing."
            f"{detail}"
        )

    def _answer_opens_browser_auth(self, answer: str, option: dict[str, Any] | None = None) -> bool:
        option = option or (self._question.option if self._question else None)
        if not option or self._option_name(option) != "config_is_local":
            return False
        return answer.lower() in {"true", "1", "yes", "y"}

    def _browser_auth_port_ready(self) -> bool:
        if not (self._uses_browser_auth() and self._drive_local_auth):
            return True
        port_available, owner_hint = _local_port_status(RCLONE_OAUTH_LOCAL_PORT)
        if port_available:
            self.status.setText("")
            return True
        self._browser_port_available = False
        self._browser_port_owner_hint = owner_hint
        self.status.setText(self._browser_port_wait_message(owner_hint))
        self._update_action_button()
        return False

    def _recover_from_rclone_port_error(self, message: str) -> bool:
        if not _is_rclone_auth_port_error(message):
            return False
        self._cleanup_incomplete_remote()
        self._show_setup_view(True)
        self._remote_name = ""
        self._remote_alias = ""
        self._question = None
        self._answer_field = None
        self._answer_group = None
        self._waiting_for_browser_auth = False
        self._browser_port_available = False
        self.status.setText(self._browser_port_wait_message())
        self._update_action_button()
        return True

    def _switch_to_token_authorization(self) -> bool:
        if not self._uses_browser_auth():
            return False
        self._drive_local_auth = False
        self.status.setText("Use token authorization.")
        if self._question and self._option_name(self._question.option) == "config_is_local":
            self._run_rclone(
                lambda: rclone_wizard.continue_remote(
                    self._remote_name,
                    self._remote_type,
                    self._state,
                    "false",
                    self._initial_config_args(),
                ),
                reset_port_retry=False,
            )
            return True
        if self._remote_name:
            self._run_rclone(
                lambda: rclone_wizard.start_remote(
                    self._remote_name,
                    self._remote_type,
                    self._initial_config_args(),
                ),
                reset_port_retry=False,
            )
            return True
        return False

    def _show_setup_view(self, visible: bool) -> None:
        if hasattr(self, "initial_frame"):
            self.initial_frame.setVisible(visible)
        if visible:
            self.question_frame.hide()

    def _initial_config_args(self) -> list[str]:
        if self._remote_type == "drive":
            return rclone_wizard._drive_config_args(
                client_id=self._drive_client_id,
                client_secret=self._drive_client_secret,
                local_auth=self._drive_local_auth,
                shared_drive=self._drive_shared_drive,
                team_drive=self._drive_team_drive,
            )
        if self._remote_type == "gphotos":
            return [
                "client_id",
                self._drive_client_id.strip(),
                "client_secret",
                self._drive_client_secret.strip(),
                "read_only",
                "true" if self.fields["gphotos_read_only"].isChecked() else "false",
                "config_edit_advanced",
                "false",
                "config_is_local",
                "true" if self._drive_local_auth else "false",
                "read_size",
                "true",
            ]
        if self._remote_type in OAUTH_REMOTE_TYPES:
            return ["config_is_local", "true" if self._drive_local_auth else "false"]
        if self._remote_type == "s3":
            return self._s3_config_args()
        if self._remote_type == "koofr":
            return self._koofr_config_args()
        if self._remote_type == "protondrive":
            return self._proton_config_args()
        if self._remote_type == "iclouddrive":
            return self._icloud_config_args()
        if self._remote_type == "webdav":
            return self._webdav_config_args()
        return []

    def _s3_config_args(self) -> list[str]:
        provider = self._s3_provider_value()
        args = [
            "provider",
            provider,
            "access_key_id",
            self.fields["s3_access_key_id"].text().strip(),
            "secret_access_key",
            self.fields["s3_secret_access_key"].text().strip(),
        ]
        region = self.fields["s3_region"].text().strip()
        endpoint = self.fields["s3_endpoint"].text().strip()
        if region:
            args.extend(["region", region])
        if endpoint:
            args.extend(["endpoint", endpoint])
        if provider.lower() == "cloudflare":
            args.extend(["acl", "private", "no_check_bucket", "true"])
        return args

    def _koofr_config_args(self) -> list[str]:
        return [
            "provider",
            "koofr",
            "user",
            self.fields["koofr_user"].text().strip(),
            "password",
            self.fields["koofr_pass"].text().strip(),
        ]

    def _proton_config_args(self) -> list[str]:
        args = [
            "username",
            self.fields["proton_user"].text().strip(),
            "password",
            self.fields["proton_pass"].text(),
            "enable_caching",
            "false",
        ]
        code = self.fields["proton_2fa"].text().strip()
        mailbox_password = self.fields["proton_mailbox_pass"].text()
        if code:
            args.extend(["2fa", code])
        if mailbox_password:
            args.extend(["mailbox_password", mailbox_password])
        return args

    def _icloud_config_args(self) -> list[str]:
        return [
            "service",
            str(self.fields["icloud_service"].currentData() or "drive"),
            "apple_id",
            self.fields["icloud_user"].text().strip(),
            "password",
            self.fields["icloud_pass"].text(),
        ]

    def _webdav_config_args(self) -> list[str]:
        args = [
            "url",
            self.fields["webdav_url"].text().strip(),
            "vendor",
            self._webdav_vendor_value(),
        ]
        user = self.fields["webdav_user"].text().strip()
        password = self.fields["webdav_pass"].text()
        if user:
            args.extend(["user", user])
        if password:
            args.extend(["pass", password])
        return args

    def _initial_mount_remote_path(self) -> str:
        if self._remote_type == "s3":
            return self.fields["s3_remote_path"].text().strip().strip("/")
        return ""

    def _s3_provider_choice(self) -> dict[str, str]:
        choice = self.fields["s3_provider"].currentData()
        if isinstance(choice, dict):
            return {str(key): str(value) for key, value in choice.items()}
        provider = str(choice or "Minio")
        for option in S3_PROVIDER_OPTIONS:
            if option["provider"].casefold() == provider.casefold():
                return dict(option)
        return dict(S3_PROVIDER_OPTIONS[-1])

    def _s3_provider_value(self) -> str:
        return self._s3_provider_choice().get("provider", "Minio") or "Minio"

    def _s3_provider_config_name(self) -> str:
        return self._s3_provider_choice().get("config_name", "S3") or "S3"

    def _webdav_vendor_choice(self) -> dict[str, str]:
        choice = self.fields["webdav_vendor"].currentData()
        if isinstance(choice, dict):
            return {str(key): str(value) for key, value in choice.items()}
        vendor = str(choice or "other")
        for option in WEBDAV_VENDOR_OPTIONS:
            if option["vendor"] == vendor:
                return dict(option)
        return dict(WEBDAV_VENDOR_OPTIONS[-1])

    def _webdav_vendor_value(self) -> str:
        return self._webdav_vendor_choice().get("vendor", "other") or "other"

    def _webdav_provider_config_name(self) -> str:
        return self._webdav_vendor_choice().get("config_name", "WebDAV") or "WebDAV"

    def _save_initial_mount_settings(self) -> None:
        initial_remote_path = getattr(self, "_initial_remote_path", "")
        if not initial_remote_path:
            return
        settings = load_mount_settings()
        current = settings.get(self._remote_name) or MountSettings()
        settings[self._remote_name] = MountSettings(
            mount_path=current.mount_path,
            remote_path=initial_remote_path,
            mount_flags=list(current.mount_flags),
            auto_mount=current.auto_mount,
            enabled=current.enabled,
            order=current.order,
        )
        save_mount_settings(settings)

    def _automatic_answer(self, step: rclone_wizard.RcloneConfigStep) -> str | None:
        option_name = self._option_name(step.option)
        if option_name == "config_is_local":
            return "true" if self._drive_local_auth else "false"
        if self._is_warning_acknowledgement(step.option):
            return "true"
        if option_name in {"config_edit_advanced", "edit_advanced"}:
            return "false"
        if self._is_drive_shared_drive_choice(step.option):
            return "true" if self._drive_shared_drive else "false"
        if self._is_drive_shared_drive_id(step.option):
            return self._drive_team_drive
        return None

    def _is_drive_shared_drive_choice(self, option: dict[str, Any]) -> bool:
        option_name = self._option_name(option)
        if option_name in {"config_team_drive", "config_shared_drive"}:
            return True
        text = self._option_search_text(option)
        if "shared drive" not in text and "team drive" not in text:
            return False
        option_type = str(option.get("Type", "")).lower()
        examples = option.get("Examples") if isinstance(option.get("Examples"), list) else []
        example_values = {str(example.get("Value", "")).lower() for example in examples}
        return option_type == "bool" or {"true", "false"}.issubset(example_values)

    def _is_drive_shared_drive_id(self, option: dict[str, Any]) -> bool:
        option_name = self._option_name(option)
        if option_name in {"team_drive", "shared_drive_id", "team_drive_id"}:
            return True
        text = self._option_search_text(option)
        return ("shared drive" in text or "team drive" in text) and "id" in text

    def _is_warning_acknowledgement(self, option: dict[str, Any]) -> bool:
        option_name = self._option_name(option).casefold()
        option_type = str(option.get("Type", "")).casefold()
        text = self._option_search_text(option)
        if option_type != "bool":
            return False
        return "warning" in option_name or text.startswith(("warning", "important"))

    def _option_search_text(self, option: dict[str, Any]) -> str:
        parts = [self._option_name(option)]
        for key in ("Help", "ShortOpt", "DefaultStr"):
            value = option.get(key)
            if value is not None:
                parts.append(str(value))
        return " ".join(parts).lower()

    def _mount_created_remote(self) -> None:
        self._set_busy(True, message=f"Mounting {self._remote_display_name()}...")

        def worker() -> None:
            for remote in core.load_remotes():
                if remote.name == self._remote_name:
                    success, message = core.mount_remote(remote)
                    self._bridge.mount_finished.emit(success, message)
                    return
            self._bridge.mount_finished.emit(False, f"{self._remote_name} was created but could not be found.")

        threading.Thread(target=worker, daemon=True).start()

    def _check_created_remote(self) -> None:
        self._set_busy(True, message=f"Checking {self._remote_display_name()}...")

        def worker() -> None:
            for remote in core.load_remotes():
                if remote.name == self._remote_name:
                    success, message = core.check_remote_connection(remote)
                    self._bridge.remote_checked.emit(success, message)
                    return
            self._bridge.remote_checked.emit(False, f"{self._remote_name} was created but could not be found.")

        threading.Thread(target=worker, daemon=True).start()

    def _handle_remote_checked(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if not success:
            display_name = self._remote_display_name()
            self._cleanup_incomplete_remote()
            self._reset_after_failed_registration()
            self._warning(
                "Add remote",
                f"{display_name} could not be reached.\n\n{_clean_message(message)}",
            )
            return
        self._finish_success()

    def _handle_mount_finished(self, success: bool, message: str) -> None:
        self._set_busy(False)
        if not success:
            display_name = self._remote_display_name()
            self._cleanup_incomplete_remote()
            self._reset_after_failed_registration()
            self._warning(
                "Add remote",
                f"{display_name} was created, but Mountlet could not mount it.\n\n"
                f"{_clean_message(message)}",
            )
            return
        self._finish_success()

    def _reset_after_failed_registration(self) -> None:
        self._show_setup_view(True)
        self._remote_name = ""
        self._remote_alias = ""
        self._state = ""
        self._question = None
        self._answer_field = None
        self._answer_group = None
        self._waiting_for_browser_auth = False
        self._update_browser_port_status()
        self._update_action_button()

    def _finish_success(self) -> None:
        self._completed = True
        self._stop_port_timer()
        self._close_message_boxes()
        _wizard_pending_remote_names.discard(self._remote_name)
        self.dialog.accept()

    def _set_busy(self, busy: bool, *, message: str | None = None) -> None:
        self.action_button.setEnabled(False if busy else True)
        setup_editable = not busy and self._question is None and self._remote_name == ""
        self.fields["name"].setEnabled(setup_editable)
        self.fields["provider"].setEnabled(setup_editable)
        self.fields["credential_source"].setEnabled(setup_editable)
        self.fields["auth_group"].setEnabled(setup_editable)
        self.fields["drive_group"].setEnabled(setup_editable)
        self.fields["connect_after_create"].setEnabled(setup_editable)
        self.fields["shared_drive_id"].setEnabled(
            setup_editable and self.fields["shared_drive"].isChecked()
        )
        for field_name in (
            "s3_provider",
            "s3_endpoint",
            "s3_region",
            "s3_access_key_id",
            "s3_secret_access_key",
            "s3_remote_path",
            "gphotos_read_only",
            "koofr_user",
            "koofr_pass",
            "proton_user",
            "proton_pass",
            "proton_2fa",
            "proton_mailbox_pass",
            "icloud_service",
            "icloud_user",
            "icloud_pass",
            "webdav_url",
            "webdav_vendor",
            "webdav_user",
            "webdav_pass",
        ):
            self.fields[field_name].setEnabled(setup_editable)
        self._apply_credential_choice(enabled=setup_editable)
        self.status.setText((message or self._busy_message()) if busy else "")

    def _offer_to_stop_stuck_rclone(self, owner_hint: str) -> bool:
        owner = _port_owner_from_hint(owner_hint)
        if owner is None:
            return False
        name, pid = owner
        answer = self.qt.QMessageBox.question(
            self.dialog,
            "Add remote",
            f"{name} is using sign-in port {RCLONE_OAUTH_LOCAL_PORT}.\n\n"
            "If this is a previous rclone sign-in attempt, stopping it should free the port.\n\n"
            f"Stop process {pid} and try again?",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.Yes,
        )
        if answer != self.qt.QMessageBox.StandardButton.Yes:
            return False
        return _terminate_process_id(pid)

    def _apply_provider_choice(self) -> None:
        if not self.fields:
            return
        remote_type = self.fields["provider"].currentData() or "drive"
        self._remote_type = remote_type
        is_drive = remote_type == "drive"
        is_gphotos = remote_type == "gphotos"
        uses_browser_auth = remote_type in OAUTH_REMOTE_TYPES
        is_s3 = remote_type == "s3"
        is_koofr = remote_type == "koofr"
        is_proton = remote_type == "protondrive"
        is_icloud = remote_type == "iclouddrive"
        is_webdav = remote_type == "webdav"
        is_external = remote_type == EXTERNAL_RCLONE_CONFIG_PROVIDER
        self._set_form_row_visible(self.fields["name"], not is_external)
        self._set_form_row_visible(self.fields["credential_source"], is_drive)
        self._set_form_row_visible(self.fields["client_id"], is_drive or is_gphotos)
        self._set_form_row_visible(self.fields["client_secret"], is_drive or is_gphotos)
        self._set_form_row_visible(self.fields["gphotos_read_only"], is_gphotos)
        for field_name in (
            "drive_group",
            "shared_drive_id",
        ):
            self._set_form_row_visible(self.fields[field_name], is_drive)
        for field_name in (
            "s3_provider",
            "s3_endpoint",
            "s3_region",
            "s3_access_key_id",
            "s3_secret_access_key",
            "s3_remote_path",
            "s3_help",
        ):
            self._set_form_row_visible(self.fields[field_name], is_s3)
        for field_name in (
            "koofr_user",
            "koofr_pass",
            "koofr_help",
        ):
            self._set_form_row_visible(self.fields[field_name], is_koofr)
        for field_name in (
            "proton_user",
            "proton_pass",
            "proton_2fa",
            "proton_mailbox_pass",
            "proton_help",
        ):
            self._set_form_row_visible(self.fields[field_name], is_proton)
        for field_name in (
            "icloud_service",
            "icloud_user",
            "icloud_pass",
            "icloud_help",
        ):
            self._set_form_row_visible(self.fields[field_name], is_icloud)
        for field_name in (
            "webdav_url",
            "webdav_vendor",
            "webdav_help",
            "webdav_user",
            "webdav_pass",
        ):
            self._set_form_row_visible(self.fields[field_name], is_webdav)
        self._set_form_row_visible(self.fields["connect_after_create"], not is_external)
        self._set_form_row_visible(self.fields["credential_help"], is_drive)
        self._set_form_row_visible(self.fields["gphotos_help"], is_gphotos)
        self._set_form_row_visible(self.fields["auth_group"], uses_browser_auth)
        self.fields["name"].setPlaceholderText(self._remote_name_placeholder(remote_type))
        self.fields["connect_after_create"].setToolTip(
            "Mount the new remote as an operating-system folder after setup succeeds."
        )
        self._apply_credential_choice()
        if is_s3:
            self._apply_s3_provider_choice()
        if is_webdav:
            self._apply_webdav_vendor_choice()
        self._update_browser_port_status()
        self.dialog.adjustSize()

    def _apply_s3_provider_choice(self) -> None:
        if not getattr(self, "fields", None):
            return
        choice = self._s3_provider_choice()
        endpoint = choice.get("endpoint", "")
        current_endpoint = self.fields["s3_endpoint"].text().strip()
        if not current_endpoint or current_endpoint in _default_s3_endpoints():
            self.fields["s3_endpoint"].setText("" if choice.get("hide_endpoint") else endpoint)
        region = choice.get("region", "")
        current_region = self.fields["s3_region"].text().strip()
        if not current_region or current_region in _default_s3_regions():
            self.fields["s3_region"].setText(region)
        self.fields["s3_endpoint"].setPlaceholderText(endpoint or "Optional")
        self.fields["s3_endpoint"].setToolTip(choice.get("endpoint_tip", "S3 API endpoint."))
        self.fields["s3_region"].setPlaceholderText(region or "us-east-1")
        self.fields["s3_region"].setToolTip(choice.get("region_tip", "S3 region."))
        self.fields["s3_access_key_id"].setPlaceholderText(choice.get("access_key", "Access key"))
        self.fields["s3_secret_access_key"].setPlaceholderText(choice.get("secret_key", "Secret key"))
        self.fields["s3_remote_path"].setPlaceholderText(choice.get("bucket", "Bucket name or bucket/folder"))
        self.fields["s3_remote_path"].setToolTip(choice.get("bucket_tip", "Bucket name or bucket/folder."))
        self.fields["s3_help"].setText(choice.get("instructions", ""))
        is_s3 = (self.fields["provider"].currentData() or "drive") == "s3"
        self._set_form_row_visible(self.fields["s3_endpoint"], is_s3 and not choice.get("hide_endpoint"))
        self._set_form_row_visible(self.fields["s3_help"], is_s3)
        self._update_action_button()

    def _apply_webdav_vendor_choice(self) -> None:
        if not getattr(self, "fields", None):
            return
        choice = self._webdav_vendor_choice()
        url = choice.get("url", "https://cloud.example.com/webdav")
        fixed_url = choice.get("fixed_url", "").casefold() in {"true", "1", "yes"}
        if fixed_url:
            self.fields["webdav_url"].setText(url)
        elif self.fields["webdav_url"].text().strip() in _fixed_webdav_urls():
            self.fields["webdav_url"].clear()
        self.fields["webdav_url"].setPlaceholderText(url)
        self.fields["webdav_url"].setToolTip(choice.get("url_tip", "The WebDAV endpoint URL."))
        self.fields["webdav_user"].setPlaceholderText(choice.get("user", "Optional"))
        self.fields["webdav_user"].setToolTip(choice.get("user_tip", "Optional WebDAV username."))
        self.fields["webdav_pass"].setPlaceholderText(choice.get("password", "Optional"))
        self.fields["webdav_pass"].setToolTip(choice.get("password_tip", "Optional WebDAV password."))
        self.fields["webdav_help"].setText(choice.get("instructions", ""))
        is_webdav = (self.fields["provider"].currentData() or "drive") == "webdav"
        self._set_form_row_visible(self.fields["webdav_url"], is_webdav and not fixed_url)
        self._set_form_row_visible(self.fields["webdav_help"], is_webdav)
        self._update_action_button()

    def _set_form_row_visible(self, widget: Any, visible: bool) -> None:
        try:
            widget.setVisible(visible)
            label = self.form.labelForField(widget)
            if label is not None:
                label.setVisible(visible)
        except Exception:
            pass

    def _remote_name_placeholder(self, remote_type: str) -> str:
        placeholders = {
            "drive": "Personal Drive",
            "gphotos": "Personal Photos",
            "dropbox": "Personal Dropbox",
            "onedrive": "Personal OneDrive",
            "box": "Work Box",
            "pcloud": "Personal pCloud",
            "iclouddrive": "Personal iCloud",
            "koofr": "Personal Koofr",
            "protondrive": "Personal Proton Drive",
            "s3": "Archive S3",
            "webdav": "Nextcloud",
        }
        return placeholders.get(remote_type, "Cloud storage")

    def _apply_credential_choice(self, *, enabled: bool | None = None) -> None:
        if not self.fields:
            return
        remote_type = getattr(self, "_remote_type", "drive")
        if remote_type == "gphotos":
            allow_edit = enabled if enabled is not None else self._question is None
            self.fields["client_id"].setEnabled(allow_edit)
            self.fields["client_secret"].setEnabled(allow_edit)
            return
        if remote_type != "drive":
            self.fields["client_id"].clear()
            self.fields["client_secret"].clear()
            self.fields["client_id"].setEnabled(False)
            self.fields["client_secret"].setEnabled(False)
            return
        choice = self.fields["credential_source"].currentData()
        using_builtin = choice == DRIVE_CREDENTIAL_SOURCE_BUILTIN
        using_custom = choice == DRIVE_CREDENTIAL_SOURCE_CUSTOM
        using_existing = isinstance(choice, core.DriveOAuthCredentials)
        if using_existing:
            self.fields["client_id"].setText(choice.client_id)
            self.fields["client_secret"].setText(choice.client_secret)
        elif using_builtin:
            self.fields["client_id"].clear()
            self.fields["client_secret"].clear()
        allow_edit = (enabled if enabled is not None else self._question is None) and using_custom
        self.fields["client_id"].setEnabled(allow_edit)
        self.fields["client_secret"].setEnabled(allow_edit)

    def _busy_message(self) -> str:
        if getattr(self, "_waiting_for_browser_auth", False):
            return "Waiting for browser authentication. A sign-in page should open in your browser."
        if self._question and self._option_name(self._question.option) == "config_is_local":
            if self._answer_value() == "true":
                return "Waiting for browser authentication. A sign-in page should open in your browser."
            return "Waiting for rclone."
        return "Waiting for rclone..."

    def _show_question(self, step: rclone_wizard.RcloneConfigStep) -> None:
        self._question = step
        self._state = step.state
        self._clear_layout(self.question_layout)
        self._answer_group = None
        option = step.option
        title = self.qt.QLabel(self._question_title(option))
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        self._answer_kind, self._answer_field = self._answer_widget(option)
        self.question_layout.addRow(title)
        help_message = self._question_help(option)
        if help_message:
            help_text = self.qt.QLabel(help_message)
            help_text.setWordWrap(True)
            help_text.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
            help_text.setOpenExternalLinks(True)
            self.question_layout.addRow(help_text)
        self.question_layout.addRow("Answer", self._answer_field)
        self.question_frame.show()
        self.status.setText("")
        self._update_action_button()
        self.dialog.adjustSize()
        self._schedule_focus_widget(self._answer_focus_widget())

    def _answer_widget(self, option: dict[str, Any]) -> tuple[str, Any]:
        option_type = str(option.get("Type", "")).lower()
        option_name = self._option_name(option)
        default = option.get("DefaultStr")
        if default is None:
            default = option.get("Default", "")
        default_text = str(default).lower() if isinstance(default, bool) else str(default or "")
        examples = option.get("Examples") if isinstance(option.get("Examples"), list) else []
        if option_type == "bool":
            return self._bool_radio_widget(option_name, default_text, examples)
        if examples and option.get("Exclusive"):
            field = self.qt.QComboBox()
            selected = 0
            for index, example in enumerate(examples):
                value = str(example.get("Value", ""))
                label = str(example.get("Help", "")).strip().splitlines()[0] if example.get("Help") else value
                field.addItem(label, value)
                if value == default_text:
                    selected = index
            field.setCurrentIndex(selected)
            return "combo", field
        if option.get("IsPassword"):
            field = self.qt.QLineEdit()
            field.setEchoMode(self.qt.QLineEdit.EchoMode.Password)
            field.setText(default_text)
            return "text", field
        if option_name.endswith("token"):
            field = self.qt.QPlainTextEdit()
            field.setPlainText(default_text)
            field.setMinimumHeight(76)
            return "plain", field
        field = self.qt.QLineEdit()
        field.setText(default_text)
        return "text", field

    def _answer_value(self) -> str:
        if self._answer_field is None:
            return ""
        if self._answer_kind == "bool":
            return "true" if self._answer_field.isChecked() else "false"
        if self._answer_kind == "radio":
            if self._answer_group is None or self._answer_group.checkedButton() is None:
                return ""
            return str(self._answer_group.checkedButton().property("answerValue") or "")
        if self._answer_kind == "combo":
            return str(self._answer_field.currentData() or "")
        if self._answer_kind == "plain":
            return self._answer_field.toPlainText().strip()
        return self._answer_field.text().strip()

    def _valid_remote_name(self, name: str) -> bool:
        return core.valid_remote_alias(name)

    def _display_name_exists(
        self,
        alias: str,
        remote_type: str,
        remotes: list[core.RemoteInfo],
        *,
        provider_name: str | None = None,
    ) -> bool:
        normalized_alias = alias.casefold()
        normalized_type = remote_type.casefold()
        normalized_provider = (provider_name or self._provider_config_name(remote_type)).casefold()
        return any(
            remote.alias.casefold() == normalized_alias
            and remote.backend_type.casefold() == normalized_type
            and (
                remote_type.casefold() not in {"s3", "webdav"}
                or remote.provider.casefold() == normalized_provider
            )
            for remote in remotes
        )

    def _config_remote_name(
        self,
        alias: str,
        remote_type: str,
        remotes: list[core.RemoteInfo],
        *,
        provider_name: str | None = None,
    ) -> str:
        existing_names = {remote.name for remote in remotes}
        provider_name = provider_name or self._provider_config_name(remote_type)
        candidate = f"{alias}__{provider_name}"
        if candidate not in existing_names:
            return candidate
        index = 2
        while f"{alias} {index}__{provider_name}" in existing_names:
            index += 1
        return f"{alias} {index}__{provider_name}"

    def _provider_config_name(self, remote_type: str) -> str:
        normalized = remote_type.strip().lower()
        return REMOTE_CONFIG_SUFFIXES.get(normalized, normalized or "Remote")

    def _selected_provider_config_name(self, remote_type: str) -> str:
        fields = getattr(self, "fields", None)
        if remote_type.strip().lower() == "s3" and fields:
            return self._s3_provider_config_name()
        if remote_type.strip().lower() == "webdav" and fields:
            return self._webdav_provider_config_name()
        return self._provider_config_name(remote_type)

    def _provider_display_name(self, alias: str, remote_type: str, provider_name: str | None = None) -> str:
        return f"{alias} ({provider_name or self._provider_config_name(remote_type)})"

    def _remote_display_name(self) -> str:
        alias = getattr(self, "_remote_alias", "") or self._remote_name
        return self._provider_display_name(alias, self._remote_type, self._selected_provider_config_name(self._remote_type))

    def _bool_radio_widget(
        self,
        option_name: str,
        default_text: str,
        examples: list[dict[str, Any]],
    ) -> tuple[str, Any]:
        widget = self.qt.QWidget()
        layout = self.qt.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        group = self.qt.QButtonGroup(widget)
        options = self._bool_radio_options(option_name, examples)
        selected_value = "true" if default_text.lower() in {"true", "1", "yes", "on"} else "false"
        for index, (value, label) in enumerate(options):
            button = self.qt.QRadioButton(label)
            button.setProperty("answerValue", value)
            group.addButton(button, index)
            layout.addWidget(button)
            if value == selected_value:
                button.setChecked(True)
        if group.checkedButton() is None and group.buttons():
            group.buttons()[0].setChecked(True)
        group.buttonClicked.connect(lambda _button=None: self._update_action_button())
        self._answer_group = group
        return "radio", widget

    def _bool_radio_options(self, option_name: str, examples: list[dict[str, Any]]) -> list[tuple[str, str]]:
        if option_name == "config_is_local":
            return [
                ("true", "Open the browser on this computer"),
                ("false", "Authorize from another computer"),
            ]
        if option_name in {"team_drive", "config_team_drive"}:
            return [
                ("false", "My Drive"),
                ("true", "Shared drive"),
            ]
        values: list[tuple[str, str]] = []
        for example in examples:
            value = str(example.get("Value", "")).lower()
            if value not in {"true", "false"}:
                continue
            label = str(example.get("Help", "")).strip().splitlines()[0] if example.get("Help") else value.title()
            values.append((value, label))
        return values or [("true", "Yes"), ("false", "No")]

    def _option_name(self, option: dict[str, Any]) -> str:
        return str(option.get("Name", "")).strip()

    def _question_title(self, option: dict[str, Any]) -> str:
        option_name = self._option_name(option)
        if option_name == "config_is_local":
            return f"Connect {self._provider_label(self._remote_type)}"
        if option_name == "config_token":
            return "Paste authorization token"
        if option_name in {"team_drive", "config_team_drive"}:
            return "Shared drive"
        return _field_label(option_name or "Option")

    def _question_help(self, option: dict[str, Any]) -> str:
        option_name = self._option_name(option)
        if option_name == "config_is_local":
            return "Use this computer unless browser sign-in is busy."
        if option_name == "config_team_drive":
            return "Choose where the files live."
        if option_name == "team_drive":
            return "Paste the shared drive ID."
        return str(option.get("Help", "")).strip().split("\n\n", 1)[0]

    def _provider_label(self, remote_type: str) -> str:
        for label, backend_type in REMOTE_PROVIDER_OPTIONS:
            if backend_type == remote_type:
                return label
        return remote_type

    def _question_button_text(self) -> str:
        if self._question and self._option_name(self._question.option) == "config_is_local":
            return "Open browser" if self._answer_value() == "true" else "Continue"
        if self._question and self._option_name(self._question.option) == "config_token":
            return "Finish setup"
        return "Continue"

    def _reject(self) -> None:
        self._cancelled = True
        self._stop_port_timer()
        self._close_message_boxes()
        self._cleanup_incomplete_remote()
        self.dialog.reject()

    def _stop_port_timer(self) -> None:
        try:
            self._port_timer.stop()
        except Exception:
            pass

    def _warning(self, title: str, message: str) -> None:
        try:
            box = self.qt.QMessageBox(self.dialog)
            box.setIcon(self.qt.QMessageBox.Icon.Warning)
            box.setWindowTitle(title)
            box.setText(message)
            box.setStandardButtons(self.qt.QMessageBox.StandardButton.Ok)
            try:
                box.setWindowModality(self.qt.Qt.WindowModality.WindowModal)
            except Exception:
                pass
            self._message_boxes.append(box)
            box.finished.connect(lambda _result=0, item=box: self._untrack_message_box(item))
            box.show()
            box.raise_()
            box.activateWindow()
        except Exception:
            self.qt.QMessageBox.warning(self.dialog, title, message)

    def _untrack_message_box(self, box: Any) -> None:
        self._message_boxes = [item for item in getattr(self, "_message_boxes", []) if item is not box]

    def _close_message_boxes(self) -> None:
        boxes = list(getattr(self, "_message_boxes", []))
        self._message_boxes = []
        for box in boxes:
            try:
                box.close()
            except Exception:
                pass

    def _cleanup_incomplete_remote(self) -> None:
        if not self._remote_name or self._completed:
            return
        _wizard_pending_remote_names.discard(self._remote_name)
        rclone_wizard.cancel_remote_config(self._remote_name)
        core.delete_rclone_remote(self._remote_name)
        settings = load_mount_settings()
        if self._remote_name in settings:
            settings.pop(self._remote_name)
            save_mount_settings(settings)

    def _created_remote_has_credentials(self) -> bool:
        for remote in core.load_remotes():
            if remote.name != self._remote_name:
                continue
            return core._remote_section_is_configured(remote.backend_type, remote.extra_info)
        return False

    def _clear_layout(self, layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class MountletWindow:
    def __init__(self, tray_app: "MountletTray") -> None:
        self.tray_app = tray_app
        self.qt = tray_app.qt
        self.desktop = _desktop_services(self.qt)
        self._usage_cache: dict[str, core.StorageUsage] = {}
        self._connection_cache: dict[str, bool] = {}
        self._usage_pending: set[str] = set()
        self._action_pending: set[str] = set()
        self._row_widgets: dict[str, SimpleNamespace] = {}
        self._current_remote_names: list[str] = []
        self._selected_remote_name = ""
        self._name_column_width = 160
        self._refresh_pending = False
        self._child_dialogs: list[Any] = []
        self._child_dialog_owners: dict[Any, Any] = {}
        self._last_child_offsets: dict[Any, tuple[int, int]] = {}
        self._window_stack_hidden = False
        self._keep_above = False
        self._keep_above_button: Any | None = None
        self._settings_button: Any | None = None
        self._push_sync_button: Any | None = None
        self._pull_sync_button: Any | None = None
        self._purchase_license_button: Any | None = None
        self._license_lock_banner: Any | None = None
        self._license_prompted_for_lock = False
        self._sort_button: Any | None = None
        self._reverse_button: Any | None = None
        self._all_cache_sync_button: Any | None = None
        self._remove_all_offline_button: Any | None = None
        self._clear_all_cache_button: Any | None = None
        self._app_menu: Any | None = None
        self._mount_menu: Any | None = None
        self._config_menu: Any | None = None
        self._remote_sync_metadata: dict[str, object] | None = None
        self._remote_sync_check_pending = False
        self._offline_reconcile_running: set[str] = set()
        self._offline_reconcile_scheduled: set[str] = set()
        self._deferred_offline_conflicts: set[tuple[str, str, float, float]] = set()
        self._offline_watched_paths: set[str] = set()
        self._offline_file_watcher: Any | None = None
        self._offline_change_poll_timer: Any | None = None
        self._remote_change_poll_timer: Any | None = None
        self._remote_change_poll_offsets: dict[str, int] = {}
        self._remote_change_poll_index = 0
        self._position_after_fit = False
        self._last_tray_anchor: Any | None = None
        self._last_popup_position: tuple[int, int] | None = None
        self._drag_offset: Any | None = None
        self._deactivated_for_tray = False
        self._focus_owner = "main"
        self._bridge = self._make_bridge()
        self._bridge.storage_ready.connect(self._handle_storage_ready)
        self._bridge.action_finished.connect(self._handle_action_finished)
        self._bridge.bulk_action_finished.connect(self._handle_bulk_action_finished)
        self._bridge.folder_opened.connect(self._handle_folder_opened)
        self._bridge.sync_metadata_ready.connect(self._handle_sync_metadata_ready)
        self._bridge.offline_reconcile_ready.connect(self._handle_offline_reconcile_ready)
        self._bridge.cache_sync_debug_ready.connect(self._handle_cache_sync_debug_ready)
        self.window = self._make_main_window()
        self.window.setWindowTitle("Mountlet")
        self.window.setWindowIcon(self.tray_app.icon)
        self.file_browser = CompactCloudBrowser(
            self.qt,
            self.window,
            remotes=_load_visible_remotes,
            notify=lambda title, message, success: self.tray_app._notify(title, message, success=success),
            open_mount=self._open_remote_path,
            open_file=self.desktop.open_file,
            open_local_folder=lambda path: self.desktop.open_folder(str(path)),
            toggle_mount=self._run_switch_action,
            sync_paths=self._sync_cached_paths,
            file_manager_label=self.desktop.file_manager_label,
            embedded=bool(getattr(self.tray_app, "_is_wayland", False)),
            layout_changed=self._browser_layout_changed,
            local_files_changed=self._local_browser_files_changed,
        )
        self.window.focus_remote_row = self._focus_current_remote_row
        self.window.update_focus_style = self._update_main_focus_style
        self.window.set_mountlet_focus_owner = self._set_focus_owner
        self.window.mountlet_focus_owner = lambda: getattr(self, "_focus_owner", "main")
        self.file_browser.window.setWindowIcon(self.tray_app.icon)
        self.file_browser.preload(_load_visible_remotes())
        self._setup_offline_change_tracking()
        self._close_filter = self._make_close_filter()
        self.window.installEventFilter(self._close_filter)
        self.window.resize(720, 260)
        self._build_app_menu()

    def _desktop_api(self) -> DesktopServices:
        desktop = getattr(self, "desktop", None)
        if desktop is None:
            desktop = _desktop_services(getattr(self, "qt", None))
            self.desktop = desktop
        return desktop

    def _local_browser_files_changed(self) -> None:
        self._refresh_offline_file_watches()
        self._update_app_cache_buttons()

    def _make_bridge(self) -> Any:
        qt = self.qt

        class Bridge(qt.QObject):
            storage_ready = qt.Signal(str, object)
            action_finished = qt.Signal(str, bool, str)
            bulk_action_finished = qt.Signal(str, object, object)
            folder_opened = qt.Signal(bool)
            sync_metadata_ready = qt.Signal(object, object)
            offline_reconcile_ready = qt.Signal(str, object, object)
            cache_sync_debug_ready = qt.Signal(str)

        return Bridge()

    def _make_main_window(self) -> Any:
        qt = self.qt
        outer = self

        class MainWindow(qt.QMainWindow):
            def __init__(self) -> None:
                tray_app = getattr(outer, "tray_app", None)
                base_name = _main_window_type_name(
                    bool(getattr(tray_app, "_is_macos", False)),
                    bool(getattr(tray_app, "_is_wayland", False)),
                )
                if _main_window_uses_native_frame(bool(getattr(tray_app, "_is_wayland", False))):
                    try:
                        super().__init__(None, getattr(qt.Qt.WindowType, base_name))
                        return
                    except Exception:
                        pass
                    super().__init__()
                    return
                flags = _frameless_window_flags(qt, base_name)
                if flags is not None:
                    try:
                        super().__init__(None, flags)
                        return
                    except Exception:
                        pass
                super().__init__()
                _apply_frameless_window_flags(qt, self, base_name=base_name)

            def closeEvent(self, event: object) -> None:
                try:
                    if outer._handle_window_close(event):
                        return
                except Exception:
                    pass

            def keyPressEvent(self, event: Any) -> None:
                if outer._handle_main_key(event):
                    return
                super().keyPressEvent(event)

            def changeEvent(self, event: Any) -> None:
                super().changeEvent(event)
                if event.type() in {
                    qt.QEvent.Type.ActivationChange,
                    qt.QEvent.Type.WindowActivate,
                    qt.QEvent.Type.WindowDeactivate,
                }:
                    if event.type() == qt.QEvent.Type.WindowActivate:
                        outer._set_focus_owner("main")
                        outer._handle_main_window_activation(active=True)
                    elif event.type() == qt.QEvent.Type.WindowDeactivate:
                        outer._handle_main_window_activation(active=False)
                try:
                    super().closeEvent(event)
                except Exception:
                    pass

        return MainWindow()

    def _make_close_filter(self) -> Any:
        qt = self.qt
        outer = self

        class CloseFilter(qt.QObject):
            def eventFilter(self, watched: object, event: object) -> bool:
                try:
                    if watched is outer.window:
                        event_type = event.type()
                        if event_type == qt.QEvent.Type.Close:
                            return outer._handle_window_close(event)
                        if event_type == qt.QEvent.Type.WindowActivate:
                            outer._deactivated_for_tray = False
                            outer._set_focus_owner("main")
                            outer._handle_main_window_activation(active=True)
                        elif event_type == qt.QEvent.Type.WindowDeactivate:
                            outer._deactivated_for_tray = _windows_foreground_is_tray()
                            outer._handle_main_window_activation(active=False)
                except Exception:
                    return False
                return False

        return CloseFilter(self.window)

    def _handle_window_close(self, event: Any) -> bool:
        if self._tray_is_quitting():
            return False
        self._hide_window_stack()
        try:
            event.ignore()
        except Exception:
            pass
        return True

    def _handle_main_window_activation(self, *, active: bool) -> None:
        if active:
            self._refresh_file_browser_after_focus_return()

    def _app_window_is_active(self) -> bool:
        qt = getattr(self, "qt", None)
        application = getattr(qt, "QApplication", None)
        active_window = application.activeWindow() if application is not None else None
        if active_window is None:
            return bool(getattr(self.window, "isActiveWindow", lambda: False)())
        if active_window is self.window:
            return True
        file_browser = getattr(self, "file_browser", None)
        if file_browser is not None and active_window is getattr(file_browser, "window", None):
            return True
        return active_window in getattr(self, "_child_dialogs", [])

    def _refresh_file_browser_after_focus_return(self) -> None:
        file_browser = getattr(self, "file_browser", None)
        remote = getattr(file_browser, "remote", None)
        if file_browser is None:
            return
        self._scan_local_cache_changes()
        if remote is None or not file_browser.is_visible():
            return
        file_browser.invalidate(remote.name)

    def _setup_offline_change_tracking(self) -> None:
        self._setup_offline_file_watcher()
        self._setup_offline_change_polling()
        self._setup_remote_change_polling()
        self._scan_local_cache_changes()

    def _setup_offline_file_watcher(self) -> None:
        watcher_type = getattr(self.qt, "QFileSystemWatcher", None)
        if watcher_type is None:
            return
        try:
            watcher = watcher_type(self.window)
        except Exception:
            return
        watcher.fileChanged.connect(self._handle_local_cache_file_changed)
        watcher.directoryChanged.connect(self._handle_local_cache_directory_changed)
        self._offline_file_watcher = watcher
        self._refresh_offline_file_watches()

    def _refresh_offline_file_watches(self) -> None:
        watcher = getattr(self, "_offline_file_watcher", None)
        if watcher is None:
            return
        backend = self.file_browser.backend
        paths: set[str] = set()
        for remote_name, local_files in backend.managed_file_paths().items():
            root = backend.offline_path(remote_name, "")
            if root.is_dir():
                paths.add(str(root))
            for path in local_files:
                if path.is_file():
                    paths.add(str(path))
                parent = path.parent
                while True:
                    if parent.is_dir():
                        paths.add(str(parent))
                    if parent == root:
                        break
                    try:
                        parent.relative_to(root)
                    except ValueError:
                        break
                    parent = parent.parent
        current = set(getattr(self, "_offline_watched_paths", set()))
        removed = sorted(current - paths)
        added = sorted(paths - current)
        if removed:
            with suppress(Exception):
                watcher.removePaths(removed)
        if added:
            with suppress(Exception):
                watcher.addPaths(added)
        self._offline_watched_paths = paths

    def _handle_local_cache_file_changed(self, changed_path: str) -> None:
        self._rearm_offline_file_watch(changed_path)
        remote_name = self.file_browser.backend.remote_name_for_offline_path(Path(changed_path))
        if remote_name:
            self._refresh_file_browser_mount_state(remote_name)
            self._schedule_offline_reconcile(remote_name, delay_ms=500)

    def _handle_local_cache_directory_changed(self, changed_path: str) -> None:
        self._refresh_offline_file_watches()
        remote_name = self.file_browser.backend.remote_name_for_offline_path(Path(changed_path))
        if remote_name:
            self._refresh_file_browser_mount_state(remote_name)
        self._scan_local_cache_changes()

    def _rearm_offline_file_watch(self, changed_path: str) -> None:
        watcher = getattr(self, "_offline_file_watcher", None)
        watched = getattr(self, "_offline_watched_paths", set())
        if watcher is None:
            self._refresh_offline_file_watches()
            return
        if changed_path in watched:
            with suppress(Exception):
                watcher.removePath(changed_path)
            watched.discard(changed_path)
            self._offline_watched_paths = watched
        self._refresh_offline_file_watches()

    def _setup_offline_change_polling(self) -> None:
        timer_type = getattr(self.qt, "QTimer", None)
        if timer_type is None:
            return
        try:
            timer = timer_type(self.window)
            timer.setInterval(2000)
            timer.timeout.connect(self._scan_local_cache_changes)
            timer.start()
        except Exception:
            return
        self._offline_change_poll_timer = timer

    def _scan_local_cache_changes(self) -> None:
        if self._tray_is_quitting() or self._license_locked():
            return
        self._refresh_offline_file_watches()
        try:
            changed_remote_names = self.file_browser.backend.changed_managed_remote_names()
        except Exception:
            return
        if not isinstance(changed_remote_names, (list, tuple, set)):
            return
        pending = set(getattr(self, "_action_pending", set()))
        scheduled = set(getattr(self, "_offline_reconcile_scheduled", set()))
        running = set(getattr(self, "_offline_reconcile_running", set()))
        for remote_name in changed_remote_names:
            if remote_name in pending:
                continue
            if remote_name in running:
                if remote_name not in scheduled:
                    self._offline_reconcile_scheduled.add(remote_name)
                    self._refresh_file_browser_mount_state(remote_name)
                continue
            if remote_name not in scheduled:
                self._refresh_file_browser_mount_state(remote_name)
                self._schedule_offline_reconcile(remote_name)

    def _setup_remote_change_polling(self) -> None:
        existing = getattr(self, "_remote_change_poll_timer", None)
        if existing is not None:
            with suppress(Exception):
                existing.stop()
            self._remote_change_poll_timer = None
        interval_seconds = load_app_settings().remote_sync_interval_seconds
        if interval_seconds <= 0:
            return
        timer_type = getattr(self.qt, "QTimer", None)
        if timer_type is None:
            return
        try:
            timer = timer_type(self.window)
            timer.setInterval(max(1000, int(interval_seconds * 1000)))
            timer.timeout.connect(self._scan_remote_cache_changes)
            timer.start()
        except Exception:
            return
        self._remote_change_poll_timer = timer

    def _scan_remote_cache_changes(self) -> None:
        if self._tray_is_quitting() or self._license_locked():
            return
        pending = set(getattr(self, "_action_pending", set()))
        scheduled = set(getattr(self, "_offline_reconcile_scheduled", set()))
        running = set(getattr(self, "_offline_reconcile_running", set()))
        remotes = [
            remote
            for remote in _load_visible_remotes()
            if remote.name not in pending and remote.name not in scheduled and remote.name not in running
        ]
        if not remotes:
            return
        backend = self.file_browser.backend
        start = int(getattr(self, "_remote_change_poll_index", 0)) % len(remotes)
        for offset in range(len(remotes)):
            remote = remotes[(start + offset) % len(remotes)]
            try:
                paths = backend.managed_record_paths(remote.name)
            except Exception:
                continue
            if not paths:
                continue
            poll_offsets = getattr(self, "_remote_change_poll_offsets", {})
            path_offset = int(poll_offsets.get(remote.name, 0))
            selected = self._remote_change_poll_batch(paths, path_offset)
            if not selected:
                continue
            poll_offsets[remote.name] = (path_offset + len(selected)) % len(paths)
            self._remote_change_poll_offsets = poll_offsets
            self._remote_change_poll_index = (start + offset + 1) % len(remotes)
            self._start_remote_cache_check(remote, selected)
            return

    def _remote_change_poll_batch(self, paths: list[str], offset: int) -> list[str]:
        if not paths:
            return []
        limit = min(REMOTE_CACHE_CHECK_BATCH_SIZE, len(paths))
        start = offset % len(paths)
        return [paths[(start + index) % len(paths)] for index in range(limit)]

    def _sync_all_cached_files(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        remotes = _load_visible_remotes()
        started = 0
        for remote in remotes:
            try:
                paths = self.file_browser.backend.managed_record_paths(remote.name)
            except Exception:
                continue
            if not paths:
                continue
            if remote.name in getattr(self, "_offline_reconcile_running", set()):
                continue
            visible_remote = getattr(getattr(self, "file_browser", None), "remote", None)
            if visible_remote is not None and visible_remote.name == remote.name:
                self.file_browser.start_sync(remote.name, paths)
            if self._start_remote_cache_check(remote, paths):
                started += 1
        if not started:
            self.tray_app._notify("Cache sync", "No cached or offline files need checking.", success=True)

    def _remove_all_offline_files(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self.file_browser.remove_all_offline()
        self._update_app_cache_buttons()

    def _clear_all_cache_files(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self.file_browser.clear_all_cache()
        self._update_app_cache_buttons()

    def _sync_cached_paths(self, remote: core.RemoteInfo, items: list[tuple[str, bool]]) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        if not items:
            return
        record_paths: list[str] = []
        seen: set[str] = set()
        for path, is_dir in items:
            normalized = normalize_browser_path(path)
            try:
                paths = self.file_browser.backend.managed_record_paths_under(remote.name, normalized if is_dir else normalized)
            except Exception as exc:
                self.tray_app._notify("Cache sync", f"Could not prepare sync: {exc}", success=False)
                return
            for record_path in paths:
                if record_path in seen:
                    continue
                seen.add(record_path)
                record_paths.append(record_path)
        if not record_paths:
            self.tray_app._notify("Cache sync", "No cached or offline files found there.", success=True)
            self.file_browser.finish_sync(remote.name)
            return
        if not self._start_remote_cache_check(remote, record_paths):
            self.file_browser.finish_sync(remote.name)

    def _build_app_menu(self) -> None:
        menu_bar = self.window.menuBar()
        try:
            menu_bar.setNativeMenuBar(False)
        except Exception:
            pass
        app_menu = menu_bar.addMenu("App")
        self._app_menu = app_menu
        self.tray_app._add_action(app_menu, "Update status", self.refresh)
        app_menu.addSeparator()
        self.tray_app._add_action(app_menu, "Sync cached files now", self._sync_all_cached_files)
        self.tray_app._add_action(app_menu, "Remove all offline files", self._remove_all_offline_files)
        self.tray_app._add_action(app_menu, "Clear all resolved cache", self._clear_all_cache_files)
        self.tray_app._add_action(app_menu, "Debug cache sync", self._show_cache_sync_debug_report)
        app_menu.addSeparator()
        self.tray_app._add_action(app_menu, "License", self._show_license_dialog)
        self.tray_app._add_action(app_menu, "About Mountlet", self._show_about)
        app_menu.addSeparator()
        self.tray_app._add_action(app_menu, "Quit", self.tray_app.request_quit)

        mount_menu = menu_bar.addMenu("Mount")
        self._mount_menu = mount_menu
        self.tray_app._add_action(mount_menu, "Mount all", lambda: self._mount_all())
        self.tray_app._add_action(mount_menu, "Unmount all", lambda: self._unmount_all())
        mount_menu.addSeparator()
        self.tray_app._add_action(mount_menu, "Add remote", self._show_new_remote_wizard)

        config_menu = menu_bar.addMenu("Config")
        self._config_menu = config_menu
        self.tray_app._add_action(config_menu, "Keyboard shortcuts", self._show_shortcut_config_editor)
        config_menu.addSeparator()
        self.tray_app._add_action(config_menu, "Export config bundle", self._export_config_bundle)
        self.tray_app._add_action(config_menu, "Import config bundle", self._import_config_bundle)
        self.tray_app._add_action(config_menu, "Open config backup folder", self._open_config_backup_folder)
        config_menu.addSeparator()
        self.tray_app._add_action(config_menu, "Set config sync location", self._show_config_sync_editor)
        self.tray_app._add_action(config_menu, "Push config to sync location", self._push_config_sync_bundle)
        self.tray_app._add_action(config_menu, "Pull config from sync location", self._pull_config_sync_bundle)
        config_menu.addSeparator()
        self._add_open_config_files_menu(config_menu)
        self._update_license_lock_state()

    def _add_open_config_files_menu(self, parent_menu: Any) -> Any:
        files_menu = parent_menu.addMenu("Open config file")
        self.tray_app._add_action(files_menu, "rclone", self._open_rclone_config_file)
        self.tray_app._add_action(files_menu, "App", self._open_app_config_file)
        self.tray_app._add_action(files_menu, "Mounts", self._open_mount_config_file)
        if _has_mount_driver_config():
            self.tray_app._add_action(files_menu, "Filesystem driver", self._open_fuse_config_file)
        return files_menu

    def _show_about(self) -> None:
        self.qt.QMessageBox.information(self.window, "About Mountlet", _about_text(self.qt))

    def _show_license_dialog(self) -> None:
        dialog = LicenseDialog(self.qt, self.window)
        dialog.dialog.finished.connect(lambda _result=0: self._license_dialog_closed())
        self._open_child_dialog(dialog)

    def _license_dialog_closed(self) -> None:
        self.tray_app.rebuild_menus()
        self.refresh()
        self._update_purchase_license_button()

    def is_visible(self) -> bool:
        return bool(self.window.isVisible())

    def toggle_from_tray(self) -> None:
        if self._tray_is_quitting():
            return
        visible_on_current_desktop = self._desktop_api().window_is_on_current_workspace(self.window)
        if self.is_visible() and visible_on_current_desktop is not False:
            if (
                self._app_window_is_active()
                or getattr(self, "_deactivated_for_tray", False)
                or self._has_visible_child_dialog()
            ):
                self._deactivated_for_tray = False
                self._hide_window_stack()
            else:
                self.show()
            return
        self.show()

    def show(self) -> None:
        if self._tray_is_quitting():
            return
        was_visible = self.is_visible()
        visible_on_current_desktop = self._desktop_api().window_is_on_current_workspace(self.window)
        reopened_from_other_desktop = was_visible and visible_on_current_desktop is False
        if reopened_from_other_desktop:
            self._close_child_dialogs()
            self.window.hide()
            was_visible = False
        self.refresh()
        if not was_visible:
            self._position_after_fit = True
            self._position_near_tray()
        self._window_stack_hidden = False
        self._focus_window(defer_activation=reopened_from_other_desktop)
        if not was_visible:
            self._schedule_position_near_tray()

    def _window_is_active(self) -> bool:
        try:
            return bool(self.window.isActiveWindow())
        except Exception:
            return False

    def _focus_window(self, *, defer_activation: bool = False) -> None:
        self._desktop_api().move_window_to_current_workspace(self.window)
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self._desktop_api().move_window_to_current_workspace(self.window)
        self._restore_x11_keep_above()
        if self._has_visible_child_dialog():
            self._raise_child_windows()
            self._schedule_child_window_raises()
            return
        if defer_activation:
            self._schedule_main_window_activation()
            self._raise_child_windows()
            self._schedule_child_window_raises()
            return
        self._activate_main_window()
        self._schedule_main_window_activation()
        self._raise_child_windows()
        self._schedule_child_window_raises()

    def _schedule_main_window_activation(self) -> None:
        timer = getattr(getattr(self, "qt", None), "QTimer", None)
        if timer is None:
            return
        timer.singleShot(50, self._activate_main_window_if_current_desktop)
        timer.singleShot(150, self._activate_main_window_if_current_desktop)
        timer.singleShot(300, self._activate_main_window_if_current_desktop)

    def _schedule_position_near_tray(self) -> None:
        timer = getattr(getattr(self, "qt", None), "QTimer", None)
        if timer is None:
            return
        timer.singleShot(0, self._position_near_tray)
        timer.singleShot(50, self._position_near_tray)
        timer.singleShot(150, self._position_near_tray)

    def _activate_main_window_if_current_desktop(self) -> None:
        if not self.is_visible():
            return
        desktop = self._desktop_api()
        desktop.move_window_to_current_workspace(self.window)
        if desktop.window_is_on_current_workspace(self.window) is False:
            return
        self._restore_x11_keep_above()
        self._activate_main_window()

    def _restore_x11_keep_above(self) -> None:
        if getattr(self, "_keep_above", False):
            self._desktop_api().set_keep_above(self.window, True)

    def _activate_main_window(self) -> None:
        self._set_focus_owner("main")
        self.window.raise_()
        self.window.activateWindow()
        qt = getattr(self, "qt", None)
        if qt is not None:
            qt.QTimer.singleShot(0, self._focus_current_remote_row)

    def _schedule_child_window_raises(self) -> None:
        timer = getattr(getattr(self, "qt", None), "QTimer", None)
        if timer is not None:
            timer.singleShot(0, self._raise_child_windows)
            timer.singleShot(100, self._raise_child_windows)
            timer.singleShot(300, self._raise_child_windows)

    def _has_visible_child_dialog(self) -> bool:
        for child in (self._active_child_window(), *getattr(self, "_child_dialogs", [])):
            if child is None:
                continue
            try:
                if child.isVisible():
                    return True
            except Exception:
                return True
        return False

    def _raise_active_child_window(self) -> None:
        self._raise_child_window(self._active_child_window())

    def _raise_child_windows(self) -> None:
        seen: set[int] = set()
        for child in (self._active_child_window(), *getattr(self, "_child_dialogs", [])):
            if child is None or id(child) in seen:
                continue
            seen.add(id(child))
            self._raise_child_window(child)

    def _raise_child_window(self, child: Any | None) -> None:
        if child is None:
            return
        try:
            if child.isMinimized():
                child.showNormal()
            else:
                child.show()
            child.raise_()
            child.activateWindow()
        except Exception:
            return

    def _track_child_dialog(self, dialog: Any, owner: Any | None = None) -> None:
        self._child_dialogs = [
            child for child in getattr(self, "_child_dialogs", []) if child is not dialog
        ]
        self._child_dialogs.append(dialog)
        if owner is not None:
            if not hasattr(self, "_child_dialog_owners"):
                self._child_dialog_owners = {}
            self._child_dialog_owners[dialog] = owner

    def _untrack_child_dialog(self, dialog: Any) -> None:
        self._child_dialogs = [
            child for child in getattr(self, "_child_dialogs", []) if child is not dialog
        ]
        getattr(self, "_child_dialog_owners", {}).pop(dialog, None)

    def _open_child_dialog(self, owner: Any, on_accepted: Any | None = None) -> None:
        dialog = owner.dialog
        self._track_child_dialog(dialog, owner)
        try:
            dialog.setModal(True)
            dialog.setWindowModality(self.qt.Qt.WindowModality.WindowModal)
        except Exception:
            pass
        if on_accepted is not None:
            dialog.accepted.connect(on_accepted)
        dialog.finished.connect(lambda _result=0, child=dialog: self._untrack_child_dialog(child))
        if not self.is_visible():
            self.show()
            self.qt.QTimer.singleShot(0, lambda child=dialog: self._show_tracked_child_dialog(child))
            return
        self._show_tracked_child_dialog(dialog)

    def _show_tracked_child_dialog(self, dialog: Any) -> None:
        if dialog not in getattr(self, "_child_dialogs", []) or self._tray_is_quitting():
            return
        dialog.show()
        self._fit_child_dialog_to_screen(dialog)
        self._raise_child_windows()

    def _fit_child_dialog_to_screen(self, dialog: Any) -> None:
        try:
            screen = dialog.screen() or self.window.screen() or self.qt.QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            size = dialog.size()
            width = min(size.width(), max(320, available.width()))
            height = min(size.height(), max(240, available.height()))
            if width != size.width() or height != size.height():
                dialog.resize(width, height)
            position = dialog.frameGeometry().topLeft()
            max_x = max(available.left(), available.left() + available.width() - width)
            max_y = max(available.top(), available.top() + available.height() - height)
            x = min(max(position.x(), available.left()), max_x)
            y = min(max(position.y(), available.top()), max_y)
            if x != position.x() or y != position.y():
                dialog.move(x, y)
        except Exception:
            return

    def _hide_window_stack(self) -> None:
        self._close_child_dialogs()
        file_browser = getattr(self, "file_browser", None)
        if file_browser is not None:
            file_browser.hide()
        self._window_stack_hidden = True
        try:
            self.window.hide()
        except Exception:
            pass

    def _close_child_dialogs(self) -> None:
        dialogs = []
        active_child = self._active_child_window()
        if active_child is not None:
            dialogs.append(active_child)
        dialogs.extend(reversed(getattr(self, "_child_dialogs", [])))
        owners = dict(getattr(self, "_child_dialog_owners", {}))
        seen: set[int] = set()
        for child in dialogs:
            if id(child) in seen:
                continue
            seen.add(id(child))
            owner = owners.get(child)
            try:
                if owner is not None and hasattr(owner, "_reject"):
                    owner._reject()
                elif hasattr(child, "reject"):
                    child.reject()
                else:
                    child.close()
            except Exception:
                try:
                    child.close()
                except Exception:
                    pass
        self._child_dialogs = []
        self._child_dialog_owners = {}

    def _child_offsets(self) -> dict[Any, tuple[int, int]]:
        main_position = self._window_position(self.window)
        if main_position is None:
            return {}
        offsets: dict[Any, tuple[int, int]] = {}
        for child in getattr(self, "_child_dialogs", []):
            child_position = self._window_position(child)
            if child_position is None:
                continue
            offsets[child] = (
                child_position[0] - main_position[0],
                child_position[1] - main_position[1],
            )
        return offsets

    def _restore_child_offsets(self, offsets: dict[Any, tuple[int, int]]) -> None:
        main_position = self._window_position(self.window)
        if main_position is None:
            return
        for child, (x_offset, y_offset) in offsets.items():
            if child not in getattr(self, "_child_dialogs", []):
                continue
            try:
                child.move(main_position[0] + x_offset, main_position[1] + y_offset)
            except Exception:
                pass

    def _window_position(self, window: Any) -> tuple[int, int] | None:
        try:
            point = window.frameGeometry().topLeft()
            return int(point.x()), int(point.y())
        except Exception:
            return None

    def _active_child_window(self) -> Any | None:
        qt = getattr(self, "qt", None)
        if qt is None:
            return None
        for candidate in (
            qt.QApplication.activeModalWidget(),
            qt.QApplication.activeWindow(),
            *reversed(getattr(self, "_child_dialogs", [])),
        ):
            if candidate is not None and self._is_child_window(candidate):
                return candidate
        return None

    def _is_child_window(self, candidate: Any) -> bool:
        current = candidate
        while current is not None:
            if current is self.window:
                return candidate is not self.window
            try:
                current = current.parentWidget()
            except Exception:
                return False
        return False

    def _position_near_tray(self) -> None:
        try:
            anchor, geometry_valid = self._tray_anchor()
            screen = self.qt.QApplication.screenAt(anchor) or self.qt.QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            size = self.window.size()
            if not size.isValid():
                size = self.window.sizeHint()
            if getattr(self.tray_app, "_is_gnome_wayland", False) and not geometry_valid:
                x = available.left() + available.width() - size.width() - 8
                y = available.top() + 8
            else:
                x, y = _popup_position(
                    anchor.x(),
                    anchor.y(),
                    (available.left(), available.top(), available.width(), available.height()),
                    (size.width(), size.height()),
                )
            x, y = self._safe_popup_position(x, y, available, size)
            self.window.move(x, y)
            self._last_popup_position = (x, y)
        except Exception:
            return

    def _tray_anchor(self) -> tuple[Any, bool]:
        tray_geometry = self.tray_app.tray.geometry()
        primary_screen = self.qt.QApplication.primaryScreen()
        available = primary_screen.availableGeometry() if primary_screen is not None else None
        if tray_geometry.isValid() and available is not None and not self._tray_geometry_is_suspicious(tray_geometry, available):
            anchor = tray_geometry.center()
            self._last_tray_anchor = anchor
            return anchor, True
        cursor = self.qt.QCursor.pos()
        screen = self.qt.QApplication.screenAt(cursor) or primary_screen
        available = screen.availableGeometry() if screen is not None else available
        if available is not None and not self._point_is_suspicious_anchor(cursor, available):
            self._last_tray_anchor = cursor
            return cursor, False
        remembered = getattr(self, "_last_tray_anchor", None)
        if remembered is not None:
            return remembered, False
        if available is not None:
            point = self.qt.QPoint(available.right() - 8, available.top() + 8)
            return point, False
        return cursor, False

    def _tray_geometry_is_suspicious(self, tray_geometry: Any, available: Any) -> bool:
        try:
            center = tray_geometry.center()
            return (
                tray_geometry.width() <= 1
                or tray_geometry.height() <= 1
                or self._point_is_suspicious_anchor(center, available)
            )
        except Exception:
            return True

    def _point_is_suspicious_anchor(self, point: Any, available: Any) -> bool:
        try:
            return (
                abs(point.x() - available.left()) <= 1
                and abs(point.y() - available.top()) <= 1
            )
        except Exception:
            return True

    def _safe_popup_position(self, x: int, y: int, available: Any, size: Any) -> tuple[int, int]:
        if not self._position_is_suspicious(x, y, available):
            return x, y
        remembered = getattr(self, "_last_popup_position", None)
        if remembered is not None:
            remembered_x, remembered_y = remembered
            if not self._position_is_suspicious(remembered_x, remembered_y, available):
                return self._clamped_popup_position(remembered_x, remembered_y, available, size)
        fallback_x = available.left() + available.width() - size.width() - 8
        fallback_y = available.top() + 8
        return self._clamped_popup_position(fallback_x, fallback_y, available, size)

    def _position_is_suspicious(self, x: int, y: int, available: Any) -> bool:
        try:
            return (
                abs(x) <= 1
                and abs(y) <= 1
            ) or (
                abs(x - available.left()) <= 1
                and abs(y - available.top()) <= 1
            )
        except Exception:
            return True

    def _clamped_popup_position(self, x: int, y: int, available: Any, size: Any) -> tuple[int, int]:
        max_x = max(available.left(), available.left() + available.width() - size.width())
        max_y = max(available.top(), available.top() + available.height() - size.height())
        return min(max(x, available.left()), max_x), min(max(y, available.top()), max_y)

    def _clamp_to_screen(self, screen: Any | None = None) -> None:
        try:
            target_screen = screen or self.window.screen() or self.qt.QApplication.primaryScreen()
            if target_screen is None:
                return
            available = target_screen.availableGeometry()
            position = self.window.frameGeometry().topLeft()
            size = self.window.size()
            max_x = max(available.left(), available.left() + available.width() - size.width())
            max_y = max(available.top(), available.top() + available.height() - size.height())
            x = min(max(position.x(), available.left()), max_x)
            y = min(max(position.y(), available.top()), max_y)
            if x != position.x() or y != position.y():
                self.window.move(x, y)
        except Exception:
            return

    def refresh(self) -> None:
        if self._tray_is_quitting():
            return
        self._refresh_pending = False
        locked = self._license_locked()
        if locked:
            self._hide_locked_file_browser()
        remotes = _load_visible_remotes()
        if not locked:
            self._request_config_sync_metadata_check(remotes)
        mounted_by_name = {remote.name: core.is_mounted(remote) for remote in remotes}
        remote_names = [remote.name for remote in remotes]
        name_width = self._remote_name_width(remotes)
        if self._current_remote_names == remote_names and self._row_widgets:
            self._name_column_width = name_width
            self._update_purchase_license_button()
            for remote in remotes:
                self._update_remote_row(remote, mounted_by_name[remote.name])
                if not locked:
                    self._schedule_storage_load(remote)
            self._update_license_lock_state()
            self._browser_layout_changed()
            return

        root = self.qt.QWidget()
        outer = self.qt.QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addWidget(self._sort_toolbar())
        if locked:
            outer.addWidget(self._license_locked_banner())

        scroll = self.qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(self.qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(REMOTE_LIST_MIN_HEIGHT)
        container = self.qt.QWidget()
        rows = self.qt.QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        self._row_widgets = {}
        self._current_remote_names = remote_names
        self._remote_scroll = scroll
        if self._selected_remote_name not in remote_names:
            self._selected_remote_name = remote_names[0] if remote_names else ""
        self._name_column_width = name_width
        if remotes:
            for remote in remotes:
                rows.addWidget(self._remote_row(remote, mounted_by_name[remote.name]))
                if not locked:
                    self._schedule_storage_load(remote)
        else:
            rows.addWidget(self.qt.QLabel("No rclone remotes found"))
        scroll.setWidget(container)
        outer.addWidget(scroll)
        outer.addWidget(self._add_remote_row())

        central = root
        if getattr(self.tray_app, "_is_wayland", False):
            central = self.qt.QWidget()
            shell = self.qt.QHBoxLayout(central)
            shell.setContentsMargins(0, 0, 0, 0)
            shell.setSpacing(6)
            try:
                root.setSizePolicy(
                    self.qt.QSizePolicy.Policy.Fixed,
                    self.qt.QSizePolicy.Policy.Preferred,
                )
            except Exception:
                pass
            shell.addWidget(root, 0)
            self.file_browser.embed_into(shell)
        central.setObjectName("mountletMainSurface")
        self._main_surface = central
        self.window.setCentralWidget(central)
        self._update_main_focus_style()
        self._content_fit_widgets = (root, scroll, container)
        self.file_browser.preload(remotes)
        self._update_license_lock_state()
        self._fit_to_content(root, scroll, container)
        self.qt.QTimer.singleShot(0, lambda: self._finish_content_fit(root, scroll, container, central))
        self._update_app_cache_buttons(remotes)
        self._update_license_lock_state()

    def _finish_content_fit(self, root: Any, scroll: Any, container: Any, central: Any | None = None) -> None:
        expected = central or root
        if self.window.centralWidget() is not expected or self._tray_is_quitting():
            return
        self._fit_to_content(root, scroll, container)
        if getattr(self, "_position_after_fit", False):
            self._position_after_fit = False
            self._position_near_tray()
        self._reposition_file_browser()

    def _browser_layout_changed(self) -> None:
        widgets = getattr(self, "_content_fit_widgets", None)
        if widgets is None or self._tray_is_quitting():
            return
        self.qt.QTimer.singleShot(0, lambda: self._fit_to_content(*widgets))

    def _update_main_focus_style(self) -> None:
        root = getattr(self, "_main_surface", None)
        if root is None:
            return
        active = getattr(self, "_focus_owner", "main") == "main"
        color = "#2563eb" if active else "rgba(107, 114, 128, 110)"
        root.setStyleSheet(f"QWidget#mountletMainSurface {{ border: 2px solid {color}; border-radius: 4px; }}")

    def _set_focus_owner(self, owner: str) -> None:
        owner = "browser" if owner == "browser" else "main"
        self._focus_owner = owner
        self._update_main_focus_style()
        file_browser = getattr(self, "file_browser", None)
        if file_browser is not None:
            file_browser._update_focus_style(owner == "browser")

    def _toolbar_button(self, icon_name: str, fallback_text: str, tooltip: str, callback: Any) -> Any:
        button = create_badged_button(self.qt, fallback_text)
        button.setFixedSize(34, 30)
        button.setToolTip(tooltip)
        apply_button_icon(self.qt, button, icon_name, fallback_text=fallback_text, size=22)
        try:
            font = button.font()
            font.setPointSize(max(font.pointSize() + 5, 16))
            button.setFont(font)
        except Exception:
            pass
        button.clicked.connect(lambda checked=False: callback())
        return button

    def _settings_toolbar_button(self) -> Any:
        button = self._toolbar_button("ui-config", "⚙", "App settings", self._show_app_config_editor)
        self._settings_button = button
        return button

    def _push_sync_toolbar_button(self) -> Any:
        button = self._toolbar_button(
            "ui-config-push",
            "↑",
            "Push config to sync location",
            self._push_config_sync_bundle,
        )
        self._push_sync_button = button
        return button

    def _pull_sync_toolbar_button(self) -> Any:
        button = self._toolbar_button(
            "ui-config-pull",
            "↓",
            "Pull config from sync location",
            self._pull_config_sync_bundle,
        )
        self._pull_sync_button = button
        return button

    def _purchase_license_toolbar_button(self) -> Any:
        button = self.qt.QPushButton()
        _apply_external_link_button(self.qt, button, text="Buy license", prominent=True)
        with suppress(Exception):
            button.setMinimumHeight(32)
        button.setToolTip("Open the Mountlet license purchase page.")
        button.clicked.connect(lambda checked=False: self._open_purchase_page())
        self._purchase_license_button = button
        self._update_purchase_license_button()
        return button

    def _all_cache_sync_toolbar_button(self) -> Any:
        button = self._toolbar_button("ui-sync", "⇄", "Sync cached files for all remotes", self._sync_all_cached_files)
        self._all_cache_sync_button = button
        return button

    def _remove_all_offline_toolbar_button(self) -> Any:
        button = self._toolbar_button(
            "ui-remove-offline",
            "",
            "Remove offline files for all remotes",
            self._remove_all_offline_files,
        )
        self._remove_all_offline_button = button
        return button

    def _clear_all_cache_toolbar_button(self) -> Any:
        button = self._toolbar_button(
            "ui-clear-cache",
            "",
            "Clear resolved cache for all remotes",
            self._clear_all_cache_files,
        )
        self._clear_all_cache_button = button
        return button

    def _update_app_cache_buttons(self, remotes: list[core.RemoteInfo] | None = None) -> None:
        remotes = remotes if remotes is not None else _load_visible_remotes()
        remove_button = getattr(self, "_remove_all_offline_button", None)
        clear_button = getattr(self, "_clear_all_cache_button", None)
        if self._license_locked():
            for button in (remove_button, clear_button):
                if button is not None:
                    button.setEnabled(False)
                    button.setToolTip(_license_lock_message())
            return
        if remove_button is not None:
            enabled = any(self.file_browser.backend.has_offline_content(remote.name, "", is_dir=True) for remote in remotes)
            remove_button.setEnabled(enabled)
            remove_button.setToolTip(
                "Remove offline files for all remotes" if enabled else "No offline files are saved"
            )
        if clear_button is not None:
            enabled = any(
                self.file_browser.backend.has_temporary_cache_content(remote.name, "", is_dir=True)
                for remote in remotes
            )
            clear_button.setEnabled(enabled)
            clear_button.setToolTip(
                "Clear resolved cache for all remotes" if enabled else "No resolved temporary cache to clear"
            )

    def _update_config_sync_buttons(self) -> None:
        push_button = getattr(self, "_push_sync_button", None)
        pull_button = getattr(self, "_pull_sync_button", None)
        if push_button is None and pull_button is None:
            return
        if self._license_locked():
            for button in (push_button, pull_button):
                if button is not None:
                    button.setEnabled(False)
                    button.setToolTip(_license_lock_message())
            return
        settings = load_app_settings()
        sync_configured = bool(settings.config_sync_remote)
        state = _load_config_sync_state()
        try:
            local_hash = bundle_file.current_config_fingerprint()
        except Exception:
            local_hash = ""
        last_synced_hash = str(state.get("last_synced_hash") or "")
        original_last_synced_hash = last_synced_hash
        hash_kind = str(state.get("last_synced_hash_kind") or "")
        remote_metadata = getattr(self, "_remote_sync_metadata", None) or {}
        remote_hash = str(remote_metadata.get("config_hash", ""))
        last_local_hash = str(
            state.get("last_local_config_hash")
            or state.get("last_pushed_hash")
            or state.get("last_pulled_hash")
            or last_synced_hash
            or ""
        )
        last_remote_hash = str(
            state.get("remote_config_hash")
            or state.get("last_remote_config_hash")
            or state.get("last_pushed_remote_hash")
            or state.get("last_pulled_remote_hash")
            or last_synced_hash
            or ""
        )
        known_remote_hashes = {
            value
            for value in (
                original_last_synced_hash,
                last_remote_hash,
                str(state.get("last_pushed_hash") or ""),
                str(state.get("last_pulled_hash") or ""),
            )
            if value
        }
        if (
            sync_configured
            and hash_kind != "operation"
            and local_hash
            and remote_hash
            and remote_hash == original_last_synced_hash
            and local_hash != original_last_synced_hash
        ):
            last_synced_hash = local_hash
            last_local_hash = local_hash
            state["last_synced_hash"] = local_hash
            state["last_synced_hash_kind"] = "operation"
            state["last_local_config_hash"] = local_hash
            _save_config_sync_state(state)
        local_changed = bool(sync_configured and local_hash and local_hash != last_local_hash)
        remote_changed = bool(sync_configured and remote_hash and remote_hash not in known_remote_hashes | {last_synced_hash})
        if push_button is not None:
            self._apply_button_icon_if_available(push_button, "ui-config-push", "↑", size=22)
            set_badge(push_button, local_changed, "#ef4444")
            push_button.setEnabled(sync_configured)
            push_button.setToolTip(
                "Local config changed since the last push." if local_changed else "Push config to sync location"
            )
        if pull_button is not None:
            self._apply_button_icon_if_available(pull_button, "ui-config-pull", "↓", size=22)
            set_badge(pull_button, remote_changed, "#ef4444")
            pull_button.setEnabled(sync_configured)
            if remote_changed:
                detail = _sync_metadata_summary(remote_metadata)
                pull_button.setToolTip(f"Synced config differs from this device.\n{detail}".strip())
            else:
                pull_button.setToolTip("Pull config from sync location")

    def _configuration_changed(self) -> None:
        self._update_config_sync_buttons()

    def _update_purchase_license_button(self) -> None:
        button = getattr(self, "_purchase_license_button", None)
        if button is None:
            return
        button.setVisible(license_control.current_status().state != "licensed")

    def _open_purchase_page(self) -> None:
        _open_external_url(
            self.qt,
            self.window,
            license_control.license_purchase_url(),
            title="Buy license",
        )

    def _apply_button_icon_if_available(
        self,
        button: Any,
        icon_name: str,
        fallback_text: str,
        *,
        size: int = 22,
        color: str | None = None,
    ) -> None:
        qt = getattr(self, "qt", None)
        if qt is None:
            with suppress(Exception):
                button.setText(fallback_text)
            return
        apply_button_icon(qt, button, icon_name, fallback_text=fallback_text, size=size, color=color)

    def _request_config_sync_metadata_check(self, remotes: list[core.RemoteInfo]) -> None:
        if self._remote_sync_check_pending or self._tray_is_quitting():
            return
        settings = load_app_settings()
        if not settings.config_sync_remote:
            self._remote_sync_metadata = None
            self._update_config_sync_buttons()
            return
        remote = next((item for item in remotes if item.name == settings.config_sync_remote), None)
        if remote is None:
            self._remote_sync_metadata = None
            self._update_config_sync_buttons()
            return
        relative_path = normalize_browser_path(settings.config_sync_path) or "Mountlet/config.mountlet"
        if Path(relative_path).suffix.casefold() != bundle_file.BUNDLE_EXTENSION:
            relative_path = f"{relative_path}{bundle_file.BUNDLE_EXTENSION}"
        self._remote_sync_check_pending = True

        def worker() -> None:
            try:
                with tempfile.TemporaryDirectory(prefix="mountlet-sync-status-") as tempdir:
                    temporary = Path(tempdir) / Path(relative_path).name
                    self._copy_remote_file_to_local(remote, relative_path, temporary)
                    metadata = bundle_file.bundle_metadata(temporary)
                self._bridge.sync_metadata_ready.emit(metadata, None)
            except Exception as exc:
                self._bridge.sync_metadata_ready.emit(None, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_sync_metadata_ready(self, metadata: object, error: object) -> None:
        self._remote_sync_check_pending = False
        if self._tray_is_quitting():
            return
        self._remote_sync_metadata = metadata if isinstance(metadata, dict) else None
        self._update_config_sync_buttons()

    def _pin_button(self) -> Any:
        button = self.qt.QPushButton("📌")
        button.setFixedSize(34, 30)
        button.setToolTip("Keep Mountlet above other windows")
        apply_button_icon(self.qt, button, "ui-pin", fallback_text="📌", size=22)
        try:
            font = button.font()
            font.setPointSize(max(font.pointSize() + 5, 16))
            button.setFont(font)
        except Exception:
            pass
        try:
            button.setCheckable(True)
            button.setChecked(self._keep_above)
        except Exception:
            pass
        button.clicked.connect(lambda checked=False: self._toggle_keep_above(bool(checked)))
        pin_supported = not getattr(self.tray_app, "_is_gnome_wayland", False)
        button.setEnabled(pin_supported)
        if not pin_supported:
            button.setToolTip("GNOME on Wayland does not allow apps to pin their own windows.")
        self._keep_above_button = button
        self._update_keep_above_button()
        return button

    def _toggle_keep_above(self, checked: bool | None = None) -> None:
        self._keep_above = not self._keep_above if checked is None else checked
        self._apply_keep_above()
        self._update_keep_above_button()

    def _apply_keep_above(self) -> None:
        if self._desktop_api().set_keep_above(self.window, self._keep_above):
            return
        try:
            flag = self.qt.Qt.WindowType.WindowStaysOnTopHint
        except Exception:
            return
        try:
            was_visible = self.window.isVisible()
        except Exception:
            was_visible = False
        position = self._window_position(self.window)
        try:
            self.window.setWindowFlag(flag, self._keep_above)
        except Exception:
            return
        if position is not None:
            try:
                self.window.move(*position)
            except Exception:
                pass
        if was_visible:
            try:
                self.window.show()
            except Exception:
                pass

    def _update_keep_above_button(self) -> None:
        button = getattr(self, "_keep_above_button", None)
        if button is None:
            return
        try:
            if not button.isEnabled():
                button.setToolTip("GNOME on Wayland does not allow apps to pin their own windows.")
                button.setStyleSheet("")
                return
        except Exception:
            pass
        try:
            button.setChecked(self._keep_above)
        except Exception:
            pass
        try:
            if self._keep_above:
                button.setToolTip("Stop keeping Mountlet above other windows")
                button.setStyleSheet(
                    "QPushButton { background: #2563eb; color: #ffffff; "
                    "border: 1px solid #93c5fd; border-radius: 4px; }"
                )
            else:
                button.setToolTip("Keep Mountlet above other windows")
                button.setStyleSheet(
                    "QPushButton, QPushButton:checked { background: transparent; "
                    "border: 1px solid transparent; border-radius: 4px; }"
                )
                button.setDown(False)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        except Exception:
            pass

    def _request_refresh(self) -> None:
        if self._tray_is_quitting():
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.qt.QTimer.singleShot(25, self.refresh)

    def _license_locked(self) -> bool:
        return _license_locked()

    def _license_locked_banner(self) -> Any:
        label = self.qt.QLabel(_license_lock_message())
        label.setObjectName("licenseLockBanner")
        label.setWordWrap(True)
        label.setStyleSheet(
            "QLabel#licenseLockBanner {"
            "color: #ef4444;"
            "font-weight: 700;"
            "padding: 4px 2px;"
            "}"
        )
        self._license_lock_banner = label
        return label

    def _hide_locked_file_browser(self) -> None:
        file_browser = getattr(self, "file_browser", None)
        if file_browser is None:
            return
        try:
            file_browser.close()
        except Exception:
            pass

    def _update_license_lock_state(self) -> None:
        locked = self._license_locked()
        banner = getattr(self, "_license_lock_banner", None)
        if banner is not None:
            banner.setVisible(locked)
        if locked:
            self._hide_locked_file_browser()
            if self.is_visible() and not getattr(self, "_license_prompted_for_lock", False):
                self._license_prompted_for_lock = True
                self.qt.QTimer.singleShot(0, self._show_license_dialog)
        else:
            self._license_prompted_for_lock = False
        self._update_locked_menus(locked)
        self._update_locked_toolbar(locked)
        self._update_locked_remote_rows(locked)
        self._update_config_sync_buttons()
        self._update_app_cache_buttons()

    def _update_locked_menus(self, locked: bool) -> None:
        mount_menu = getattr(self, "_mount_menu", None)
        config_menu = getattr(self, "_config_menu", None)
        if mount_menu is not None:
            mount_menu.setEnabled(not locked)
            with suppress(Exception):
                mount_menu.menuAction().setEnabled(not locked)
        if config_menu is not None:
            config_menu.setEnabled(not locked)
            with suppress(Exception):
                config_menu.menuAction().setEnabled(not locked)
        app_menu = getattr(self, "_app_menu", None)
        if app_menu is None:
            return
        allowed = {"License", "About Mountlet", "Quit"}
        for action in app_menu.actions():
            text = action.text().replace("&", "")
            if action.isSeparator():
                continue
            action.setEnabled(not locked or text in allowed)

    def _update_locked_toolbar(self, locked: bool) -> None:
        for button in (
            getattr(self, "_settings_button", None),
            getattr(self, "_push_sync_button", None),
            getattr(self, "_pull_sync_button", None),
            getattr(self, "_sort_button", None),
            getattr(self, "_reverse_button", None),
            getattr(self, "_all_cache_sync_button", None),
            getattr(self, "_remove_all_offline_button", None),
            getattr(self, "_clear_all_cache_button", None),
            getattr(self, "_keep_above_button", None),
        ):
            if button is not None:
                button.setEnabled(not locked)

    def _update_locked_remote_rows(self, locked: bool) -> None:
        for widgets in getattr(self, "_row_widgets", {}).values():
            widgets.frame.setEnabled(not locked)
            widgets.frame.setCursor(
                self.qt.QCursor(
                    self.qt.Qt.CursorShape.ArrowCursor
                    if locked
                    else self.qt.Qt.CursorShape.PointingHandCursor
                )
            )
            widgets.frame.setToolTip(_license_lock_message() if locked else widgets.frame.toolTip())
            if locked:
                for name in ("config_button", "browser_button", "up_button", "down_button"):
                    button = getattr(widgets, name, None)
                    if button is not None:
                        button.setEnabled(False)

    def _sort_toolbar(self) -> Any:
        widget = self.qt.QWidget()
        layout = self.qt.QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        drag_handle = self.qt.QLabel("✥")
        drag_handle.setFixedSize(22, 26)
        drag_handle.setAlignment(self.qt.Qt.AlignmentFlag.AlignCenter)
        drag_handle.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.SizeAllCursor))
        drag_handle.setToolTip("Drag to move Mountlet.")
        drag_handle.mousePressEvent = self._begin_window_drag
        drag_handle.mouseMoveEvent = self._continue_window_drag
        drag_handle.mouseReleaseEvent = self._end_window_drag

        sort_button = self.qt.QPushButton("Sort by")
        sort_menu = self.qt.QMenu(sort_button)
        for mode, label in REMOTE_SORT_OPTIONS:
            self.tray_app._add_action(sort_menu, label, lambda selected=mode: self._sort_remote_order(selected))
        sort_button.setMenu(sort_menu)
        sort_button.setToolTip("Sort remotes and save the new order.")
        self._sort_button = sort_button

        reverse_button = self.qt.QPushButton("↕")
        reverse_button.setFixedSize(34, 30)
        reverse_button.setToolTip("Reverse the current remote order.")
        apply_button_icon(self.qt, reverse_button, "ui-reorder", fallback_text="↕", size=22)
        try:
            font = reverse_button.font()
            font.setPointSize(max(font.pointSize() + 5, 16))
            reverse_button.setFont(font)
        except Exception:
            pass
        reverse_button.clicked.connect(lambda checked=False: self._reverse_remote_order())
        self._reverse_button = reverse_button

        layout.addWidget(drag_handle)
        layout.addWidget(self._settings_toolbar_button())
        layout.addWidget(self._push_sync_toolbar_button())
        layout.addWidget(self._pull_sync_toolbar_button())
        layout.addWidget(self._all_cache_sync_toolbar_button())
        layout.addWidget(self._remove_all_offline_toolbar_button())
        layout.addWidget(self._clear_all_cache_toolbar_button())
        layout.addWidget(sort_button)
        layout.addWidget(reverse_button)
        layout.addStretch(1)
        layout.addWidget(self._purchase_license_toolbar_button())
        layout.addWidget(self._pin_button())
        self._update_config_sync_buttons()
        self._update_app_cache_buttons()
        self._update_purchase_license_button()
        return widget

    def _event_global_point(self, event: Any) -> Any:
        point = event.globalPosition() if hasattr(event, "globalPosition") else event.globalPos()
        return point.toPoint() if hasattr(point, "toPoint") else point

    def _begin_window_drag(self, event: Any) -> None:
        try:
            if event.button() != self.qt.Qt.MouseButton.LeftButton:
                return
            handle = self.window.windowHandle() if hasattr(self.window, "windowHandle") else None
            if handle is not None and hasattr(handle, "startSystemMove") and handle.startSystemMove():
                self._drag_offset = None
                event.accept()
                return
            self._drag_offset = self._event_global_point(event) - self.window.frameGeometry().topLeft()
            event.accept()
        except Exception:
            self._drag_offset = None

    def _continue_window_drag(self, event: Any) -> None:
        if self._drag_offset is None:
            return
        try:
            if not (event.buttons() & self.qt.Qt.MouseButton.LeftButton):
                return
            self.window.move(self._event_global_point(event) - self._drag_offset)
            event.accept()
        except Exception:
            return

    def _end_window_drag(self, event: Any) -> None:
        self._drag_offset = None
        self._clamp_to_screen()
        try:
            event.accept()
        except Exception:
            pass

    def _sort_remote_order(self, sort_mode: str) -> None:
        remotes = _load_visible_remotes()
        if not remotes:
            return
        if sort_mode == "registration":
            self._restore_registration_order([remote.name for remote in remotes])
            return
        if self._sort_uses_storage(sort_mode):
            missing = [remote for remote in remotes if self._storage_sort_value(remote, sort_mode) is None]
            if missing:
                for remote in missing:
                    self._schedule_storage_load(remote)
                self.tray_app._notify(
                    "Sort remotes",
                    "Storage usage is loading. Try again when the values appear.",
                    success=True,
                )
                return
        sorted_names = [remote.name for remote in self._sorted_remotes(remotes, sort_mode)]
        self._save_remote_order(sorted_names)
        self._current_remote_names = []
        self.tray_app.rebuild_menus()

    def _restore_registration_order(self, remote_names: list[str]) -> None:
        settings = load_mount_settings()
        for remote_name in remote_names:
            current = settings.get(remote_name)
            if current is None or current.order is None:
                continue
            settings[remote_name] = MountSettings(
                mount_path=current.mount_path,
                remote_path=current.remote_path,
                mount_flags=list(current.mount_flags),
                auto_mount=current.auto_mount,
                enabled=current.enabled,
                order=None,
            )
        save_mount_settings(settings)
        self._current_remote_names = []
        self._configuration_changed()
        self.tray_app.rebuild_menus()

    def _reverse_remote_order(self) -> None:
        names = [remote.name for remote in _load_visible_remotes()]
        names.reverse()
        self._save_remote_order(names)
        self._current_remote_names = []
        self.tray_app.rebuild_menus()

    def _remote_row(self, remote: core.RemoteInfo, mounted: bool) -> Any:
        usage = self._row_usage(remote, mounted)
        checking_usage = remote.name not in self._usage_cache
        action_pending = remote.name in self._action_pending
        open_tooltip = f"Browse {remote.display_name}"
        title_tooltip = f"{open_tooltip}\n{remote.mount_path}"

        frame = self.qt.QFrame()
        frame.setObjectName("remoteRow")
        frame.setProperty("mounted", mounted)
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        frame.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.PointingHandCursor))
        frame.setFocusPolicy(self.qt.Qt.FocusPolicy.StrongFocus)
        frame.setFixedHeight(REMOTE_ROW_HEIGHT)
        frame.setToolTip(open_tooltip)
        frame.setAcceptDrops(True)
        frame.mouseReleaseEvent = lambda event, row=frame, selected=remote: self._handle_remote_row_click(event, row, selected)
        frame.enterEvent = lambda event, row=frame, tooltip=open_tooltip: self._highlight_remote_row(
            row,
            highlighted=True,
            tooltip=tooltip,
            remote=remote,
        )
        frame.leaveEvent = lambda event, row=frame: self._highlight_remote_row(row, highlighted=False)
        frame.focusInEvent = lambda event, row=frame, selected=remote: self._remote_row_focus(
            event, row, selected, focused=True
        )
        frame.focusOutEvent = lambda event, row=frame, selected=remote: self._remote_row_focus(
            event, row, selected, focused=False
        )
        frame.keyPressEvent = lambda event, selected=remote, row=frame: self._handle_remote_row_key(
            event, selected, row
        )
        frame.dragEnterEvent = lambda event, row=frame, selected=remote: self._remote_drag_enter(event, row, selected)
        frame.dragMoveEvent = lambda event: self._remote_drag_move(event)
        frame.dropEvent = lambda event, selected=remote: self._remote_drop(event, selected)
        frame.setStyleSheet(self._remote_row_style(frame, highlighted=False))
        layout = self.qt.QGridLayout(frame)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(0)
        layout.setColumnMinimumWidth(0, 24)
        layout.setColumnMinimumWidth(2, 126)
        layout.setColumnMinimumWidth(3, 116)
        layout.setColumnMinimumWidth(4, 36)
        layout.setColumnMinimumWidth(5, 36)
        layout.setColumnMinimumWidth(6, 24)
        layout.setColumnStretch(1, 1)

        status_icon = self._remote_status_icon(remote, mounted, checking=checking_usage)
        title = self.qt.QLabel(self._display_remote_name(remote))
        title.setTextFormat(self.qt.Qt.TextFormat.RichText)
        title.setToolTip(title_tooltip)
        title.setFixedWidth(self._name_column_width)
        title.setSizePolicy(self.qt.QSizePolicy.Policy.Expanding, self.qt.QSizePolicy.Policy.Preferred)
        title.enterEvent = lambda event, widget=title, tooltip=title_tooltip: self._show_immediate_tooltip(widget, tooltip)

        usage_indicator = self._usage_indicator(usage, checking_usage=checking_usage)

        status = self.qt.QLabel()
        status.setFixedWidth(120)
        self._set_status_text(status, usage, action_pending=action_pending)
        usage_note = self._drive_usage_note_label(remote)
        status_group = self.qt.QWidget()
        status_layout = self.qt.QHBoxLayout(status_group)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(2)
        status_layout.addWidget(status)
        status_layout.addWidget(usage_note)
        config_button = self._icon_button(
            "ui-config",
            lambda: self._show_mount_config_editor(remote),
            enabled=not action_pending,
            fallback_text="⚙",
        )
        config_button.setProperty("rowControl", True)
        config_tooltip = f"Configure {remote.display_name}" + _shortcut_hint("remote_config")
        config_button.setToolTip(config_tooltip)
        config_button.enterEvent = lambda event, widget=config_button, tooltip=config_tooltip: self._show_immediate_tooltip(
            widget,
            tooltip,
        )
        browser_button = self._icon_button(
            "ui-external-link",
            lambda selected=remote: self._open_remote_in_browser(selected),
            fallback_text="↗",
        )
        browser_button.setProperty("rowControl", True)
        self._update_browser_button(browser_button, remote)
        move_controls, up_button, down_button = self._move_button_stack(remote)

        layout.addWidget(status_icon, 0, 0)
        layout.addWidget(title, 0, 1)
        layout.addWidget(usage_indicator, 0, 2)
        layout.addWidget(status_group, 0, 3)
        layout.addWidget(config_button, 0, 4)
        layout.addWidget(browser_button, 0, 5)
        layout.addWidget(move_controls, 0, 6)
        self._row_widgets[remote.name] = SimpleNamespace(
            frame=frame,
            status_icon=status_icon,
            title=title,
            usage_indicator=usage_indicator,
            status=status,
            usage_note=usage_note,
            config_button=config_button,
            browser_button=browser_button,
            up_button=up_button,
            down_button=down_button,
        )
        return frame

    def _add_remote_row(self) -> Any:
        locked = self._license_locked()
        frame = self.qt.QFrame()
        frame.setObjectName("remoteRow")
        frame.setFrameShape(self.qt.QFrame.Shape.StyledPanel)
        frame.setFixedHeight(REMOTE_ROW_HEIGHT)
        frame.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.PointingHandCursor))
        tooltip = _license_lock_message() if locked else "Add a new remote"
        frame.setToolTip(tooltip)
        frame.setEnabled(not locked)
        frame.mouseReleaseEvent = lambda event: self._show_license_dialog() if locked else self._show_new_remote_wizard()
        frame.enterEvent = lambda event, widget=frame: self._show_immediate_tooltip(widget, tooltip)

        layout = self.qt.QHBoxLayout(frame)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)
        add_button = self._icon_button("ui-add", self._show_new_remote_wizard, fallback_text="+")
        add_button.setEnabled(not locked)
        add_button.setProperty("rowControl", True)
        add_button.setToolTip(tooltip)
        add_button.enterEvent = lambda event, widget=add_button: self._show_immediate_tooltip(widget, tooltip)
        label = self.qt.QLabel("Add remote")
        label.setStyleSheet(_muted_text_style(label))
        layout.addWidget(add_button)
        layout.addWidget(label)
        layout.addStretch(1)
        return frame

    def _update_remote_row(self, remote: core.RemoteInfo, mounted: bool) -> None:
        row = self._row_widgets.get(remote.name)
        if not row:
            return
        usage = self._row_usage(remote, mounted)
        checking_usage = remote.name not in self._usage_cache
        action_pending = remote.name in self._action_pending
        open_tooltip = f"Browse {remote.display_name}"
        title_tooltip = f"{open_tooltip}\n{remote.mount_path}"

        row.frame.setProperty("mounted", mounted)
        row.frame.setToolTip(open_tooltip)
        row.frame.setAcceptDrops(True)
        row.frame.mouseReleaseEvent = lambda event, frame=row.frame, selected=remote: self._handle_remote_row_click(
            event,
            frame,
            selected,
        )
        row.frame.enterEvent = lambda event, frame=row.frame, tooltip=open_tooltip: self._highlight_remote_row(
            frame,
            highlighted=True,
            tooltip=tooltip,
            remote=remote,
        )
        row.frame.dragEnterEvent = lambda event, frame=row.frame, selected=remote: self._remote_drag_enter(
            event, frame, selected
        )
        row.frame.dragMoveEvent = lambda event: self._remote_drag_move(event)
        row.frame.dropEvent = lambda event, selected=remote: self._remote_drop(event, selected)
        row.frame.focusInEvent = lambda event, frame=row.frame, selected=remote: self._remote_row_focus(
            event, frame, selected, focused=True
        )
        row.frame.focusOutEvent = lambda event, frame=row.frame, selected=remote: self._remote_row_focus(
            event, frame, selected, focused=False
        )
        row.frame.keyPressEvent = lambda event, selected=remote, frame=row.frame: self._handle_remote_row_key(
            event, selected, frame
        )
        row.frame.setStyleSheet(self._remote_row_style(row.frame, highlighted=False))

        row.title.setText(self._display_remote_name(remote))
        row.title.setToolTip(title_tooltip)
        row.title.setFixedWidth(self._name_column_width)
        row.title.enterEvent = lambda event, widget=row.title, tooltip=title_tooltip: self._show_immediate_tooltip(
            widget,
            tooltip,
        )

        self._apply_remote_status_icon(row.status_icon, remote, mounted, checking=checking_usage)
        row.usage_indicator.setEnabled(True)
        self._apply_usage_indicator(row.usage_indicator, usage, checking_usage=checking_usage)

        self._set_status_text(row.status, usage, action_pending=action_pending)
        row.config_button.setEnabled(not action_pending)
        config_tooltip = f"Configure {remote.display_name}" + _shortcut_hint("remote_config")
        row.config_button.setToolTip(config_tooltip)
        row.config_button.enterEvent = lambda event, widget=row.config_button, tooltip=config_tooltip: (
            self._show_immediate_tooltip(widget, tooltip)
        )
        self._update_browser_button(row.browser_button, remote)
        self._update_move_button(row.up_button, remote, -1)
        self._update_move_button(row.down_button, remote, 1)

    def _update_browser_button(self, button: Any, remote: core.RemoteInfo) -> None:
        url = _remote_browser_url(remote)
        if url:
            tooltip = _remote_browser_tooltip(remote) + _shortcut_hint("remote_open_browser")
            button.setEnabled(True)
            self._apply_button_icon_if_available(
                button, "ui-external-link", "↗", size=22, color=_provider_color(remote)
            )
            button.setStyleSheet(f"color: {_provider_color(remote)};")
        else:
            tooltip = f"No browser view is configured for {remote.display_name}" + _shortcut_hint("remote_open_browser")
            button.setEnabled(False)
            self._apply_button_icon_if_available(button, "ui-external-link", "↗", size=22)
            button.setStyleSheet("")
        button.setToolTip(tooltip)
        button.enterEvent = lambda event, widget=button, text=tooltip: self._show_immediate_tooltip(widget, text)

    def _move_button_stack(self, remote: core.RemoteInfo) -> tuple[Any, Any, Any]:
        widget = self.qt.QWidget()
        widget.setProperty("rowControl", True)
        layout = self.qt.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        up_button = self._move_button(remote, -1)
        down_button = self._move_button(remote, 1)
        layout.addWidget(up_button)
        layout.addWidget(down_button)
        return widget, up_button, down_button

    def _move_button(self, remote: core.RemoteInfo, delta: int) -> Any:
        button = self._small_icon_button(
            "ui-move-up" if delta < 0 else "ui-move-down",
            lambda: self._move_remote(remote.name, delta),
            fallback_text="▲" if delta < 0 else "▼",
        )
        button.setProperty("rowControl", True)
        self._update_move_button(button, remote, delta)
        return button

    def _update_move_button(self, button: Any, remote: core.RemoteInfo, delta: int) -> None:
        direction = "up" if delta < 0 else "down"
        enabled = self._can_move_remote(remote.name, delta)
        tooltip = f"Move {remote.display_name} {direction}"
        button.setEnabled(enabled)
        button.setToolTip(tooltip)
        button.enterEvent = lambda event, widget=button, text=tooltip: self._show_immediate_tooltip(widget, text)

    def _can_move_remote(self, remote_name: str, delta: int) -> bool:
        try:
            index = self._current_remote_names.index(remote_name)
        except ValueError:
            return False
        target = index + delta
        return 0 <= target < len(self._current_remote_names)

    def _move_remote(self, remote_name: str, delta: int) -> None:
        names = [remote.name for remote in _load_visible_remotes()]
        try:
            index = names.index(remote_name)
        except ValueError:
            return
        target = index + delta
        if not 0 <= target < len(names):
            return
        names[index], names[target] = names[target], names[index]
        self._save_remote_order(names)
        self._current_remote_names = []
        self.tray_app.rebuild_menus()

    def _save_remote_order(self, remote_names: list[str]) -> None:
        settings = load_mount_settings()
        for order, remote_name in enumerate(remote_names):
            current = settings.get(remote_name) or MountSettings()
            settings[remote_name] = MountSettings(
                mount_path=current.mount_path,
                remote_path=current.remote_path,
                mount_flags=list(current.mount_flags),
                auto_mount=current.auto_mount,
                enabled=current.enabled,
                order=order,
            )
        save_mount_settings(settings)
        self._configuration_changed()

    def _sort_uses_storage(self, sort_mode: str) -> bool:
        return sort_mode in STORAGE_SORT_MODES

    def _sorted_remotes(
        self,
        remotes: list[core.RemoteInfo],
        sort_mode: str,
        *,
        reverse: bool = False,
    ) -> list[core.RemoteInfo]:
        mode = sort_mode
        indexed = list(enumerate(remotes))
        if mode == "registration":
            return list(remotes)
        if mode == "name":
            result = [
                remote
                for _index, remote in sorted(
                    indexed,
                    key=lambda item: (
                        item[1].alias.casefold(),
                        item[1].provider.casefold(),
                        item[1].name.casefold(),
                        item[0],
                    ),
                )
            ]
            return list(reversed(result)) if reverse else result
        if mode == "provider":
            result = [
                remote
                for _index, remote in sorted(
                    indexed,
                    key=lambda item: (
                        item[1].provider.casefold(),
                        item[1].alias.casefold(),
                        item[1].name.casefold(),
                        item[0],
                    ),
                )
            ]
            return list(reversed(result)) if reverse else result
        if mode in STORAGE_SORT_MODES:
            result = [remote for _index, remote in sorted(indexed, key=lambda item: self._storage_sort_key(item, mode))]
            return list(reversed(result)) if reverse else result
        return list(remotes)

    def _storage_sort_key(self, item: tuple[int, core.RemoteInfo], sort_mode: str) -> tuple[bool, int, str, int]:
        index, remote = item
        value = self._storage_sort_value(remote, sort_mode)
        if value is None:
            return (True, 0, remote.display_name.casefold(), index)
        if sort_mode in {"size", "used"}:
            value = -value
        return (False, value, remote.display_name.casefold(), index)

    def _storage_sort_value(self, remote: core.RemoteInfo, sort_mode: str) -> int | None:
        usage = self._usage_cache.get(remote.name)
        if usage is None:
            return None
        if sort_mode == "size":
            return usage.total
        if sort_mode == "used":
            return usage.used
        if sort_mode == "remaining" and usage.total is not None and usage.used is not None:
            return max(usage.total - usage.used, 0)
        return None

    def _row_usage(self, remote: core.RemoteInfo, mounted: bool) -> core.StorageUsage:
        del mounted
        return self._usage_cache.get(remote.name) or core.StorageUsage("Checking...")

    def _remote_status_icon(self, remote: core.RemoteInfo, mounted: bool, *, checking: bool) -> Any:
        label = self.qt.QLabel()
        label.setFixedSize(22, 22)
        label.setAlignment(self.qt.Qt.AlignmentFlag.AlignCenter)
        self._apply_remote_status_icon(label, remote, mounted, checking=checking)
        return label

    def _apply_remote_status_icon(self, label: Any, remote: core.RemoteInfo, mounted: bool, *, checking: bool) -> None:
        connected = getattr(self, "_connection_cache", {}).get(remote.name)
        tooltip = self._remote_status_tooltip(remote, mounted, connected, checking=checking)
        label.setToolTip(tooltip)
        label.enterEvent = lambda event, widget=label, text=tooltip: self._show_immediate_tooltip(widget, text)
        pixmap = self._remote_status_pixmap(mounted=mounted, connected=connected, checking=checking)
        if pixmap is not None:
            label.setPixmap(pixmap)
            label.setText("")
            return
        if mounted:
            label.setText("☁▲")
            label.setStyleSheet("color: #0ea5e9;")
        elif connected is True:
            label.setText("☁")
            label.setStyleSheet("color: #f8fafc;")
        else:
            label.setText("")
            label.setStyleSheet("")

    def _remote_status_tooltip(
        self,
        remote: core.RemoteInfo,
        mounted: bool,
        connected: bool | None,
        *,
        checking: bool,
    ) -> str:
        if mounted:
            return f"{remote.display_name} is mounted."
        if connected is True:
            return f"{remote.display_name} is reachable but not mounted."
        if connected is False:
            return f"{remote.display_name} could not be reached."
        if checking:
            return f"Checking {remote.display_name}..."
        return f"{remote.display_name} has not been checked yet."

    def _remote_status_pixmap(self, *, mounted: bool, connected: bool | None, checking: bool) -> Any | None:
        del checking
        if not mounted and connected is not True:
            return None
        return self._status_asset_pixmap("status-mounted.svg" if mounted else "status-cloud.svg")

    def _status_asset_pixmap(self, asset_name: str) -> Any | None:
        path = _packaged_asset_path(asset_name)
        if not path:
            return None
        try:
            icon = self.qt.QIcon(path)
            pixmap = icon.pixmap(22, 22)
            if hasattr(pixmap, "isNull") and pixmap.isNull():
                return None
            return pixmap
        except Exception:
            return None

    def _usage_indicator(self, usage: core.StorageUsage, *, checking_usage: bool) -> Any:
        indicator = self.qt.QProgressBar()
        indicator.setFixedSize(116, 8)
        indicator.setRange(0, 100)
        indicator.setTextVisible(False)
        self._apply_usage_indicator(indicator, usage, checking_usage=checking_usage)
        return indicator

    def _drive_usage_note_label(self, remote: core.RemoteInfo) -> Any:
        label = self.qt.QLabel("ⓘ" if _is_google_drive_remote(remote) else "")
        label.setFixedWidth(16)
        label.setAlignment(self.qt.Qt.AlignmentFlag.AlignCenter)
        if _is_google_drive_remote(remote):
            label.setToolTip(DRIVE_USAGE_NOTE)
            label.setCursor(self.qt.QCursor(self.qt.Qt.CursorShape.PointingHandCursor))
            label.setStyleSheet(f"color: {_provider_color(remote)}; font-weight: 700;")
            label.enterEvent = lambda event, widget=label: self._show_immediate_tooltip(widget, DRIVE_USAGE_NOTE)
            label.mousePressEvent = lambda event, widget=label: self._show_usage_note(widget)
        return label

    def _show_usage_note(self, widget: Any) -> None:
        self.qt.QToolTip.showText(self.qt.QCursor.pos(), DRIVE_USAGE_NOTE, widget)

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
            text_color = _palette_text_color(self.window)
            return (
                f'<span style="color:{color};">{used_gb:.1f}</span>'
                f'<span style="color:{text_color};">/{total_gb:.1f} GB</span>'
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

    def _show_immediate_tooltip(self, widget: Any, tooltip: str) -> None:
        if tooltip:
            self.qt.QToolTip.showText(self.qt.QCursor.pos(), tooltip, widget)

    def _handle_remote_row_click(self, event: Any, row: Any, remote: core.RemoteInfo) -> None:
        child = row.childAt(event.position().toPoint()) if hasattr(event, "position") else None
        while child is not None and child is not row:
            if child.property("rowControl"):
                return
            child = child.parentWidget()
        self._browse_remote(remote, row)

    def _remote_row_style(self, row: Any, *, highlighted: bool) -> str:
        mounted = bool(row.property("mounted"))
        selected = bool(row.property("browserSelected"))
        keyboard_focus = bool(row.property("keyboardFocus"))
        hovered = highlighted or bool(row.property("hovered"))
        border = "rgba(107, 114, 128, 90)"
        background = "rgba(107, 114, 128, 24)" if not mounted else "transparent"
        if not mounted:
            if selected:
                border = "#3b82f6"
                background = "rgba(59, 130, 246, 30)"
            if keyboard_focus:
                border = "#2563eb"
                background = "rgba(37, 99, 235, 45)"
        elif hovered or selected or keyboard_focus:
            border = "#2563eb" if keyboard_focus else "#3b82f6" if selected else "rgba(22, 163, 74, 190)"
            background = (
                "rgba(37, 99, 235, 45)"
                if keyboard_focus
                else "rgba(59, 130, 246, 30)"
                if selected
                else "rgba(22, 163, 74, 36)"
            )
        return (
            "QFrame#remoteRow {"
            f"border: 2px solid {border};"
            "border-radius: 4px;"
            f"background: {background};"
            "}"
        )

    def _highlight_remote_row(
        self,
        row: Any,
        *,
        highlighted: bool,
        tooltip: str | None = None,
        remote: core.RemoteInfo | None = None,
    ) -> None:
        if self._license_locked():
            row.setProperty("hovered", False)
            row.setStyleSheet(self._remote_row_style(row, highlighted=False))
            if highlighted:
                self.qt.QToolTip.showText(self.qt.QCursor.pos(), _license_lock_message(), row)
            return
        row.setProperty("hovered", highlighted)
        row.setStyleSheet(self._remote_row_style(row, highlighted=False))
        if highlighted and tooltip:
            self.qt.QToolTip.showText(self.qt.QCursor.pos(), tooltip, row)
        if highlighted and remote is not None:
            try:
                row.setFocus(self.qt.Qt.FocusReason.MouseFocusReason)
            except Exception:
                pass
            self._select_browser_remote(remote, row)

    def _browse_remote(self, remote: core.RemoteInfo, row: Any) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._show_file_browser_for_remote(remote, row, focus_browser=True)

    def _select_browser_remote(self, remote: core.RemoteInfo, row: Any) -> None:
        if self._license_locked():
            return
        self._show_file_browser_for_remote(remote, row, focus_browser=False)

    def _show_file_browser_for_remote_name(self, remote_name: str, *, focus_browser: bool) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        remote = self._remote_by_name(remote_name)
        row = self._row_widgets.get(remote_name)
        if remote is None or row is None:
            return
        self._show_file_browser_for_remote(remote, row.frame, focus_browser=focus_browser)

    def _show_file_browser_for_remote(self, remote: core.RemoteInfo, row: Any, *, focus_browser: bool) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._set_browser_selected(remote.name)
        self.file_browser.show_remote(remote, row, show_browser=True, focus_browser=focus_browser)

    def _set_browser_selected(self, remote_name: str | None) -> None:
        self._selected_remote_name = remote_name or ""
        for name, widgets in self._row_widgets.items():
            widgets.frame.setProperty("browserSelected", name == remote_name)
            widgets.frame.setStyleSheet(self._remote_row_style(widgets.frame, highlighted=False))

    def _reposition_file_browser(self) -> None:
        file_browser = getattr(self, "file_browser", None)
        if file_browser is None or not file_browser.is_visible() or file_browser.remote is None:
            return
        row = self._row_widgets.get(file_browser.remote.name)
        if row is not None:
            file_browser.show_remote(file_browser.remote, row.frame, show_browser=False)

    def _remote_row_focus(self, event: Any, row: Any, remote: core.RemoteInfo, *, focused: bool) -> None:
        if focused:
            self._set_focus_owner("main")
            for widgets in self._row_widgets.values():
                if widgets.frame is not row:
                    widgets.frame.setProperty("keyboardFocus", False)
                    widgets.frame.setStyleSheet(self._remote_row_style(widgets.frame, highlighted=False))
        row.setProperty("keyboardFocus", focused)
        row.setStyleSheet(self._remote_row_style(row, highlighted=False))
        if focused:
            self._select_browser_remote(remote, row)
        try:
            event.accept()
        except Exception:
            pass

    def _handle_remote_row_key(self, event: Any, remote: core.RemoteInfo, row: Any) -> None:
        if self._license_locked():
            self._show_license_dialog()
            event.accept()
            return
        key = event.key()
        if matches_shortcut(self.qt, event, "remote_move_up"):
            self._move_focused_remote(remote.name, -1)
        elif matches_shortcut(self.qt, event, "remote_move_down"):
            self._move_focused_remote(remote.name, 1)
        elif matches_shortcut(self.qt, event, "common_previous"):
            self._focus_relative_remote(remote.name, -1)
        elif matches_shortcut(self.qt, event, "common_next"):
            self._focus_relative_remote(remote.name, 1)
        elif matches_shortcut(self.qt, event, "remote_toggle_mount"):
            self._toggle_remote_mount(remote)
        elif matches_shortcut(self.qt, event, "remote_config"):
            self._show_mount_config_editor(remote)
        elif matches_shortcut(self.qt, event, "remote_open_browser"):
            self._open_remote_in_browser(remote)
        elif key == self.qt.Qt.Key.Key_Up:
            self._focus_relative_remote(remote.name, -1)
        elif key == self.qt.Qt.Key.Key_Down:
            self._focus_relative_remote(remote.name, 1)
        elif key in {self.qt.Qt.Key.Key_Return, self.qt.Qt.Key.Key_Enter}:
            self._browse_remote(remote, row)
        elif key in {self.qt.Qt.Key.Key_Left, self.qt.Qt.Key.Key_Right}:
            if self._direction_points_to_browser(key):
                self._browse_remote(remote, row)
            else:
                event.accept()
                return
        elif matches_shortcut(self.qt, event, "remote_enter_browser"):
            self._browse_remote(remote, row)
        else:
            event.ignore()
            return
        event.accept()

    def _handle_main_key(self, event: Any) -> bool:
        if self._license_locked():
            self._show_license_dialog()
            event.accept()
            return True
        key = event.key()
        focused_remote_name = self._focused_remote_name()
        if matches_shortcut(self.qt, event, "remote_move_up"):
            self._move_focused_remote(focused_remote_name, -1)
        elif matches_shortcut(self.qt, event, "remote_move_down"):
            self._move_focused_remote(focused_remote_name, 1)
        elif matches_shortcut(self.qt, event, "common_previous"):
            self._focus_relative_remote(focused_remote_name, -1)
        elif matches_shortcut(self.qt, event, "common_next"):
            self._focus_relative_remote(focused_remote_name, 1)
        elif matches_shortcut(self.qt, event, "remote_toggle_mount"):
            focused_remote = self._remote_by_name(focused_remote_name)
            if focused_remote is None:
                return False
            self._toggle_remote_mount(focused_remote)
        elif matches_shortcut(self.qt, event, "remote_config"):
            focused_remote = self._remote_by_name(focused_remote_name)
            if focused_remote is None:
                return False
            self._show_mount_config_editor(focused_remote)
        elif matches_shortcut(self.qt, event, "remote_open_browser"):
            focused_remote = self._remote_by_name(focused_remote_name)
            if focused_remote is None:
                return False
            self._open_remote_in_browser(focused_remote)
        elif key == self.qt.Qt.Key.Key_Up:
            self._focus_relative_remote(focused_remote_name, -1)
        elif key == self.qt.Qt.Key.Key_Down:
            self._focus_relative_remote(focused_remote_name, 1)
        elif key in {self.qt.Qt.Key.Key_Return, self.qt.Qt.Key.Key_Enter}:
            self._focus_current_browser()
        elif key in {self.qt.Qt.Key.Key_Left, self.qt.Qt.Key.Key_Right}:
            if self._direction_points_to_browser(key):
                self._focus_current_browser()
            else:
                event.accept()
                return True
        elif matches_shortcut(self.qt, event, "remote_enter_browser"):
            self._focus_current_browser()
        else:
            return False
        event.accept()
        return True

    def _move_focused_remote(self, remote_name: str, delta: int) -> None:
        if not remote_name or not self._can_move_remote(remote_name, delta):
            return
        self._move_remote(remote_name, delta)
        self._focus_remote_row(remote_name)

    def _direction_points_to_browser(self, key: Any) -> bool:
        side = getattr(self.file_browser, "side", lambda: "right")()
        if side == "left":
            return key == self.qt.Qt.Key.Key_Left
        return key == self.qt.Qt.Key.Key_Right

    def _focused_remote_name(self) -> str:
        selected = getattr(self, "_selected_remote_name", "")
        if selected in getattr(self, "_current_remote_names", []):
            return selected
        for name, widgets in getattr(self, "_row_widgets", {}).items():
            if widgets.frame.hasFocus():
                return name
        remote = getattr(self.file_browser, "remote", None)
        return remote.name if remote is not None else ""

    def _focus_relative_remote(self, remote_name: str, delta: int) -> None:
        names = list(self._current_remote_names)
        if not names:
            return
        try:
            index = names.index(remote_name)
        except ValueError:
            index = 0 if delta >= 0 else len(names) - 1
        else:
            index = min(max(index + delta, 0), len(names) - 1)
        self._focus_remote_row(names[index])

    def _focus_remote_row(self, remote_name: str) -> None:
        self._set_focus_owner("main")
        self._selected_remote_name = remote_name if remote_name in self._row_widgets else ""
        for widgets in self._row_widgets.values():
            widgets.frame.setProperty("keyboardFocus", False)
            widgets.frame.setProperty("hovered", False)
            widgets.frame.setStyleSheet(self._remote_row_style(widgets.frame, highlighted=False))
        row = self._row_widgets.get(remote_name)
        if row is not None:
            row.frame.setFocus(self.qt.Qt.FocusReason.ShortcutFocusReason)
            self._ensure_remote_row_visible(row.frame)

    def _ensure_remote_row_visible(self, row: Any) -> None:
        scroll = getattr(self, "_remote_scroll", None)
        if scroll is None:
            return
        try:
            scroll.ensureWidgetVisible(row, 0, 6)
        except Exception:
            return

    def _focus_current_remote_row(self) -> None:
        name = self._focused_remote_name()
        if not name and self._current_remote_names:
            name = self._current_remote_names[0]
        self._focus_remote_row(name)

    def _focus_current_browser(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        name = self._focused_remote_name()
        row = self._row_widgets.get(name)
        remote = self._remote_by_name(name)
        if row is not None and remote is not None:
            self._browse_remote(remote, row.frame)

    def _remote_by_name(self, name: str) -> core.RemoteInfo | None:
        return next((item for item in _load_visible_remotes() if item.name == name), None)

    def _toggle_remote_mount(self, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        action = core.unmount_remote if core.is_mounted(remote) else core.mount_remote
        self._run_remote_action(remote, action)

    def _remote_drag_enter(self, event: Any, row: Any, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            event.ignore()
            return
        if not event.mimeData().hasFormat(MIME_TYPE):
            event.ignore()
            return
        self._select_browser_remote(remote, row)
        event.acceptProposedAction()

    def _remote_drag_move(self, event: Any) -> None:
        if self._license_locked():
            event.ignore()
            return
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()

    def _remote_drop(self, event: Any, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            event.ignore()
            return
        if not event.mimeData().hasFormat(MIME_TYPE):
            event.ignore()
            return
        modifiers = event.modifiers() if hasattr(event, "modifiers") else event.keyboardModifiers()
        move = bool(modifiers & self.qt.Qt.KeyboardModifier.ShiftModifier)
        self.file_browser.remote = remote
        self.file_browser.path = self.file_browser.backend.current_path(remote.name)
        self.file_browser.accept_drop(bytes(event.mimeData().data(MIME_TYPE)), move=move)
        event.acceptProposedAction()

    def _display_remote_name(self, remote: core.RemoteInfo) -> str:
        name = html.escape(self._truncated_remote_alias(remote, include_provider=bool(remote.provider)))
        if not remote.provider:
            return name
        color = _provider_color(remote)
        provider = html.escape(remote.provider)
        return f'{name} <span style="color:{color};">({provider})</span>'

    def _plain_remote_name(self, remote: core.RemoteInfo) -> str:
        alias = self._truncated_remote_alias(remote, include_provider=bool(remote.provider))
        if remote.provider:
            return f"{alias} ({remote.provider})"
        return alias

    def _truncated_remote_alias(self, remote: core.RemoteInfo, *, include_provider: bool) -> str:
        name = remote.alias
        suffix_length = len(f" ({remote.provider})") if include_provider else 0
        limit = max(4, 28 - suffix_length)
        return name if len(name) <= limit else name[: limit - 3] + "..."

    def _remote_name_width(self, remotes: list[core.RemoteInfo]) -> int:
        displayed = [self._plain_remote_name(remote) for remote in remotes]
        longest = max(displayed, key=len, default="Remote")
        metrics = self.window.fontMetrics()
        return min(max(metrics.horizontalAdvance(longest) + 10, 88), metrics.horizontalAdvance("W" * 28) + 10)

    def _fit_to_content(self, root: Any, scroll: Any, container: Any) -> None:
        layout = root.layout()
        layout.activate()
        container.layout().activate()
        scroll_frame = scroll.frameWidth() * 2
        menu_height = self.window.menuBar().sizeHint().height()
        margins = layout.contentsMargins()
        spacing = max(layout.spacing(), 0)
        toolbar_size = self._layout_item_size(layout, 0)
        add_row_size = self._layout_item_size(layout, 2)
        container_size = container.sizeHint()
        horizontal_padding = margins.left() + margins.right()
        vertical_padding = margins.top() + margins.bottom()
        content_width = max(toolbar_size.width(), add_row_size.width(), container_size.width())
        remote_panel_width = (
            horizontal_padding + content_width + scroll_frame + scroll.verticalScrollBar().sizeHint().width() + 2
        )
        width = remote_panel_width
        height = (
            menu_height
            + vertical_padding
            + toolbar_size.height()
            + add_row_size.height()
            + max(container_size.height(), REMOTE_LIST_MIN_HEIGHT)
            + scroll_frame
            + (spacing * 2)
            + 2
        )
        file_browser = getattr(self, "file_browser", None)
        if getattr(getattr(self, "tray_app", None), "_is_wayland", False):
            try:
                root.setMinimumWidth(remote_panel_width)
                root.setMaximumWidth(remote_panel_width)
            except Exception:
                pass
        if (
            getattr(getattr(self, "tray_app", None), "_is_wayland", False)
            and file_browser is not None
            and file_browser.is_visible()
        ):
            browser_size = file_browser.root.sizeHint()
            browser_width = max(browser_size.width(), EMBEDDED_BROWSER_MIN_WIDTH)
            browser_height = max(browser_size.height(), EMBEDDED_BROWSER_MIN_HEIGHT)
            width += browser_width + 6
            height = max(height, menu_height + browser_height + 16)

        screen = self.window.screen() or self.qt.QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            max_width = max(360, available.width() - 16)
            max_height = max(220, available.height() - 16)
        else:
            max_width = 960
            max_height = 720

        if height > max_height:
            width += scroll.verticalScrollBar().sizeHint().width()
            height = max_height

        target_width = min(max(width, 360), max_width)
        target_height = min(max(height, 220), max_height)
        self._resize_anchored(target_width, target_height, screen)

    def _resize_anchored(self, width: int, height: int, screen: Any | None) -> None:
        """Resize while preserving the tray-side screen-edge offsets."""
        if not self.is_visible() or screen is None:
            self.window.resize(width, height)
            self._clamp_to_screen(screen)
            return
        try:
            available = screen.availableGeometry()
            frame = self.window.frameGeometry()
            left_gap = max(frame.left() - available.left(), 0)
            right_gap = max(available.right() - frame.right(), 0)
            top_gap = max(frame.top() - available.top(), 0)
            bottom_gap = max(available.bottom() - frame.bottom(), 0)
            anchor = getattr(self, "_last_tray_anchor", None)
            if anchor is not None:
                preserve_right = anchor.x() > available.left() + (available.width() / 2)
                preserve_bottom = anchor.y() > available.top() + (available.height() / 2)
            else:
                preserve_right = right_gap < left_gap
                preserve_bottom = bottom_gap < top_gap
            self.window.resize(width, height)
            x = available.right() - right_gap - width + 1 if preserve_right else available.left() + left_gap
            y = available.bottom() - bottom_gap - height + 1 if preserve_bottom else available.top() + top_gap
            self.window.move(x, y)
        except Exception:
            self.window.resize(width, height)
        self._clamp_to_screen(screen)

    def _layout_item_size(self, layout: Any, index: int) -> Any:
        item = layout.itemAt(index)
        if item is None:
            return self.qt.QSize(0, 0)
        widget = item.widget()
        if widget is None:
            return item.sizeHint()
        return widget.sizeHint()

    def _button(self, label: str, callback: Any, *, enabled: bool = True) -> Any:
        button = self.qt.QPushButton(label)
        button.setEnabled(enabled)
        button.clicked.connect(lambda checked=False: callback())
        return button

    def _icon_button(
        self,
        icon_name: str,
        callback: Any,
        *,
        enabled: bool = True,
        fallback_text: str = "",
    ) -> Any:
        button = self._button(fallback_text, callback, enabled=enabled)
        button.setFixedSize(34, 30)
        apply_button_icon(self.qt, button, icon_name, fallback_text=fallback_text, size=22)
        font = button.font()
        font.setPointSize(max(font.pointSize() + 7, 18))
        button.setFont(font)
        return button

    def _small_icon_button(
        self,
        icon_name: str,
        callback: Any,
        *,
        enabled: bool = True,
        fallback_text: str = "",
    ) -> Any:
        button = self._button(fallback_text, callback, enabled=enabled)
        button.setFixedSize(24, 14)
        apply_button_icon(self.qt, button, icon_name, fallback_text=fallback_text, size=12)
        font = button.font()
        font.setPointSize(max(font.pointSize(), 9))
        button.setFont(font)
        return button

    def _run_switch_action(self, remote_name: str, want_mounted: bool) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        remote = next((candidate for candidate in _load_visible_remotes() if candidate.name == remote_name), None)
        if remote is None:
            self.tray_app._notify("Mountlet", f"{remote_name} is no longer available.", success=False)
            self._request_refresh()
            return
        self._run_remote_action(remote, core.mount_remote if want_mounted else core.unmount_remote)

    def _run_remote_action(self, remote: core.RemoteInfo, action: Any) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
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
        getattr(self, "_connection_cache", {}).pop(remote_name, None)
        self.file_browser.invalidate(remote_name)
        self.file_browser.refresh_mount_state(remote_name)
        self.tray_app._notify("Mountlet", _clean_message(message), success=success)
        if not success:
            self._offer_reauthentication_if_relevant(remote_name, message)
        else:
            self._reconcile_offline_changes_after_mount(remote_name)
        self.tray_app.rebuild_menus()
        self._request_refresh()

    def _offer_reauthentication_if_relevant(self, remote_name: str, message: str) -> None:
        if not _message_might_be_auth_failure(message):
            return
        remote = next((candidate for candidate in _load_visible_remotes() if candidate.name == remote_name), None)
        if remote is None:
            return
        reply = self.qt.QMessageBox.question(
            self.window,
            "Reauthenticate remote?",
            f"{remote.display_name} may need to sign in again.\n\n"
            "Reauthenticate it now and retry mounting?",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.Yes,
        )
        if reply != self.qt.QMessageBox.StandardButton.Yes:
            return
        self._run_remote_reauthentication(remote, remount=True)

    def _run_remote_reauthentication(self, remote: core.RemoteInfo, *, remount: bool) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        if remote.name in self._action_pending:
            return
        self._action_pending.add(remote.name)
        self._request_refresh()

        def worker() -> None:
            success, reconnect_message = core.reconnect_remote(remote)
            if success and remount:
                mount_success, mount_message = core.mount_remote(remote)
                message = f"{reconnect_message}\n{mount_message}"
                self._bridge.action_finished.emit(remote.name, mount_success, message)
                return
            self._bridge.action_finished.emit(remote.name, success, reconnect_message)

        threading.Thread(target=worker, daemon=True).start()

    def _mount_all(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._run_bulk_action("Mount all", core.mount_all)

    def _unmount_all(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._run_bulk_action("Unmount all", core.unmount_all)

    def _run_bulk_action(self, title: str, action: Any) -> None:
        remotes = _load_visible_remotes()
        self._run_bulk_action_for_remotes(title, remotes, action)

    def _run_bulk_action_for_remotes(self, title: str, remotes: list[core.RemoteInfo], action: Any) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
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
        pending_names = set(self._action_pending)
        self._action_pending.clear()
        self._usage_cache.clear()
        getattr(self, "_connection_cache", {}).clear()
        for remote_name in pending_names:
            self.file_browser.invalidate(remote_name)
            self.file_browser.refresh_mount_state(remote_name)
            self._reconcile_offline_changes_after_mount(remote_name)
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

    def _reconcile_offline_changes_after_mount(self, remote_name: str) -> None:
        self._schedule_offline_reconcile(remote_name)

    def _refresh_file_browser_mount_state(self, remote_name: str) -> None:
        refresh = getattr(getattr(self, "file_browser", None), "refresh_mount_state", None)
        if refresh is None:
            return
        try:
            refresh(remote_name)
        except Exception:
            return

    def _finish_file_browser_sync_state(self, remote_name: str) -> None:
        finish = getattr(getattr(self, "file_browser", None), "finish_sync", None)
        if finish is None:
            return
        try:
            finish(remote_name)
        except Exception:
            return

    def _schedule_offline_reconcile(self, remote_name: str, *, delay_ms: int = 0) -> None:
        if self._tray_is_quitting() or self._license_locked() or not remote_name:
            return
        scheduled = getattr(self, "_offline_reconcile_scheduled", None)
        if scheduled is None:
            scheduled = set()
            self._offline_reconcile_scheduled = scheduled
        if remote_name in scheduled:
            return
        scheduled.add(remote_name)
        timer = getattr(getattr(self, "qt", None), "QTimer", None)
        if timer is None:
            return
        timer.singleShot(delay_ms, lambda name=remote_name: self._start_offline_reconcile(name))

    def _start_offline_reconcile(self, remote_name: str) -> None:
        scheduled = getattr(self, "_offline_reconcile_scheduled", set())
        scheduled.discard(remote_name)
        running = getattr(self, "_offline_reconcile_running", None)
        if running is None:
            running = set()
            self._offline_reconcile_running = running
        if self._tray_is_quitting() or self._license_locked():
            return
        if remote_name in running:
            scheduled.add(remote_name)
            return
        remote = next((candidate for candidate in _load_visible_remotes() if candidate.name == remote_name), None)
        if remote is None:
            return
        running.add(remote_name)
        self._set_file_browser_status(remote.name, "Uploading or downloading local changes…")

        def worker() -> None:
            try:
                conflicts = self.file_browser.backend.changed_managed_files(remote)
            except Exception as exc:
                self._bridge.offline_reconcile_ready.emit(remote.name, None, exc)
                return
            self._bridge.offline_reconcile_ready.emit(remote.name, conflicts, None)

        threading.Thread(target=worker, daemon=True).start()

    def _start_remote_cache_check(self, remote: core.RemoteInfo, paths: list[str]) -> bool:
        if self._tray_is_quitting() or self._license_locked() or not paths:
            return False
        running = getattr(self, "_offline_reconcile_running", None)
        if running is None:
            running = set()
            self._offline_reconcile_running = running
        if remote.name in running:
            return False
        running.add(remote.name)
        self._set_file_browser_status(remote.name, "Checking cloud changes…")

        def worker() -> None:
            try:
                conflicts = self.file_browser.backend.changed_managed_files(
                    remote,
                    include_remote_checks=True,
                    paths=paths,
                )
            except Exception as exc:
                self._bridge.offline_reconcile_ready.emit(remote.name, None, exc)
                return
            self._bridge.offline_reconcile_ready.emit(remote.name, conflicts, None)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _handle_offline_reconcile_ready(self, remote_name: str, conflicts: object, error: object) -> None:
        if self._tray_is_quitting():
            return
        self._offline_reconcile_running.discard(remote_name)
        self._finish_file_browser_sync_state(remote_name)
        self._refresh_offline_file_watches()
        remote = next((candidate for candidate in _load_visible_remotes() if candidate.name == remote_name), None)
        if remote is None:
            return
        if error is not None:
            self._set_file_browser_status(remote.name, "Could not sync local changes")
            self.tray_app._notify("Local cache", f"Could not sync local file changes: {error}", success=False)
            self._reschedule_pending_offline_reconcile(remote_name)
            return
        if not isinstance(conflicts, list):
            self._reschedule_pending_offline_reconcile(remote_name)
            return
        if not conflicts:
            self._set_file_browser_status(remote.name, "")
            self._refresh_file_browser_mount_state(remote.name)
            self._reschedule_pending_offline_reconcile(remote_name, only_if_dirty=True)
            return
        self._handle_offline_conflicts(remote, conflicts)
        self._reschedule_pending_offline_reconcile(remote_name)

    def _reschedule_pending_offline_reconcile(self, remote_name: str, *, only_if_dirty: bool = False) -> None:
        scheduled = getattr(self, "_offline_reconcile_scheduled", set())
        if remote_name not in scheduled:
            return
        if only_if_dirty and not self._remote_has_pending_local_cache_changes(remote_name):
            scheduled.discard(remote_name)
            return
        scheduled.discard(remote_name)
        self._schedule_offline_reconcile(remote_name, delay_ms=500)

    def _remote_has_pending_local_cache_changes(self, remote_name: str) -> bool:
        try:
            changed = self.file_browser.backend.changed_managed_remote_names()
        except Exception:
            return False
        return remote_name in set(changed) if isinstance(changed, (list, tuple, set)) else False

    def _show_cache_sync_debug_report(self) -> None:
        if getattr(self, "_cache_sync_debug_running", False):
            self.tray_app._notify("Cache sync debug", "A cache sync report is already running.", success=True)
            return
        self._cache_sync_debug_running = True
        dialog = self.qt.QDialog(self.window)
        dialog.setWindowTitle("Cache sync debug")
        layout = self.qt.QVBoxLayout(dialog)
        label = self.qt.QLabel("Collecting cache sync diagnostics. The report will appear here when it finishes.")
        layout.addWidget(label)
        text = self.qt.QPlainTextEdit()
        text.setPlainText("Running cache sync diagnostics…")
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = self.qt.QDialogButtonBox(self.qt.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            dialog.resize(760, 560)
        except Exception:
            pass
        self._cache_sync_debug_dialog = dialog
        self._cache_sync_debug_text = text
        self._cache_sync_debug_label = label
        self._open_child_dialog(SimpleNamespace(dialog=dialog))

        def worker() -> None:
            report = self._cache_sync_debug_report()
            self._bridge.cache_sync_debug_ready.emit(report)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_cache_sync_debug_ready(self, report: str) -> None:
        self._cache_sync_debug_running = False
        text = getattr(self, "_cache_sync_debug_text", None)
        label = getattr(self, "_cache_sync_debug_label", None)
        if label is not None:
            with suppress(Exception):
                label.setText("Copy this report and paste it into the support conversation.")
        if text is not None:
            with suppress(Exception):
                text.setPlainText(report)
                text.selectAll()
        self._refresh_offline_file_watches()
        try:
            changed = self.file_browser.backend.changed_managed_remote_names()
        except Exception:
            changed = []
        for remote_name in changed if isinstance(changed, (list, tuple, set)) else []:
            self._refresh_file_browser_mount_state(str(remote_name))

    def _cache_sync_debug_report(self) -> str:
        lines = [
            "Mountlet cache sync debug",
            f"timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"platform: {platform.platform()}",
            f"python: {sys.version.split()[0]}",
            f"mountlet: {__version__}",
            "",
        ]
        backend = self.file_browser.backend
        try:
            changed_remote_names = backend.changed_managed_remote_names()
        except Exception as exc:
            lines.append(f"changed_managed_remote_names: ERROR {type(exc).__name__}: {exc}")
            return "\n".join(lines)
        lines.append(f"changed_remotes_before: {list(changed_remote_names)}")
        remotes = {remote.name: remote for remote in _load_visible_remotes()}
        if not changed_remote_names:
            lines.append("No pending local cache changes detected.")
            lines.append("")
            lines.extend(self._cache_sync_remote_check_summary(remotes))
            lines.append("")
            lines.extend(self._cache_sync_manifest_summary(remotes))
            return "\n".join(lines)
        for remote_name in changed_remote_names:
            remote = remotes.get(remote_name)
            lines.append("")
            lines.append(f"== {remote_name} ==")
            if remote is None:
                lines.append("remote: not visible in current Mountlet config")
                continue
            diagnostics: list[str] = []
            started = time.perf_counter()
            try:
                conflicts = backend.changed_managed_files(remote, diagnostics=diagnostics)
            except Exception as exc:
                lines.append(f"result: ERROR after {time.perf_counter() - started:.3f}s")
                lines.append(f"error: {type(exc).__name__}: {exc}")
            else:
                lines.append(f"result: ok after {time.perf_counter() - started:.3f}s")
                lines.append(f"conflicts_returned: {len(conflicts)}")
            lines.extend(diagnostics)
            try:
                still_changed = remote_name in set(backend.changed_managed_remote_names())
            except Exception as exc:
                lines.append(f"changed_after: ERROR {type(exc).__name__}: {exc}")
            else:
                lines.append(f"changed_after: {still_changed}")
        lines.append("")
        lines.extend(self._cache_sync_remote_check_summary(remotes))
        lines.append("")
        lines.extend(self._cache_sync_manifest_summary(remotes))
        return "\n".join(lines)

    def _cache_sync_remote_check_summary(self, remotes: dict[str, core.RemoteInfo]) -> list[str]:
        lines = ["remote_check:"]
        backend = self.file_browser.backend
        checked = False
        for remote_name, remote in remotes.items():
            try:
                paths = backend.managed_record_paths(remote_name)
            except Exception as exc:
                lines.append(f"  {remote_name}: ERROR {type(exc).__name__}: {exc}")
                continue
            if not paths:
                continue
            checked = True
            selected = self._remote_change_poll_batch(paths, int(getattr(self, "_remote_change_poll_offsets", {}).get(remote_name, 0)))
            lines.append(f"  {remote_name}: checking {len(selected)} of {len(paths)} managed files")
            diagnostics: list[str] = []
            started = time.perf_counter()
            try:
                conflicts = backend.changed_managed_files(
                    remote,
                    diagnostics=diagnostics,
                    include_remote_checks=True,
                    paths=selected,
                )
            except Exception as exc:
                lines.append(f"  result: ERROR after {time.perf_counter() - started:.3f}s")
                lines.append(f"  error: {type(exc).__name__}: {exc}")
            else:
                lines.append(f"  result: ok after {time.perf_counter() - started:.3f}s")
                lines.append(f"  conflicts_returned: {len(conflicts)}")
            lines.extend(f"  {line}" for line in diagnostics)
            break
        if not checked:
            lines.append("  no managed files to check")
        return lines

    def _cache_sync_manifest_summary(self, remotes: dict[str, core.RemoteInfo]) -> list[str]:
        lines = ["manifest:"]
        records_by_remote = getattr(self.file_browser.backend, "_offline_records", {})
        if not isinstance(records_by_remote, dict) or not records_by_remote:
            return [*lines, "  no offline/cache records"]
        for remote_name, records in sorted(records_by_remote.items()):
            remote = remotes.get(str(remote_name))
            lines.append(
                f"  {remote_name}: provider={getattr(remote, 'provider', '')} "
                f"backend={getattr(remote, 'backend_type', '')} records={len(records) if isinstance(records, dict) else '?'}"
            )
            if not isinstance(records, dict):
                continue
            for path, record in sorted(records.items()):
                if not isinstance(record, dict) or bool(record.get("is_dir")):
                    continue
                try:
                    dirty = self.file_browser.backend.offline_changed(str(remote_name), str(path))
                except Exception as exc:
                    dirty_text = f"ERROR {type(exc).__name__}: {exc}"
                else:
                    dirty_text = str(dirty)
                lines.append(
                    f"    {path}: dirty={dirty_text} protected={bool(record.get('protected'))} "
                    f"local_size={record.get('local_size')} remote_size={record.get('remote_size')} "
                    f"remote_modtime={record.get('remote_modtime')}"
                )
        return lines

    def _set_file_browser_status(self, remote_name: str, message: str) -> None:
        file_browser = getattr(self, "file_browser", None)
        remote = getattr(file_browser, "remote", None)
        status = getattr(file_browser, "status", None)
        if remote is None or remote.name != remote_name or status is None:
            return
        try:
            status.setText(message)
        except Exception:
            return

    def _handle_offline_conflicts(self, remote: core.RemoteInfo, conflicts: list[Any]) -> None:
        resolved = 0
        for conflict in conflicts:
            key = self._offline_conflict_key(conflict)
            if key in self._deferred_offline_conflicts:
                continue
            self._show_conflict_file_in_browser(remote, conflict.path)
            choice = self._ask_offline_conflict_choice(remote, conflict)
            if choice is None:
                self._deferred_offline_conflicts.add(key)
                continue
            try:
                self._set_file_browser_status(remote.name, f"Resolving {conflict.name}…")
                self.file_browser.backend.resolve_managed_conflict(remote, conflict, choice)
            except Exception as exc:
                self.tray_app._notify("Local cache", f"Could not resolve {conflict.name}: {exc}", success=False)
                continue
            self._deferred_offline_conflicts.discard(key)
            resolved += 1
        self._refresh_offline_file_watches()
        if resolved:
            self.file_browser.invalidate(remote.name)
            self.tray_app._notify(
                "Local cache",
                f"Resolved {resolved} changed local file{'s' if resolved != 1 else ''}.",
                success=True,
            )

    def _offline_conflict_key(self, conflict: Any) -> tuple[str, str, float, float]:
        return (
            str(conflict.remote_name),
            str(conflict.path),
            float(conflict.offline_mtime),
            float(conflict.mounted_mtime),
        )

    def _show_conflict_file_in_browser(self, remote: core.RemoteInfo, path: str) -> None:
        folder = parent_browser_path(path)
        row = self._row_widgets.get(remote.name)
        try:
            if row is not None:
                self.file_browser.show_remote(remote, row.frame, show_browser=True, focus_browser=False)
            else:
                self.file_browser.remote = remote
            self.file_browser.path = folder
            self.file_browser.backend.remember_path(remote.name, folder)
            self.file_browser.refresh(force=False)
        except Exception:
            return

    def _ask_offline_conflict_choice(self, remote: core.RemoteInfo, conflict: Any) -> str | None:
        newer_source = "local copy" if conflict.offline_is_newer else "cloud file"
        older_source = "cloud file" if conflict.offline_is_newer else "local copy"
        newer_link = "offline" if conflict.offline_is_newer else "cloud"
        older_link = "cloud" if conflict.offline_is_newer else "offline"
        dialog = self.qt.QDialog(self.window)
        dialog.setWindowTitle("Local file changed")
        layout = self.qt.QVBoxLayout(dialog)
        title = self.qt.QLabel(f"{html.escape(conflict.name)} changed locally and in {html.escape(remote.display_name)}.")
        layout.addWidget(title)
        detail = self.qt.QLabel(
            "Choose which version to keep.<br><br>"
            f"Newer version: <a href=\"{newer_link}\">{html.escape(newer_source)}</a><br>"
            f"Older version: <a href=\"{older_link}\">{html.escape(older_source)}</a>"
        )
        try:
            detail.setTextFormat(self.qt.Qt.TextFormat.RichText)
            detail.setTextInteractionFlags(self.qt.Qt.TextInteractionFlag.TextBrowserInteraction)
            detail.setOpenExternalLinks(False)
        except Exception:
            pass
        detail.linkActivated.connect(lambda target, item=conflict: self._open_conflict_version(item, target))
        layout.addWidget(detail)
        buttons = self.qt.QDialogButtonBox()
        newer_button = buttons.addButton("Use newer", self.qt.QDialogButtonBox.ButtonRole.AcceptRole)
        older_button = buttons.addButton("Use older", self.qt.QDialogButtonBox.ButtonRole.DestructiveRole)
        keep_button = buttons.addButton("Keep both", self.qt.QDialogButtonBox.ButtonRole.ActionRole)
        result: dict[str, str | None] = {"choice": None}

        def choose(button: Any) -> None:
            if button is newer_button:
                result["choice"] = "newer"
            elif button is older_button:
                result["choice"] = "older"
            elif button is keep_button:
                result["choice"] = "keep_both"
            dialog.accept()

        buttons.clicked.connect(choose)
        layout.addWidget(buttons)
        try:
            newer_button.setDefault(True)
        except Exception:
            pass
        dialog.exec()
        return result["choice"]

    def _open_conflict_version(self, conflict: Any, target: str) -> None:
        path = conflict.offline_path if target == "offline" else conflict.mounted_path
        if self.desktop.open_file(path):
            return
        self.tray_app._notify("Open file", f"Could not open {path.name}.", success=False)

    def _open_folder(self, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._open_remote_path(remote, "")

    def _open_remote_path(self, remote: core.RemoteInfo, relative_path: str) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        if not core.is_mounted(remote):
            self.tray_app._notify("Open folder", "Mount the remote before opening its folder.", success=False)
            return
        path = Path(remote.mount_path).joinpath(*[part for part in relative_path.split("/") if part])
        if platform.system() != "Windows" and not path.is_dir():
            self.tray_app._notify(
                "Open folder",
                "The mount folder is not reachable. Remount this remote and try again.",
                success=False,
            )
            return

        def worker() -> None:
            self._bridge.folder_opened.emit(self.desktop.open_folder(str(path)))

        threading.Thread(target=worker, daemon=True).start()

    def _open_remote_in_browser(self, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self.tray_app._open_remote_in_browser(remote)

    def _handle_folder_opened(self, success: bool) -> None:
        if self._tray_is_quitting():
            return
        if not success:
            self.tray_app._notify("Open folder", "Could not open the mount folder.", success=False)

    def _show_app_config_editor(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        old_remotes = _load_visible_remotes()
        mounted_before = self._mounted_remote_names(old_remotes)
        old_base = core.BASE_MOUNT_DIR
        dialog = AppConfigDialog(self.qt, self.window)

        def on_accepted() -> None:
            new_base, _note = core.ensure_base_mount_dir()
            changes = self._remount_changes(old_remotes, mounted_before)
            base_changed = _absolute_path(old_base) != _absolute_path(new_base)
            self._usage_cache.clear()
            getattr(self, "_connection_cache", {}).clear()
            self._setup_remote_change_polling()
            self.tray_app.rebuild_menus()
            self.refresh()
            self._configuration_changed()
            self._ask_remount_for_config_changes(changes, old_base=old_base if base_changed else None)

        self._open_child_dialog(dialog, on_accepted)

    def _show_shortcut_config_editor(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        self._open_child_dialog(ShortcutConfigDialog(self.qt, self.window), self._configuration_changed)

    def _show_config_sync_editor(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        def on_accepted() -> None:
            self._remote_sync_metadata = None
            self.tray_app.rebuild_menus()
            self._configuration_changed()
            self._request_config_sync_metadata_check(_load_visible_remotes())

        self._open_child_dialog(ConfigSyncDialog(self.qt, self.window), on_accepted)

    def _show_mount_config_editor(self, remote: core.RemoteInfo) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        old_remotes = _load_visible_remotes()
        mounted_before = self._mounted_remote_names(old_remotes)
        dialog = MountConfigDialog(self.qt, remote, self.window)

        def on_accepted() -> None:
            if dialog.deleted:
                self._usage_cache.pop(remote.name, None)
                getattr(self, "_connection_cache", {}).pop(remote.name, None)
                self._current_remote_names = []
                if getattr(getattr(self.file_browser, "remote", None), "name", "") == remote.name:
                    self.file_browser.close()
                    self.file_browser.remote = None
                self.file_browser.invalidate(remote.name)
                self.tray_app.rebuild_menus()
                self.refresh()
                self._configuration_changed()
                return
            if dialog.renamed_from and dialog.renamed_to != dialog.renamed_from:
                self._usage_cache.pop(dialog.renamed_from, None)
                getattr(self, "_connection_cache", {}).pop(dialog.renamed_from, None)
                self.file_browser.backend.rename_remote(dialog.renamed_from, dialog.renamed_to)
                if getattr(getattr(self.file_browser, "remote", None), "name", "") == dialog.renamed_from:
                    self.file_browser.close()
                    self.file_browser.remote = None
                self.file_browser.invalidate(dialog.renamed_from)
            core.ensure_base_mount_dir()
            changes = self._remount_changes(old_remotes, mounted_before)
            self._usage_cache.clear()
            getattr(self, "_connection_cache", {}).clear()
            self.tray_app.rebuild_menus()
            self.refresh()
            self._configuration_changed()
            if dialog.remount_after_rename:
                new_remote = next(
                    (candidate for candidate in _load_visible_remotes() if candidate.name == dialog.renamed_to),
                    None,
                )
                if new_remote is not None:
                    self._run_remote_action(new_remote, core.mount_remote)
            self._ask_remount_for_config_changes(changes)

        self._open_child_dialog(dialog, on_accepted)

    def _show_new_remote_wizard(self) -> None:
        if self._license_locked():
            self._show_license_dialog()
            return
        dialog = NewRemoteWizard(self.qt, self.window)

        def on_accepted() -> None:
            self._usage_cache.clear()
            getattr(self, "_connection_cache", {}).clear()
            self._current_remote_names = []
            self.tray_app.rebuild_menus()
            self.refresh()
            self._configuration_changed()

        self._open_child_dialog(dialog, on_accepted)

    def _mounted_remote_names(self, remotes: list[core.RemoteInfo]) -> set[str]:
        return {remote.name for remote in remotes if core.is_mounted(remote)}

    def _remount_changes(
        self,
        old_remotes: list[core.RemoteInfo],
        mounted_before: set[str],
    ) -> list[tuple[core.RemoteInfo, core.RemoteInfo]]:
        old_by_name = {remote.name: remote for remote in old_remotes}
        changes: list[tuple[core.RemoteInfo, core.RemoteInfo]] = []
        for new_remote in _load_visible_remotes():
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

    def _export_config_bundle(self) -> None:
        file_dialog = getattr(self.qt, "QFileDialog", None)
        if file_dialog is None:
            self.tray_app._notify("Export config", "File dialogs are not available.", success=False)
            return
        kwargs = self._file_dialog_kwargs()
        try:
            destination, _filter = file_dialog.getSaveFileName(
                self.window,
                "Export Mountlet config bundle",
                str(bundle_file.default_export_path()),
                f"Mountlet config bundle (*{bundle_file.BUNDLE_EXTENSION});;All files (*)",
                **kwargs,
            )
        except TypeError:
            destination, _filter = file_dialog.getSaveFileName(
                self.window,
                "Export Mountlet config bundle",
                str(bundle_file.default_export_path()),
                f"Mountlet config bundle (*{bundle_file.BUNDLE_EXTENSION});;All files (*)",
            )
        if not destination:
            return
        destination_path = Path(destination).expanduser()
        if destination_path.suffix.casefold() != bundle_file.BUNDLE_EXTENSION:
            destination_path = destination_path.with_suffix(bundle_file.BUNDLE_EXTENSION)
        if destination_path.exists() and not self._confirm_replace_file(destination_path):
            return
        password = self._ask_bundle_password(
            "Export bundle password",
            "Password for this bundle.\n\nLeave blank to export without encryption.",
            confirm=True,
        )
        if password is None:
            return
        remote_destination = self._mounted_remote_file(destination_path)
        if remote_destination is not None and not password and not self._confirm_unencrypted_remote_export():
            return
        try:
            if remote_destination is None:
                exported = bundle_file.export_bundle_file(destination_path, overwrite=True, password=password)
            else:
                remote, relative_path = remote_destination
                with tempfile.TemporaryDirectory(prefix="mountlet-export-") as tempdir:
                    temporary = Path(tempdir) / destination_path.name
                    bundle_file.export_bundle_file(temporary, overwrite=True, password=password)
                    self._copy_local_file_to_remote(temporary, remote, relative_path)
                exported = destination_path
        except Exception as exc:
            self.tray_app._notify("Export config", str(exc), success=False)
            return
        self._bundle_export_completed(destination_path, remote_destination is not None)
        self.tray_app._notify("Export config", f"Exported to {exported}.", success=True)

    def _import_config_bundle(self) -> None:
        file_dialog = getattr(self.qt, "QFileDialog", None)
        if file_dialog is None:
            self.tray_app._notify("Import config", "File dialogs are not available.", success=False)
            return
        kwargs = self._file_dialog_kwargs()
        try:
            selected, _filter = file_dialog.getOpenFileName(
                self.window,
                "Import Mountlet config bundle",
                str(Path.home()),
                f"Mountlet config bundle (*{bundle_file.BUNDLE_EXTENSION});;All files (*)",
                **kwargs,
            )
        except TypeError:
            selected, _filter = file_dialog.getOpenFileName(
                self.window,
                "Import Mountlet config bundle",
                str(Path.home()),
                f"Mountlet config bundle (*{bundle_file.BUNDLE_EXTENSION});;All files (*)",
            )
        if not selected:
            return
        selected_path = Path(selected).expanduser()
        try:
            import_metadata = bundle_file.bundle_metadata(selected_path)
        except Exception:
            import_metadata = None
        detail = f"\n\n{_sync_metadata_summary(import_metadata)}" if isinstance(import_metadata, dict) else ""
        reply = self.qt.QMessageBox.question(
            self.window,
            "Import Mountlet config?",
            "Replace this device's Mountlet and rclone settings with this bundle?\n\n"
            f"Mountlet will first save a restorable backup bundle.{detail}",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.No,
        )
        if reply != self.qt.QMessageBox.StandardButton.Yes:
            return
        password = self._ask_bundle_password(
            "Import bundle password",
            "Bundle password.\n\nLeave blank if this bundle is not encrypted.",
        )
        if password is None:
            return
        try:
            remote_source = self._mounted_remote_file(selected_path)
            if remote_source is None:
                backup_path = bundle_file.import_bundle_file(selected_path, backup=True, password=password)
            else:
                remote, relative_path = remote_source
                with tempfile.TemporaryDirectory(prefix="mountlet-import-") as tempdir:
                    temporary = Path(tempdir) / selected_path.name
                    self._copy_remote_file_to_local(remote, relative_path, temporary)
                    backup_path = bundle_file.import_bundle_file(temporary, backup=True, password=password)
        except Exception as exc:
            self.tray_app._notify("Import config", str(exc), success=False)
            return
        if isinstance(import_metadata, dict):
            self._record_config_sync_state(import_metadata)
            self._remote_sync_metadata = import_metadata
        self._rclone_config_replaced()
        message = "Imported bundle and refreshed remotes."
        if backup_path is not None:
            message += f"\nBackup: {backup_path}"
        self.tray_app._notify("Import config", message, success=True)

    def _confirm_replace_file(self, path: Path) -> bool:
        reply = self.qt.QMessageBox.question(
            self.window,
            "Replace existing bundle?",
            f"{path} already exists.\n\nReplace it?",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.No,
        )
        return reply == self.qt.QMessageBox.StandardButton.Yes

    def _confirm_unencrypted_remote_export(self) -> bool:
        reply = self.qt.QMessageBox.question(
            self.window,
            "Export without password?",
            "This bundle contains cloud credentials and will be written to a mounted remote without encryption.\n\n"
            "Continue?",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.No,
        )
        return reply == self.qt.QMessageBox.StandardButton.Yes

    def _ask_bundle_password(self, title: str, prompt: str, *, confirm: bool = False) -> str | None:
        password = self._read_password(title, prompt)
        if password is None:
            return None
        if not confirm or not password:
            return password
        repeated = self._read_password("Confirm bundle password", "Enter the same password again.")
        if repeated is None:
            return None
        if password != repeated:
            self.tray_app._notify("Config bundle", "The passwords did not match.", success=False)
            return None
        return password

    def _read_password(self, title: str, prompt: str) -> str | None:
        try:
            value, accepted = self.qt.QInputDialog.getText(
                self.window,
                title,
                prompt,
                self.qt.QLineEdit.EchoMode.Password,
            )
        except TypeError:
            value, accepted = self.qt.QInputDialog.getText(self.window, title, prompt)
        if not accepted:
            return None
        return str(value)

    def _open_config_backup_folder(self) -> None:
        backup_path = bundle_file.backup_dir()
        backup_path.mkdir(parents=True, exist_ok=True)
        if not self.desktop.open_folder(str(backup_path)):
            self.tray_app._notify("Open backups", f"Could not open {backup_path}.", success=False)

    def _push_config_sync_bundle(self) -> None:
        target = self._config_sync_target()
        if target is None:
            return
        remote, relative_path = target
        password = self._ask_bundle_password(
            "Sync bundle password",
            "Password for the sync bundle.\n\nLeave blank to sync without encryption.",
            confirm=True,
        )
        if password is None:
            return
        if not password and not self._confirm_unencrypted_remote_export():
            return
        try:
            with tempfile.TemporaryDirectory(prefix="mountlet-sync-push-") as tempdir:
                temporary = Path(tempdir) / Path(relative_path).name
                bundle_file.export_bundle_file(temporary, overwrite=True, password=password)
                metadata = bundle_file.bundle_metadata(temporary) if temporary.exists() else {}
                self._copy_local_file_to_remote(temporary, remote, relative_path)
        except Exception as exc:
            self.tray_app._notify("Push config", str(exc), success=False)
            return
        if metadata:
            self._record_config_sync_state(metadata)
            self._remote_sync_metadata = metadata
        self._update_config_sync_buttons()
        self.tray_app._notify("Push config", f"Pushed config to {remote.display_name}/{relative_path}.", success=True)

    def _pull_config_sync_bundle(self) -> None:
        target = self._config_sync_target()
        if target is None:
            return
        remote, relative_path = target
        metadata = None
        try:
            with tempfile.TemporaryDirectory(prefix="mountlet-sync-info-") as tempdir:
                temporary = Path(tempdir) / Path(relative_path).name
                self._copy_remote_file_to_local(remote, relative_path, temporary)
                metadata = bundle_file.bundle_metadata(temporary)
        except Exception:
            metadata = None
        detail = f"\n\n{_sync_metadata_summary(metadata)}" if isinstance(metadata, dict) else ""
        reply = self.qt.QMessageBox.question(
            self.window,
            "Pull synced config?",
            "Replace this device's Mountlet and rclone settings with the synced bundle?\n\n"
            f"Mountlet will first save a restorable backup bundle.{detail}",
            self.qt.QMessageBox.StandardButton.Yes | self.qt.QMessageBox.StandardButton.No,
            self.qt.QMessageBox.StandardButton.No,
        )
        if reply != self.qt.QMessageBox.StandardButton.Yes:
            return
        password = self._ask_bundle_password(
            "Sync bundle password",
            "Password for the sync bundle.\n\nLeave blank if this bundle is not encrypted.",
        )
        if password is None:
            return
        try:
            with tempfile.TemporaryDirectory(prefix="mountlet-sync-pull-") as tempdir:
                temporary = Path(tempdir) / Path(relative_path).name
                self._copy_remote_file_to_local(remote, relative_path, temporary)
                metadata = bundle_file.bundle_metadata(temporary)
                backup_path = bundle_file.import_bundle_file(temporary, backup=True, password=password)
        except Exception as exc:
            self.tray_app._notify("Pull config", str(exc), success=False)
            return
        if isinstance(metadata, dict):
            self._record_config_sync_state(metadata)
            self._remote_sync_metadata = metadata
        self._rclone_config_replaced()
        message = f"Pulled config from {remote.display_name}/{relative_path}."
        if backup_path is not None:
            message += f"\nBackup: {backup_path}"
        self.tray_app._notify("Pull config", message, success=True)

    def _record_config_sync_state(self, metadata: dict[str, object]) -> None:
        state = _load_config_sync_state()
        try:
            local_hash = bundle_file.current_config_fingerprint()
        except Exception:
            local_hash = str(metadata.get("config_hash", ""))
        if local_hash:
            state["last_synced_hash"] = local_hash
            state["last_synced_hash_kind"] = "operation"
            state["last_local_config_hash"] = local_hash
            state["last_pushed_hash"] = local_hash
            state["last_pulled_hash"] = local_hash
        for key in ("config_hash", "created_at", "device", "system", "system_release", "platform"):
            value = metadata.get(key)
            if value:
                state[f"remote_{key}"] = value
        remote_hash = str(metadata.get("config_hash") or "")
        if remote_hash:
            state["last_remote_config_hash"] = remote_hash
        _save_config_sync_state(state)

    def _config_sync_target(self) -> tuple[core.RemoteInfo, str] | None:
        settings = load_app_settings()
        if not settings.config_sync_remote:
            self.tray_app._notify("Config sync", "Set a config sync location first.", success=False)
            self._show_config_sync_editor()
            return None
        remotes = {remote.name: remote for remote in _load_visible_remotes()}
        remote = remotes.get(settings.config_sync_remote)
        if remote is None:
            self.tray_app._notify("Config sync", "The configured sync remote is not available.", success=False)
            return None
        relative_path = normalize_browser_path(settings.config_sync_path)
        if not relative_path:
            relative_path = "Mountlet/config.mountlet"
        if Path(relative_path).suffix.casefold() != bundle_file.BUNDLE_EXTENSION:
            relative_path = f"{relative_path}{bundle_file.BUNDLE_EXTENSION}"
        return remote, relative_path

    def _mounted_remote_file(self, path: Path) -> tuple[core.RemoteInfo, str] | None:
        absolute_original = os.path.abspath(os.path.expanduser(str(path)))
        absolute = os.path.normcase(absolute_original)
        for remote in _load_visible_remotes():
            root_original = os.path.abspath(os.path.expanduser(remote.mount_path))
            root = os.path.normcase(root_original)
            if absolute == root or not absolute.startswith(root + os.sep):
                continue
            relative = os.path.relpath(absolute_original, root_original).replace(os.sep, "/")
            if relative:
                return remote, relative
        return None

    def _copy_remote_file_to_local(self, remote: core.RemoteInfo, relative_path: str, destination: Path) -> None:
        if self._license_locked():
            raise RuntimeError(_license_lock_message())
        binary = core.find_rclone()
        if not binary:
            raise RuntimeError("rclone was not found.")
        result = subprocess.run(
            [binary, "--config", core.CONFIG_PATH, "copyto", remote_target(remote, relative_path), str(destination)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}.")

    def _copy_local_file_to_remote(self, source: Path, remote: core.RemoteInfo, relative_path: str) -> None:
        if self._license_locked():
            raise RuntimeError(_license_lock_message())
        binary = core.find_rclone()
        if not binary:
            raise RuntimeError("rclone was not found.")
        result = subprocess.run(
            [binary, "--config", core.CONFIG_PATH, "copyto", str(source), remote_target(remote, relative_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}.")

    def _bundle_export_completed(self, destination: Path, exported_to_mounted_remote: bool) -> None:
        self.file_browser.invalidate()
        if not exported_to_mounted_remote:
            return
        parent = str(destination.parent)
        if platform.system() == "Linux" and _open_folder_in_dolphin_tab(parent, focus=False):
            return
        try:
            with os.scandir(parent):
                pass
        except OSError:
            pass

    def _rclone_config_replaced(self) -> None:
        self._usage_cache.clear()
        getattr(self, "_connection_cache", {}).clear()
        self._usage_pending.clear()
        self._action_pending.clear()
        self._current_remote_names = []
        self._selected_remote_name = ""
        self._position_after_fit = self.is_visible()
        self.file_browser.invalidate()
        self.tray_app.rebuild_menus()
        self.refresh()
        self._configuration_changed()

    def _file_dialog_kwargs(self) -> dict[str, Any]:
        if platform.system() != "Windows":
            return {}
        file_dialog = getattr(self.qt, "QFileDialog", None)
        option_type = getattr(file_dialog, "Option", None)
        dont_use_native = getattr(option_type, "DontUseNativeDialog", None)
        return {"options": dont_use_native} if dont_use_native is not None else {}

    def _open_fuse_config_file(self) -> None:
        paths = get_platform().mount_driver_config_paths()
        if not paths:
            self.tray_app._notify(
                "Open config",
                "This platform does not expose a filesystem driver config file.",
                success=False,
            )
            return
        path = next((candidate for candidate in paths if candidate.exists()), paths[0])
        self._open_text_config(path)

    def _open_text_config(self, path: Path, *, ensure_mountlet_config: bool = False) -> None:
        if ensure_mountlet_config:
            ensure_default_config_files()
        if not path.exists():
            self.tray_app._notify("Open config", f"{path} does not exist.", success=False)
            return
        if not self.desktop.open_text_file(path):
            self.tray_app._notify("Open config", f"Could not open {path}.", success=False)

    def _schedule_storage_load(self, remote: core.RemoteInfo) -> None:
        if self._tray_is_quitting() or self._license_locked():
            return
        if remote.name in self._usage_cache and remote.name in self._connection_cache:
            return
        if remote.name in self._usage_pending:
            return
        self._usage_pending.add(remote.name)

        def worker() -> None:
            connected, _message = core.check_remote_connection(remote)
            usage = core.get_storage_usage_details(remote) if connected else core.StorageUsage("?")
            self._bridge.storage_ready.emit(
                remote.name,
                SimpleNamespace(usage=usage, connected=connected),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_storage_ready(self, remote_name: str, payload: object) -> None:
        if self._tray_is_quitting():
            return
        self._usage_pending.discard(remote_name)
        if isinstance(payload, core.StorageUsage):
            usage = payload
            connected = usage.text != "?"
        else:
            usage = getattr(payload, "usage", core.StorageUsage("?"))
            connected = bool(getattr(payload, "connected", False))
        self._usage_cache[remote_name] = usage
        if not hasattr(self, "_connection_cache"):
            self._connection_cache = {}
        self._connection_cache[remote_name] = connected
        if self.is_visible():
            self._request_refresh()

    def prepare_quit(self) -> None:
        self._refresh_pending = False
        self._usage_pending.clear()
        getattr(self, "_connection_cache", {}).clear()
        self._action_pending.clear()
        self._hide_window_stack()

    def _tray_is_quitting(self) -> bool:
        return bool(getattr(getattr(self, "tray_app", None), "_quitting", False))


class MountletTray:
    def __init__(self, qt: SimpleNamespace, refresh_interval: int = 10, instance_lock: Any | None = None) -> None:
        self.qt = qt
        self._instance_lock = instance_lock
        self.refresh_interval = max(refresh_interval, 2)
        self._is_macos = get_platform().system_name == "Darwin"
        self._is_wayland = get_platform().system_name == "Linux" and bool(os.environ.get("WAYLAND_DISPLAY"))
        self._is_gnome_wayland = _is_gnome_wayland()
        self._manual_context_menu = self._is_macos or (self._is_wayland and not self._is_gnome_wayland)
        if self._is_macos:
            _set_macos_accessory_mode()
        self.app = qt.QApplication.instance() or qt.QApplication(sys.argv[:1])
        self.app.setApplicationName("Mountlet")
        self.app.setApplicationDisplayName("Mountlet")
        try:
            self.app.setDesktopFileName("com.ericholt.mountlet")
        except AttributeError:
            pass
        if self._is_macos:
            # QApplication may restore the regular activation policy while it
            # initializes AppKit. Reapply accessory mode before creating windows.
            _set_macos_accessory_mode()
        self.app.setQuitOnLastWindowClosed(False)
        self._quitting = False
        self._allow_forced_exit = True
        self._forced_exit_scheduled = False
        self.remote_menu = qt.QMenu()
        self.app_menu = qt.QMenu()
        self.icon = self._icon()
        self.app.setWindowIcon(self.icon)
        self.tray = qt.QSystemTrayIcon(self.icon, self.app)
        self.tray.setToolTip("Mountlet")
        if not self._manual_context_menu:
            self.tray.setContextMenu(self.app_menu)
        self.tray.show()
        try:
            self.app.processEvents()
        except Exception:
            pass
        self.main_window = MountletWindow(self)
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
            print("    Use the terminal menu instead: mountlet menu", file=sys.stderr)
            return 1

        locked = _license_locked()
        if locked:
            self.main_window.show()

        self.rebuild_menus()
        self.timer.start(self.refresh_interval * 1000)
        if not locked:
            self._schedule_auto_mounts()
        return int(self.app.exec() or 0)

    def _show_license_required_prompt(self) -> None:
        message = self.qt.QMessageBox(self.main_window.window)
        message.setWindowTitle("License required")
        message.setText("Mountlet needs an active license to continue.")
        open_button = message.addButton("Open purchase page", self.qt.QMessageBox.ButtonRole.ActionRole)
        message.addButton(self.qt.QMessageBox.StandardButton.Close)
        message.exec()
        if message.clickedButton() is open_button:
            _open_external_url(
                self.qt,
                self.main_window.window,
                license_control.license_purchase_url(),
                title="Buy license",
            )

    def _handle_activation(self, reason: Any) -> None:
        if getattr(self, "_quitting", False):
            return
        activation_reason = self.qt.QSystemTrayIcon.ActivationReason
        if reason in (activation_reason.Trigger, getattr(activation_reason, "DoubleClick", None)):
            self.main_window.toggle_from_tray()
            self.qt.QTimer.singleShot(25, self.rebuild_menus)
            return
        manual_context = getattr(self, "_manual_context_menu", getattr(self, "_is_macos", False))
        if manual_context and reason == self.qt.QSystemTrayIcon.ActivationReason.Context:
            self.app_menu.popup(self.qt.QCursor.pos())

    def _show_app_settings_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._show_app_config_editor)

    def _show_shortcuts_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._show_shortcut_config_editor)

    def _show_config_sync_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._show_config_sync_editor)

    def _push_config_sync_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._push_config_sync_bundle)

    def _pull_config_sync_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._pull_config_sync_bundle)

    def _show_about_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._show_about)

    def _show_license_from_tray(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.main_window.show()
        self.qt.QTimer.singleShot(0, self.main_window._show_license_dialog)

    def rebuild_menus(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self.remote_menu.clear()
        self.app_menu.clear()
        locked = _license_locked()
        remotes = _load_visible_remotes()
        mounted_names = [remote.display_name for remote in remotes if core.is_mounted(remote)]
        self.tray.setToolTip(_status_tooltip(remotes, mounted_names))

        if locked:
            action = self.remote_menu.addAction(_license_lock_message())
            action.setEnabled(False)
        elif remotes:
            for remote in remotes:
                self._add_remote_menu(remote, self.remote_menu)
        else:
            action = self.remote_menu.addAction("No rclone remotes found")
            action.setEnabled(False)

        status = self.app_menu.addAction(
            _license_lock_message() if locked else _status_tooltip(remotes, mounted_names).replace("Mountlet - ", "")
        )
        status.setEnabled(False)
        self.app_menu.addSeparator()
        if not locked:
            self._add_action(self.app_menu, "Open Mountlet", self.main_window.show)
            self._add_action(self.app_menu, "Update status", self.rebuild_menus)
            self.app_menu.addSeparator()
            self._add_action(self.app_menu, "Mount all", lambda: self._mount_all(remotes), enabled=bool(remotes))
            self._add_action(self.app_menu, "Unmount all", lambda: self._unmount_all(remotes), enabled=bool(remotes))
            self._add_action(self.app_menu, "Add remote", self.main_window._show_new_remote_wizard)
            self.app_menu.addSeparator()
            self._add_action(self.app_menu, "Sync cached files now", self.main_window._sync_all_cached_files)
            self._add_action(self.app_menu, "Remove all offline files", self.main_window._remove_all_offline_files)
            self._add_action(self.app_menu, "Clear all resolved cache", self.main_window._clear_all_cache_files)
            self._add_action(self.app_menu, "Debug cache sync", self.main_window._show_cache_sync_debug_report)
            self.app_menu.addSeparator()
            self._add_action(self.app_menu, "App settings", self._show_app_settings_from_tray)
            self._add_action(self.app_menu, "Keyboard shortcuts", self._show_shortcuts_from_tray)
            self.app_menu.addSeparator()
            self._add_action(self.app_menu, "Export config bundle", self.main_window._export_config_bundle)
            self._add_action(self.app_menu, "Import config bundle", self.main_window._import_config_bundle)
            self._add_action(self.app_menu, "Open config backup folder", self.main_window._open_config_backup_folder)
            self._add_action(self.app_menu, "Set config sync location", self._show_config_sync_from_tray)
            self._add_action(self.app_menu, "Push config to sync location", self._push_config_sync_from_tray)
            self._add_action(self.app_menu, "Pull config from sync location", self._pull_config_sync_from_tray)
            self.main_window._add_open_config_files_menu(self.app_menu)
            self.app_menu.addSeparator()
        self._add_action(self.app_menu, "License", self._show_license_from_tray)
        self._add_action(self.app_menu, "About Mountlet", self._show_about_from_tray)
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
        browser_url = _remote_browser_url(remote)
        if browser_url:
            self._add_action(submenu, "Open in browser", lambda: self._open_remote_in_browser(remote))
        submenu.addSeparator()
        backend = self.main_window.file_browser.backend
        self._add_action(
            submenu,
            "Sync cached files now",
            lambda selected=remote: self.main_window._sync_cached_paths(selected, [("", True)]),
            enabled=bool(backend.managed_record_paths(remote.name)),
        )
        self._add_action(
            submenu,
            "Remove offline files",
            lambda selected=remote: self.main_window.file_browser.remove_remote_offline_for(selected.name),
            enabled=backend.has_offline_content(remote.name, "", is_dir=True),
        )
        self._add_action(
            submenu,
            "Clear resolved cache",
            lambda selected=remote: self.main_window.file_browser.clear_remote_cache_for(selected.name),
            enabled=backend.has_temporary_cache_content(remote.name, "", is_dir=True),
        )
        submenu.addSeparator()
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
        if _license_locked():
            self._show_license_from_tray()
            return
        self.main_window._run_remote_action(remote, action)

    def _mount_all(self, remotes: list[core.RemoteInfo]) -> None:
        if getattr(self, "_quitting", False):
            return
        if _license_locked():
            self._show_license_from_tray()
            return
        self.main_window._mount_all()

    def _schedule_auto_mounts(self) -> None:
        if getattr(self, "_quitting", False) or _license_locked():
            return
        remotes = [remote for remote in _load_visible_remotes() if remote.auto_mount and not core.is_mounted(remote)]
        if not remotes:
            return
        delay_ms = int(load_app_settings().auto_mount_delay * 1000)
        self.qt.QTimer.singleShot(delay_ms, lambda: self._auto_mount(remotes))

    def _auto_mount(self, remotes: list[core.RemoteInfo]) -> None:
        if getattr(self, "_quitting", False) or _license_locked():
            return
        self.main_window._run_bulk_action_for_remotes("Auto-mount", remotes, core.mount_all)

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
        if _license_locked():
            self._show_license_from_tray()
            return
        self.main_window._unmount_all()

    def _open_folder(self, remote: core.RemoteInfo) -> None:
        if getattr(self, "_quitting", False):
            return
        if _license_locked():
            self._show_license_from_tray()
            return
        self.main_window._open_folder(remote)

    def _open_remote_in_browser(self, remote: core.RemoteInfo) -> None:
        if _license_locked():
            self._show_license_from_tray()
            return
        url = _remote_browser_url(remote)
        if not url:
            self._notify("Open in browser", "This remote does not have a known browser view.", success=False)
            return
        def open_url() -> None:
            if not self.qt.QDesktopServices.openUrl(self.qt.QUrl(url)):
                self._notify("Open in browser", "Could not open the browser.", success=False)

        self.qt.QTimer.singleShot(0, open_url)

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
        self._schedule_forced_exit()
        try:
            self.app.exit(0)
        except Exception:
            self.app.quit()

    def _schedule_forced_exit(self) -> None:
        if not getattr(self, "_allow_forced_exit", False):
            return
        if getattr(self, "_forced_exit_scheduled", False):
            return
        self._forced_exit_scheduled = True
        timer = threading.Timer(FORCED_QUIT_SECONDS, self._force_exit_if_still_quitting)
        timer.daemon = True
        timer.start()

    def _force_exit_if_still_quitting(self) -> None:
        if getattr(self, "_quitting", False):
            os._exit(0)

    def _prepare_quit(self) -> None:
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            rclone_wizard.cancel_all_remote_configs()
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
        help="Start the tray without checking rclone first.",
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
    desktop_ready, message = _desktop_session_available()
    if not desktop_ready:
        print(f"[!] {message}", file=sys.stderr)
        print("    Use the terminal menu instead: mountlet menu", file=sys.stderr)
        return 1

    try:
        qt = _load_qt_bindings()
    except TrayDependencyError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    instance_lock = _acquire_instance_lock(qt)
    if instance_lock is None:
        print("[*] Mountlet is already running.", file=sys.stderr)
        return 0

    if not args.skip_readiness_check:
        readiness = setup_wizard.check_readiness()
        if not readiness.ready:
            if not _run_prerequisite_wizard(qt):
                return 1
            # Re-run the complete check so a newly installed rclone gets its
            # config directory before the tray starts.
            if not setup_wizard.check_readiness().ready:
                return 1

    ensure_app_directories()
    ensure_default_config_files()
    core.ensure_base_mount_dir()
    return MountletTray(qt, refresh_interval=args.refresh_interval, instance_lock=instance_lock).run()


if __name__ == "__main__":
    raise SystemExit(main())
