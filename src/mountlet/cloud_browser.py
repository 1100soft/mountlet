from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable

from . import core
from .config_tools.shared import app_cache_dir, app_state_dir, ensure_app_directories


BROWSER_STATE_FILE = "browser.json"
OFFLINE_CACHE_DIR = "offline"


@dataclass(frozen=True)
class BrowserEntry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: str = ""


@dataclass(frozen=True)
class TransferItem:
    remote_name: str
    path: str
    name: str
    is_dir: bool


def normalize_browser_path(path: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(path.replace("\\", "/")).parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def join_browser_path(parent: str, name: str) -> str:
    return normalize_browser_path(f"{parent}/{name}" if parent else name)


def parent_browser_path(path: str) -> str:
    normalized = normalize_browser_path(path)
    return normalize_browser_path(str(PurePosixPath(normalized).parent)) if normalized else ""


def remote_target(remote: core.RemoteInfo, relative_path: str = "") -> str:
    base = core.remote_source(remote).rstrip("/")
    relative = normalize_browser_path(relative_path)
    return f"{base}/{relative}" if relative else base


def format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


class CloudBrowserBackend:
    def __init__(self, *, state_path: Path | None = None, cache_root: Path | None = None) -> None:
        ensure_app_directories()
        self.state_path = state_path or app_state_dir() / BROWSER_STATE_FILE
        self.cache_root = cache_root or app_cache_dir() / OFFLINE_CACHE_DIR
        self._paths = self._load_paths()

    def current_path(self, remote_name: str) -> str:
        return normalize_browser_path(self._paths.get(remote_name, ""))

    def remember_path(self, remote_name: str, path: str) -> None:
        self._paths[remote_name] = normalize_browser_path(path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"paths": self._paths}, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def list_entries(self, remote: core.RemoteInfo, path: str) -> list[BrowserEntry]:
        binary = self._rclone()
        result = subprocess.run(
            self._command(binary, "lsjson", remote_target(remote, path), "--max-depth", "1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")
        try:
            values = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("rclone returned an invalid folder listing") from exc
        entries: list[BrowserEntry] = []
        for value in values:
            name = str(value.get("Name") or "").strip()
            if not name:
                continue
            modified = _display_time(str(value.get("ModTime") or ""))
            entries.append(
                BrowserEntry(
                    name=name,
                    path=join_browser_path(path, name),
                    is_dir=bool(value.get("IsDir")),
                    size=max(int(value.get("Size") or 0), 0),
                    modified=modified,
                )
            )
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.casefold()))

    def transfer(
        self,
        items: Iterable[TransferItem],
        remotes: dict[str, core.RemoteInfo],
        destination: core.RemoteInfo,
        destination_path: str,
        *,
        move: bool,
    ) -> None:
        binary = self._rclone()
        for item in items:
            source_remote = remotes.get(item.remote_name)
            if source_remote is None:
                raise RuntimeError(f"The source remote {item.remote_name} is no longer available")
            source = remote_target(source_remote, item.path)
            target_path = join_browser_path(destination_path, item.name)
            target = remote_target(destination, target_path)
            if source == target:
                continue
            if item.is_dir:
                operation = "move" if move else "copy"
                arguments = [operation, source, target, "--create-empty-src-dirs"]
                if move:
                    arguments.append("--delete-empty-src-dirs")
            else:
                arguments = ["moveto" if move else "copyto", source, target]
            self._run_operation(binary, *arguments)

    def make_offline(self, remote: core.RemoteInfo, entry: BrowserEntry) -> Path:
        binary = self._rclone()
        destination = self.offline_path(remote.name, entry.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_dir:
            destination.mkdir(parents=True, exist_ok=True)
            self._run_operation(
                binary,
                "copy",
                remote_target(remote, entry.path),
                str(destination),
                "--create-empty-src-dirs",
            )
            self._offline_marker(destination).touch()
        else:
            self._run_operation(binary, "copyto", remote_target(remote, entry.path), str(destination))
        return destination

    def remove_offline(self, remote_name: str, path: str) -> None:
        destination = self.offline_path(remote_name, path)
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink(missing_ok=True)
        self._remove_empty_parents(destination.parent, self.cache_root / _safe_component(remote_name))

    def is_offline(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        destination = self.offline_path(remote_name, path)
        if is_dir and self._offline_marker(destination).exists():
            return True
        if not is_dir and destination.is_file():
            return True
        root = self.cache_root / _safe_component(remote_name)
        parent = destination if destination.is_dir() else destination.parent
        while parent != root and root in parent.parents:
            if self._offline_marker(parent).exists():
                return True
            parent = parent.parent
        return False

    def offline_path(self, remote_name: str, path: str) -> Path:
        root = self.cache_root / _safe_component(remote_name)
        relative = normalize_browser_path(path)
        return root.joinpath(*PurePosixPath(relative).parts) if relative else root

    def _load_paths(self) -> dict[str, str]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        paths = data.get("paths", {}) if isinstance(data, dict) else {}
        if not isinstance(paths, dict):
            return {}
        return {str(name): normalize_browser_path(str(path)) for name, path in paths.items()}

    def _rclone(self) -> str:
        binary = core.find_rclone()
        if not binary:
            raise RuntimeError("rclone was not found")
        return binary

    def _command(self, binary: str, *arguments: str) -> list[str]:
        return [binary, "--config", core.CONFIG_PATH, *arguments]

    def _run_operation(self, binary: str, *arguments: str) -> None:
        result = subprocess.run(
            self._command(binary, *arguments),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")

    def _offline_marker(self, path: Path) -> Path:
        return path / ".mountlet-offline"

    def _remove_empty_parents(self, start: Path, stop: Path) -> None:
        current = start
        while current != stop and stop in current.parents:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent


def _safe_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return safe.strip(".") or "remote"


def _display_time(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


__all__ = [
    "BrowserEntry",
    "CloudBrowserBackend",
    "TransferItem",
    "format_file_size",
    "join_browser_path",
    "normalize_browser_path",
    "parent_browser_path",
    "remote_target",
]
