from __future__ import annotations

import json
import re
import shutil
import subprocess
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable

from . import core
from .config_tools.shared import app_cache_dir, app_state_dir, ensure_app_directories
from .settings import offline_root


BROWSER_STATE_FILE = "browser.json"
OFFLINE_CACHE_DIR = "offline"
OFFLINE_MANIFEST_FILE = "offline_manifest.json"
CONFLICT_COPY_RE = re.compile(r"^(?P<stem>.+) \(Mountlet offline \d{8}-\d{6}(?: \d+)?\)(?P<suffix>\.[^.]*)?$")


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


@dataclass(frozen=True)
class OfflineConflict:
    remote_name: str
    path: str
    offline_path: Path
    mounted_path: Path
    offline_mtime: float
    mounted_mtime: float

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def offline_is_newer(self) -> bool:
        return self.offline_mtime >= self.mounted_mtime


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
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        cache_root: Path | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        ensure_app_directories()
        self.state_path = state_path or app_state_dir() / BROWSER_STATE_FILE
        self.cache_root = cache_root or _default_offline_cache_root()
        if cache_root is None:
            self._migrate_legacy_offline_cache()
        self.manifest_path = manifest_path or self.state_path.with_name(OFFLINE_MANIFEST_FILE)
        self._paths = self._load_paths()
        self._offline_records = self._load_offline_manifest()

    def current_path(self, remote_name: str) -> str:
        return normalize_browser_path(self._paths.get(remote_name, ""))

    def remember_path(self, remote_name: str, path: str) -> None:
        self._paths[remote_name] = normalize_browser_path(path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"paths": self._paths}, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def list_entries(self, remote: core.RemoteInfo, path: str) -> list[BrowserEntry]:
        try:
            binary = self._rclone()
        except RuntimeError:
            offline = self._list_offline_entries(remote.name, path)
            if offline is not None:
                return offline
            raise
        result = subprocess.run(
            self._command(binary, "lsjson", remote_target(remote, path), "--max-depth", "1"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            offline = self._list_offline_entries(remote.name, path)
            if offline is not None:
                return offline
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

    def _list_offline_entries(self, remote_name: str, path: str) -> list[BrowserEntry] | None:
        normalized = normalize_browser_path(path)
        directory = self.offline_path(remote_name, path)
        by_name: dict[str, BrowserEntry] = {}
        for record_path, record in self._offline_records.get(remote_name, {}).items():
            if not record_path or parent_browser_path(record_path) != normalized:
                continue
            name = PurePosixPath(record_path).name
            by_name[name] = BrowserEntry(
                name=name,
                path=record_path,
                is_dir=bool(record.get("is_dir")),
                size=max(int(record.get("size") or 0), 0),
                modified=str(record.get("modified") or ""),
            )
        if not directory.is_dir():
            return sorted(by_name.values(), key=lambda entry: (not entry.is_dir, entry.name.casefold())) or None
        for child in directory.iterdir():
            if child.name.startswith(".mountlet-offline"):
                continue
            child_path = join_browser_path(normalized, child.name)
            if child.name in by_name:
                continue
            try:
                stat_result = child.stat()
            except OSError:
                continue
            by_name[child.name] = BrowserEntry(
                name=child.name,
                path=child_path,
                is_dir=child.is_dir(),
                size=0 if child.is_dir() else stat_result.st_size,
                modified=datetime.fromtimestamp(stat_result.st_mtime).astimezone().strftime("%Y-%m-%d %H:%M"),
            )
        if not by_name:
            return None
        return sorted(by_name.values(), key=lambda entry: (not entry.is_dir, entry.name.casefold()))

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

    def delete_entries(self, remote: core.RemoteInfo, entries: Iterable[BrowserEntry]) -> None:
        binary = self._rclone()
        for entry in entries:
            operation = "purge" if entry.is_dir else "deletefile"
            self._run_operation(binary, operation, remote_target(remote, entry.path))
            self.remove_offline(remote.name, entry.path)

    def create_folder(self, remote: core.RemoteInfo, parent: str, name: str) -> None:
        normalized_name = normalize_browser_path(name)
        if not normalized_name or "/" in normalized_name or normalized_name != name.strip():
            raise RuntimeError("Enter a single folder name without slashes")
        self._run_operation(self._rclone(), "mkdir", remote_target(remote, join_browser_path(parent, normalized_name)))

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
            self._record_offline_tree(remote.name, entry, destination)
        else:
            self._run_operation(binary, "copyto", remote_target(remote, entry.path), str(destination))
            self.prepare_offline_open(remote.name, entry.path)
            self._record_offline_entry(remote.name, entry)
        return destination

    def offline_changed(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        normalized = normalize_browser_path(path)
        if is_dir:
            prefix = f"{normalized}/" if normalized else ""
            for record_path, record in self._offline_records.get(remote_name, {}).items():
                if normalized and not record_path.startswith(prefix):
                    continue
                if not bool(record.get("is_dir")) and self.offline_changed(remote_name, record_path):
                    return True
            return False
        record = self._offline_records.get(remote_name, {}).get(normalized)
        if not record or bool(record.get("is_dir")):
            return False
        destination = self.offline_path(remote_name, normalized)
        if not destination.is_file():
            return False
        try:
            stat_result = destination.stat()
        except OSError:
            return False
        recorded_size = record.get("local_size")
        recorded_mtime = record.get("local_mtime_ns")
        if recorded_size is None or recorded_mtime is None:
            return False
        return int(recorded_size) != stat_result.st_size or int(recorded_mtime) != stat_result.st_mtime_ns

    def changed_offline_files(self, remote: core.RemoteInfo) -> list[OfflineConflict]:
        conflicts: list[OfflineConflict] = []
        for path, record in self._offline_records.get(remote.name, {}).items():
            if bool(record.get("is_dir")):
                continue
            offline = self.offline_path(remote.name, path)
            mounted = Path(remote.mount_path).joinpath(*PurePosixPath(path).parts)
            if not offline.is_file() or not mounted.is_file():
                continue
            try:
                offline_stat = offline.stat()
                mounted_stat = mounted.stat()
            except OSError:
                continue
            if _file_digest(offline) == _file_digest(mounted):
                self._update_offline_record_state(remote.name, path, offline)
                continue
            if not self.offline_changed(remote.name, path):
                shutil.copy2(mounted, offline)
                self._update_offline_record_state(remote.name, path, offline)
                continue
            conflicts.append(
                OfflineConflict(
                    remote_name=remote.name,
                    path=path,
                    offline_path=offline,
                    mounted_path=mounted,
                    offline_mtime=offline_stat.st_mtime,
                    mounted_mtime=mounted_stat.st_mtime,
                )
            )
        return conflicts

    def resolve_offline_conflict(self, conflict: OfflineConflict, choice: str) -> Path:
        if choice not in {"newer", "older", "keep_both"}:
            raise ValueError(f"Unknown offline conflict choice: {choice}")
        offline_newer = conflict.offline_is_newer
        if choice == "keep_both":
            kept = _conflict_copy_path(conflict.mounted_path)
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(conflict.offline_path, kept)
            shutil.copy2(conflict.mounted_path, conflict.offline_path)
            self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
            return kept
        use_offline = offline_newer if choice == "newer" else not offline_newer
        if use_offline:
            shutil.copy2(conflict.offline_path, conflict.mounted_path)
            self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
            return conflict.mounted_path
        shutil.copy2(conflict.mounted_path, conflict.offline_path)
        self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
        return conflict.offline_path

    def original_path_for_conflict_copy(self, path: str) -> str | None:
        normalized = normalize_browser_path(path)
        name = PurePosixPath(normalized).name
        match = CONFLICT_COPY_RE.match(name)
        if not match:
            return None
        original_name = f"{match.group('stem')}{match.group('suffix') or ''}"
        parent = parent_browser_path(normalized)
        return join_browser_path(parent, original_name)

    def replace_original_with_conflict_copy(self, remote: core.RemoteInfo, copy_path: str) -> str:
        original = self.original_path_for_conflict_copy(copy_path)
        if original is None:
            raise RuntimeError("This file is not a Mountlet conflict copy")
        source = Path(remote.mount_path).joinpath(*PurePosixPath(normalize_browser_path(copy_path)).parts)
        destination = Path(remote.mount_path).joinpath(*PurePosixPath(original).parts)
        if not source.is_file():
            raise RuntimeError("The kept copy is not available in the mounted folder")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        source.unlink(missing_ok=True)
        return original

    def remove_offline(self, remote_name: str, path: str) -> None:
        destination = self.offline_path(remote_name, path)
        if destination.is_dir():
            self._make_tree_writable(destination)
            shutil.rmtree(destination)
        else:
            self._make_file_writable(destination)
            destination.unlink(missing_ok=True)
        self._remove_empty_parents(destination.parent, self.cache_root / _safe_component(remote_name))
        self._remove_offline_records(remote_name, path)

    def prepare_offline_open(self, remote_name: str, path: str) -> Path:
        """Return the local cache path and repair permissions for external apps.

        Offline files are read-only from Mountlet's perspective because edits
        are not synced back. Some desktop apps still need write permission to
        create locks or temporary state beside the document, so the local cache
        must stay user-readable and user-writable.
        """
        destination = self.offline_path(remote_name, path)
        if destination.is_dir():
            self._make_tree_writable(destination)
        else:
            self._make_file_writable(destination)
        return destination

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

    def has_offline_content(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        """Return whether an entry can lead to a usable offline snapshot.

        A folder can be useful offline even when that folder itself was not
        downloaded as a whole. For example, saving Reports/2026/a.pdf should
        keep Reports and Reports/2026 visibly available while the remote is
        unmounted.
        """
        if self.is_offline(remote_name, path, is_dir=is_dir):
            return True
        if not is_dir:
            return False
        normalized = normalize_browser_path(path)
        prefix = f"{normalized}/" if normalized else ""
        records = self._offline_records.get(remote_name, {})
        for record_path, record in records.items():
            if normalized and not record_path.startswith(prefix):
                continue
            if not normalized and not record_path:
                continue
            if not bool(record.get("is_dir")):
                return True
        directory = self.offline_path(remote_name, path)
        if not directory.is_dir():
            return False
        try:
            for child in directory.rglob("*"):
                if child.name.startswith(".mountlet-offline"):
                    continue
                if child.is_file():
                    return True
        except OSError:
            return False
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

    def _load_offline_manifest(self) -> dict[str, dict[str, dict[str, object]]]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        remotes = data.get("remotes", {}) if isinstance(data, dict) else {}
        if not isinstance(remotes, dict):
            return {}
        normalized: dict[str, dict[str, dict[str, object]]] = {}
        for remote_name, records in remotes.items():
            if not isinstance(records, dict):
                continue
            remote_records: dict[str, dict[str, object]] = {}
            for raw_path, record in records.items():
                if not isinstance(record, dict):
                    continue
                path = normalize_browser_path(str(raw_path))
                if path:
                    remote_records[path] = {
                        "is_dir": bool(record.get("is_dir")),
                        "size": max(int(record.get("size") or 0), 0),
                        "modified": str(record.get("modified") or ""),
                        "cached_at": str(record.get("cached_at") or ""),
                        "local_size": _optional_int(record.get("local_size")),
                        "local_mtime_ns": _optional_int(record.get("local_mtime_ns")),
                        "local_sha256": str(record.get("local_sha256") or ""),
                    }
            if remote_records:
                normalized[str(remote_name)] = remote_records
        return normalized

    def _save_offline_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "remotes": self._offline_records}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    def _record_offline_entry(self, remote_name: str, entry: BrowserEntry) -> None:
        records = self._offline_records.setdefault(remote_name, {})
        cached_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for ancestor in _ancestor_paths(entry.path):
            records.setdefault(
                ancestor,
                {"is_dir": True, "size": 0, "modified": "", "cached_at": cached_at},
            )
        records[normalize_browser_path(entry.path)] = {
            "is_dir": entry.is_dir,
            "size": max(entry.size, 0),
            "modified": entry.modified,
            "cached_at": cached_at,
            **self._offline_file_state(remote_name, entry.path),
        }
        self._save_offline_manifest()

    def _record_offline_tree(self, remote_name: str, entry: BrowserEntry, directory: Path) -> None:
        self._record_offline_entry(remote_name, entry)
        root_path = normalize_browser_path(entry.path)
        records = self._offline_records.setdefault(remote_name, {})
        cached_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for child in directory.rglob("*"):
            if child.name.startswith(".mountlet-offline"):
                continue
            relative = child.relative_to(directory)
            child_path = root_path
            for part in relative.parts:
                child_path = join_browser_path(child_path, part)
            try:
                stat_result = child.stat()
            except OSError:
                continue
            if child.is_file():
                self._make_file_writable(child)
            records[child_path] = {
                "is_dir": child.is_dir(),
                "size": 0 if child.is_dir() else stat_result.st_size,
                "modified": datetime.fromtimestamp(stat_result.st_mtime).astimezone().strftime("%Y-%m-%d %H:%M"),
                "cached_at": cached_at,
                **({} if child.is_dir() else self._offline_file_state(remote_name, child_path)),
            }
        self._save_offline_manifest()

    def _offline_file_state(self, remote_name: str, path: str) -> dict[str, object]:
        destination = self.offline_path(remote_name, path)
        if not destination.is_file():
            return {}
        try:
            stat_result = destination.stat()
        except OSError:
            return {}
        return {
            "local_size": stat_result.st_size,
            "local_mtime_ns": stat_result.st_mtime_ns,
            "local_sha256": _file_digest(destination),
        }

    def _update_offline_record_state(self, remote_name: str, path: str, local_path: Path) -> None:
        record = self._offline_records.get(remote_name, {}).get(normalize_browser_path(path))
        if record is None or not local_path.is_file():
            return
        try:
            stat_result = local_path.stat()
        except OSError:
            return
        record["local_size"] = stat_result.st_size
        record["local_mtime_ns"] = stat_result.st_mtime_ns
        record["local_sha256"] = _file_digest(local_path)
        self._save_offline_manifest()

    def _remove_offline_records(self, remote_name: str, path: str) -> None:
        records = self._offline_records.get(remote_name)
        if not records:
            return
        normalized = normalize_browser_path(path)
        prefix = f"{normalized}/"
        for record_path in list(records):
            if record_path == normalized or record_path.startswith(prefix):
                records.pop(record_path, None)
        cache_root = self.cache_root / _safe_component(remote_name)
        for ancestor in reversed(_ancestor_paths(normalized)):
            if self.offline_path(remote_name, ancestor).exists():
                break
            if any(parent_browser_path(record_path) == ancestor for record_path in records):
                break
            records.pop(ancestor, None)
        if records:
            self._offline_records[remote_name] = records
        else:
            self._offline_records.pop(remote_name, None)
            if not cache_root.exists():
                self._remove_empty_parents(cache_root, self.cache_root)
        self._save_offline_manifest()

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

    def _make_file_writable(self, path: Path) -> None:
        try:
            mode = 0o700 if path.is_dir() else 0o600
            path.chmod(path.stat().st_mode | mode)
        except OSError:
            return

    def _make_tree_writable(self, path: Path) -> None:
        for child in path.rglob("*"):
            self._make_file_writable(child)
        self._make_file_writable(path)

    def _remove_empty_parents(self, start: Path, stop: Path) -> None:
        current = start
        while current != stop and stop in current.parents:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _migrate_legacy_offline_cache(self) -> None:
        if self.cache_root.exists():
            return
        for legacy in _legacy_offline_roots():
            if not legacy.exists() or legacy == self.cache_root:
                continue
            try:
                self.cache_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(self.cache_root))
            except OSError:
                continue
            return


def _safe_component(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return safe.strip(".") or "remote"


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _conflict_copy_path(path: Path) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stem = path.stem or path.name
    suffix = path.suffix
    candidate = path.with_name(f"{stem} (Mountlet offline {timestamp}){suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{stem} (Mountlet offline {timestamp} {counter}){suffix}")
        counter += 1
    return candidate


def _default_offline_cache_root() -> Path:
    return offline_root()


def _legacy_offline_roots() -> tuple[Path, ...]:
    return (
        app_cache_dir() / OFFLINE_CACHE_DIR,
        Path.home() / "Mountlet Offline",
    )


def _ancestor_paths(path: str) -> list[str]:
    normalized = normalize_browser_path(path)
    if not normalized:
        return []
    ancestors: list[str] = []
    current = parent_browser_path(normalized)
    while current:
        ancestors.append(current)
        current = parent_browser_path(current)
    return list(reversed(ancestors))


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
