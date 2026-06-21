from __future__ import annotations

import configparser
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .base import PlatformServices
from .processes import external_process_environment


SYSTEM_FILE_MANAGER_ID = "system"
_DISCOVERY_CACHE: dict[str, tuple[FileManager, ...]] = {}


@dataclass(frozen=True)
class FileManager:
    identifier: str
    label: str
    command: tuple[str, ...] = ()
    is_system_default: bool = False
    supports_new_window: bool = False


def default_file_manager_id(platform: PlatformServices) -> str:
    if platform.system_name == "Windows":
        return "explorer"
    if platform.system_name == "Darwin":
        return "finder"
    return SYSTEM_FILE_MANAGER_ID


def discover_file_managers(platform: PlatformServices, *, refresh: bool = False) -> list[FileManager]:
    if not refresh and platform.system_name in _DISCOVERY_CACHE:
        return list(_DISCOVERY_CACHE[platform.system_name])
    if platform.system_name == "Windows":
        managers = _windows_file_managers()
    elif platform.system_name == "Darwin":
        managers = _macos_file_managers()
    else:
        managers = _linux_file_managers()
    _DISCOVERY_CACHE[platform.system_name] = tuple(managers)
    return managers


def clear_file_manager_cache() -> None:
    _DISCOVERY_CACHE.clear()


def resolve_file_manager(platform: PlatformServices, identifier: str | None) -> FileManager:
    managers = discover_file_managers(platform)
    requested = (identifier or default_file_manager_id(platform)).strip()
    for manager in managers:
        if manager.identifier == requested:
            return manager
    default_id = default_file_manager_id(platform)
    for manager in managers:
        if manager.identifier == default_id:
            return manager
    return managers[0]


def open_with_file_manager(manager: FileManager, path: str, *, new_window: bool = False) -> bool:
    if not manager.command:
        return False
    command = _expand_command(manager.command, path)
    if new_window and manager.supports_new_window:
        command = _new_window_command(manager.identifier, command)
    try:
        subprocess.Popen(
            command,
            env=external_process_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def _expand_command(template: tuple[str, ...], path: str) -> list[str]:
    command: list[str] = []
    inserted = False
    for part in template:
        if part in {"%f", "%F", "%u", "%U"}:
            command.append(path)
            inserted = True
        elif part.startswith("%"):
            continue
        else:
            command.append(part)
    if not inserted:
        command.append(path)
    return command


def _new_window_command(identifier: str, command: list[str]) -> list[str]:
    normalized = identifier.lower()
    if normalized == "explorer":
        return [command[0], "/n,", *command[1:]]
    if "dolphin" in normalized and "--new-window" not in command:
        return [command[0], "--new-window", *command[1:]]
    if any(name in normalized for name in ("nautilus", "nemo", "thunar", "pcmanfm", "caja")):
        return [command[0], "--new-window", *command[1:]]
    return command


def _windows_file_managers() -> list[FileManager]:
    managers = [
        FileManager("explorer", "File Explorer", ("explorer.exe",), True, True),
    ]
    known = (
        ("files", "Files", ("Files.exe",)),
        ("directory-opus", "Directory Opus", ("dopus.exe",)),
        ("total-commander", "Total Commander", ("TOTALCMD64.EXE",)),
        ("freecommander", "FreeCommander", ("FreeCommander.exe",)),
    )
    for identifier, label, command in known:
        executable = _windows_executable(command[0])
        if executable:
            managers.append(FileManager(identifier, label, (executable,), supports_new_window=True))
    managers.append(FileManager(SYSTEM_FILE_MANAGER_ID, "System folder handler"))
    return managers


def _windows_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    try:
        import winreg
    except ImportError:
        return None
    key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if value and Path(value).exists():
            return str(value)
    return None


def _macos_file_managers() -> list[FileManager]:
    managers = [
        FileManager("finder", "Finder", ("/usr/bin/open",), True, True),
    ]
    known = (
        ("path-finder", "Path Finder", "Path Finder"),
        ("forklift", "ForkLift", "ForkLift"),
        ("commander-one", "Commander One", "Commander One"),
    )
    application_roots = (Path("/Applications"), Path.home() / "Applications")
    for identifier, label, app_name in known:
        if any((root / f"{app_name}.app").exists() for root in application_roots):
            managers.append(
                FileManager(identifier, label, ("/usr/bin/open", "-a", app_name), supports_new_window=True)
            )
    managers.append(FileManager(SYSTEM_FILE_MANAGER_ID, "System folder handler"))
    return managers


def _linux_file_managers() -> list[FileManager]:
    default_id = _linux_default_file_manager_id()
    discovered: dict[str, FileManager] = {}
    for desktop_file in _linux_desktop_files():
        manager = _parse_linux_file_manager(desktop_file, default_id)
        if manager:
            discovered.setdefault(manager.identifier, manager)

    default_manager = discovered.get(default_id)
    default_label = default_manager.label if default_manager else _desktop_id_label(default_id)
    managers = [
        FileManager(
            SYSTEM_FILE_MANAGER_ID,
            f"System default ({default_label})" if default_label else "System default",
            is_system_default=True,
        )
    ]
    managers.extend(
        sorted(
            discovered.values(),
            key=lambda manager: (manager.identifier != default_id, manager.label.casefold()),
        )
    )
    return managers


def _linux_default_file_manager_id() -> str:
    xdg_mime = shutil.which("xdg-mime")
    if not xdg_mime:
        return ""
    try:
        result = subprocess.run(
            [xdg_mime, "query", "default", "inode/directory"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _linux_desktop_files() -> list[Path]:
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    roots = [data_home, *(Path(item) for item in data_dirs if item)]
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        applications = root.expanduser() / "applications"
        if not applications.is_dir():
            continue
        for path in applications.rglob("*.desktop"):
            identifier = path.relative_to(applications).as_posix().replace("/", "-")
            if identifier in seen:
                continue
            seen.add(identifier)
            files.append(path)
    return files


def _parse_linux_file_manager(path: Path, default_id: str) -> FileManager | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
        return None
    mime_types = {item for item in entry.get("MimeType", "").split(";") if item}
    if "inode/directory" not in mime_types:
        return None
    identifier = path.name
    categories = {item for item in entry.get("Categories", "").split(";") if item}
    if "FileManager" not in categories and identifier != default_id:
        return None
    if entry.getboolean("Hidden", fallback=False) or entry.getboolean("NoDisplay", fallback=False):
        return None
    executable = entry.get("TryExec", "").strip()
    if executable and not shutil.which(executable):
        return None
    try:
        command = tuple(shlex.split(entry.get("Exec", "")))
    except ValueError:
        return None
    if not command or not (shutil.which(command[0]) or Path(command[0]).exists()):
        return None
    return FileManager(
        identifier,
        entry.get("Name", "").strip() or _desktop_id_label(identifier),
        command,
        identifier == default_id,
        True,
    )


def _desktop_id_label(identifier: str) -> str:
    if not identifier:
        return ""
    name = identifier.removesuffix(".desktop").rsplit(".", 1)[-1]
    return name.replace("-", " ").replace("_", " ").title()
