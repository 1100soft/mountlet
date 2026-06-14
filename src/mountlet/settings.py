#!/usr/bin/env python3

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config_tools.shared import app_config_file, app_mounts_file, ensure_app_directories, legacy_app_config_dirs


@dataclass(frozen=True)
class AppSettings:
    mount_base: str | None = None
    auto_mount: bool = False
    auto_mount_delay: float = 2.0
    start_at_login: bool = False
    open_folder_behavior: str = "current_desktop"
    focus_file_manager: bool = True


@dataclass(frozen=True)
class MountSettings:
    mount_path: str | None = None
    mount_flags: list[str] = field(default_factory=list)
    auto_mount: bool | None = None
    enabled: bool = True
    order: int | None = None


DEFAULT_APP_CONFIG = """# Mountlet app settings.
# rclone account credentials stay in rclone.conf.

[app]
# Leave empty to use ~/cloud_mounts.
mount_base = ""

# Default for remotes without their own auto_mount setting.
auto_mount = false
auto_mount_delay = 2.0
start_at_login = false

[tray]
# current_desktop uses an existing Dolphin window on the current X11 desktop when possible.
# default uses the desktop's normal folder opener.
open_folder_behavior = "current_desktop"
focus_file_manager = true
"""


DEFAULT_MOUNTS_CONFIG = """# Per-remote Mountlet settings.
# Remote names must match the names shown by rclone.
#
# Example:
# [remotes."Work__Drive"]
# auto_mount = true
# order = 10
# mount_path = "drive/Work"
# mount_flags = "--read-only --dir-cache-time 10m"
"""


def ensure_default_config_files() -> None:
    ensure_app_directories()
    _copy_legacy_config_files()
    defaults = {
        app_config_file(): DEFAULT_APP_CONFIG,
        app_mounts_file(): DEFAULT_MOUNTS_CONFIG,
    }
    for path, content in defaults.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _copy_legacy_config_files() -> None:
    current_dir = app_config_file().parent
    for legacy_dir in legacy_app_config_dirs():
        if not legacy_dir.exists() or legacy_dir == current_dir:
            continue
        for filename in ("config.toml", "mounts.toml"):
            source = legacy_dir / filename
            destination = current_dir / filename
            if source.exists() and not destination.exists():
                destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _strip_comment(line: str) -> str:
    in_quote = False
    escaped = False
    result: list[str] = []
    for char in line:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and in_quote:
            result.append(char)
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            result.append(char)
            continue
        if char == "#" and not in_quote:
            break
        result.append(char)
    return "".join(result).strip()


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return bytes(value[1:-1], "utf-8").decode("unicode_escape")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _read_simple_toml(path: Path) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    current = ""
    if not path.exists():
        return data

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = _strip_comment(raw_line)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            data.setdefault(current, {})
            continue
        if "=" not in line or not current:
            continue
        key, value = line.split("=", 1)
        data.setdefault(current, {})[key.strip()] = _parse_value(value)
    return data


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return os.path.expanduser(text)


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_bool_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _optional_int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _toml_string(value: str | None) -> str:
    text = value or ""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def load_app_settings(path: Path | None = None) -> AppSettings:
    source = path or app_config_file()
    data = _read_simple_toml(source)
    app = data.get("app", {})
    tray = data.get("tray", {})
    return AppSettings(
        mount_base=_string_value(app.get("mount_base")),
        auto_mount=_bool_value(app.get("auto_mount"), False),
        auto_mount_delay=max(_float_value(app.get("auto_mount_delay"), 2.0), 0.0),
        start_at_login=_bool_value(app.get("start_at_login"), _autostart_file().exists()),
        open_folder_behavior=str(tray.get("open_folder_behavior", "current_desktop")).strip() or "current_desktop",
        focus_file_manager=_bool_value(tray.get("focus_file_manager"), True),
    )


def _remote_name_from_section(section: str) -> str | None:
    prefix = "remotes."
    if not section.startswith(prefix):
        return None
    name = section[len(prefix) :].strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name or None


def load_mount_settings(path: Path | None = None) -> dict[str, MountSettings]:
    source = path or app_mounts_file()
    data = _read_simple_toml(source)
    remotes: dict[str, MountSettings] = {}
    for section, values in data.items():
        remote_name = _remote_name_from_section(section)
        if not remote_name:
            continue
        flags = str(values.get("mount_flags", "")).strip()
        remotes[remote_name] = MountSettings(
            mount_path=_string_value(values.get("mount_path")),
            mount_flags=shlex.split(flags) if flags else [],
            auto_mount=_optional_bool_value(values.get("auto_mount")),
            enabled=_bool_value(values.get("enabled"), True),
            order=_optional_int_value(values.get("order")),
        )
    return remotes


def save_app_settings(settings: AppSettings, path: Path | None = None) -> None:
    destination = path or app_config_file()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "# Mountlet app settings.",
            "# rclone account credentials stay in rclone.conf.",
            "",
            "[app]",
            "# Leave empty to use ~/cloud_mounts.",
            f"mount_base = {_toml_string(settings.mount_base)}",
            "",
            "# Default for remotes without their own auto_mount setting.",
            f"auto_mount = {_toml_bool(settings.auto_mount)}",
            f"auto_mount_delay = {settings.auto_mount_delay:g}",
            f"start_at_login = {_toml_bool(settings.start_at_login)}",
            "",
            "[tray]",
            "# current_desktop uses an existing Dolphin window on the current X11 desktop when possible.",
            "# default uses the desktop's normal folder opener.",
            f"open_folder_behavior = {_toml_string(settings.open_folder_behavior)}",
            f"focus_file_manager = {_toml_bool(settings.focus_file_manager)}",
            "",
        ]
    )
    destination.write_text(content, encoding="utf-8")


def _autostart_file() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "autostart" / "mountlet.desktop"


def set_start_at_login(enabled: bool, path: Path | None = None) -> None:
    destination = path or _autostart_file()
    if enabled:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Mountlet",
                    "Comment=Mount cloud storage folders with Mountlet",
                    "Exec=mountlet tray",
                    "Terminal=false",
                    "X-GNOME-Autostart-enabled=true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return
    try:
        destination.unlink()
    except FileNotFoundError:
        pass


def _remote_section_name(remote_name: str) -> str:
    return f'remotes.{_toml_string(remote_name)}'


def save_mount_settings(settings: dict[str, MountSettings], path: Path | None = None) -> None:
    destination = path or app_mounts_file()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Per-remote Mountlet settings.",
        "# Remote names must match the names shown by rclone.",
        "",
    ]
    for remote_name in sorted(settings):
        remote = settings[remote_name]
        lines.extend(
            [
                f"[{_remote_section_name(remote_name)}]",
                f"enabled = {_toml_bool(remote.enabled)}",
            ]
        )
        if remote.auto_mount is not None:
            lines.append(f"auto_mount = {_toml_bool(remote.auto_mount)}")
        if remote.order is not None:
            lines.append(f"order = {remote.order}")
        lines.extend(
            [
                f"mount_path = {_toml_string(remote.mount_path)}",
                f"mount_flags = {_toml_string(' '.join(remote.mount_flags))}",
                "",
            ]
        )
    destination.write_text("\n".join(lines), encoding="utf-8")


__all__ = [
    "AppSettings",
    "MountSettings",
    "ensure_default_config_files",
    "load_app_settings",
    "load_mount_settings",
    "save_app_settings",
    "save_mount_settings",
    "set_start_at_login",
]
