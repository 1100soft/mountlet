from __future__ import annotations

import json
import re
import shutil
import subprocess
import hashlib
import time
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from . import core
from .config_tools.shared import app_cache_dir, app_state_dir, ensure_app_directories
from .metadata_index import IndexedEntry, MetadataIndex
from .settings import offline_root


BROWSER_STATE_FILE = "browser.json"
OFFLINE_CACHE_DIR = "offline"
OFFLINE_MANIFEST_FILE = "offline_manifest.json"
REMOTE_CURRENT_DIR = ".mountlet-remote-current"
RCLONE_FILE_OPERATION_TIMEOUT_SECONDS = 120
RCLONE_CACHE_SYNC_TIMEOUT_SECONDS = 45
RCLONE_METADATA_TIMEOUT_SECONDS = 15
DRIVE_EDITABLE_DOCUMENT_FORMATS = ("docx", "xlsx", "pptx", "svg")
DRIVE_EDITABLE_DOCUMENT_FORMATS_VALUE = ",".join(DRIVE_EDITABLE_DOCUMENT_FORMATS)
GOOGLE_APPS_MIME_PREFIX = "application/vnd.google-apps."
GOOGLE_DOC_IMPORT_REQUIRED_MESSAGE = "can't update google document type without --drive-import-formats"
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


@dataclass(frozen=True)
class OfflineContentState:
    offline: bool = False
    protected: bool = False
    temporary: bool = False
    partial: bool = False


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


def _browser_entry_from_lsjson_value(value: object) -> BrowserEntry | None:
    if not isinstance(value, dict):
        return None
    relative = normalize_browser_path(str(value.get("Path") or value.get("Name") or ""))
    if not relative:
        return None
    name = PurePosixPath(relative).name
    is_dir = bool(value.get("IsDir"))
    return BrowserEntry(
        name=name,
        path=relative,
        is_dir=is_dir,
        size=0 if is_dir else max(int(value.get("Size") or 0), 0),
        modified=_display_time(str(value.get("ModTime") or "")),
    )


def _is_google_photos_upload_listing(remote: core.RemoteInfo, path: str, stderr: str) -> bool:
    return (
        remote.backend_type.casefold() == "gphotos"
        and normalize_browser_path(path).casefold() == "upload"
        and "directory not found" in stderr.casefold()
    )


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
        self._offline_lock = threading.RLock()
        self._offline_records = self._load_offline_manifest()
        self.index = MetadataIndex(self.state_path.with_name("metadata-index.sqlite3"))
        self.operation_output_callback: Callable[[str], None] | None = None

    def current_path(self, remote_name: str) -> str:
        return normalize_browser_path(self._paths.get(remote_name, ""))

    def remember_path(self, remote_name: str, path: str) -> None:
        self._paths[remote_name] = normalize_browser_path(path)
        self._save_paths()

    def rename_remote(self, old_name: str, new_name: str) -> None:
        if old_name == new_name:
            return
        if old_name in self._paths:
            self._paths[new_name] = self._paths.pop(old_name)
            self._save_paths()
        with self._offline_lock:
            if old_name in self._offline_records:
                self._offline_records[new_name] = self._offline_records.pop(old_name)
                self._save_offline_manifest()
        old_root = self.cache_root / _safe_component(old_name)
        new_root = self.cache_root / _safe_component(new_name)
        if old_root.exists() and old_root != new_root and not new_root.exists():
            new_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_root), str(new_root))

    def rename_indexed_remote(self, old_name: str, new_remote: core.RemoteInfo) -> None:
        self.index.rename_remote(old_name, new_remote)

    def remove_indexed_remote(self, remote_name: str) -> None:
        self.index.remove_remote(remote_name)

    def _save_paths(self) -> None:
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
            if _is_google_photos_upload_listing(remote, path, result.stderr):
                return []
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
        entries = sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        self.index.upsert_folder(remote, path, entries)
        return entries

    def list_entries_recursive(self, remote: core.RemoteInfo, entry: BrowserEntry) -> list[BrowserEntry]:
        if not entry.is_dir:
            return [entry]
        binary = self._rclone()
        result = subprocess.run(
            self._command(binary, "lsjson", remote_target(remote, entry.path), "--recursive"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")
        try:
            values = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("rclone returned an invalid recursive folder listing") from exc
        entries: list[BrowserEntry] = []
        for value in values:
            relative = normalize_browser_path(str(value.get("Path") or value.get("Name") or ""))
            if not relative:
                continue
            name = PurePosixPath(relative).name
            is_dir = bool(value.get("IsDir"))
            entries.append(
                BrowserEntry(
                    name=name,
                    path=join_browser_path(entry.path, relative),
                    is_dir=is_dir,
                    size=0 if is_dir else max(int(value.get("Size") or 0), 0),
                    modified=_display_time(str(value.get("ModTime") or "")),
                )
            )
        return sorted(entries, key=lambda item: (item.path.count("/"), not item.is_dir, item.path.casefold()))

    def list_files_recursive(self, remote: core.RemoteInfo, entry: BrowserEntry) -> list[BrowserEntry]:
        return [candidate for candidate in self.list_entries_recursive(remote, entry) if not candidate.is_dir]

    def cached_entries(self, remote: core.RemoteInfo, path: str) -> list[BrowserEntry]:
        return [
            BrowserEntry(
                name=entry.name,
                path=entry.path,
                is_dir=entry.is_dir,
                size=entry.size,
                modified=entry.modified,
            )
            for entry in self.index.cached_folder(remote.name, path)
        ]

    def search_index(
        self,
        query: str,
        *,
        remotes: Iterable[core.RemoteInfo] = (),
        limit: int = 100,
    ) -> list[IndexedEntry]:
        return self.index.search(query, remotes=remotes, limit=limit)

    def indexed_entry_count(self, remote_name: str | None = None) -> int:
        return self.index.count_entries(remote_name)

    def remote_fully_indexed(self, remote_name: str) -> bool:
        return self.index.is_remote_fully_indexed(remote_name)

    def index_remote_tree(self, remote: core.RemoteInfo) -> int:
        binary = self._rclone()
        result = subprocess.run(
            self._command(binary, "lsjson", remote_target(remote, ""), "--recursive"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=None,
            **core.PLATFORM.command_process_options(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")
        try:
            values = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("rclone returned an invalid recursive folder listing") from exc
        entries = [_browser_entry_from_lsjson_value(value) for value in values]
        entries = [entry for entry in entries if entry is not None]
        self.index.upsert_entries(remote, entries)
        self.index.mark_remote_fully_indexed(remote)
        return len(entries)

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

    def copy_local_paths(
        self,
        paths: Iterable[Path],
        destination: core.RemoteInfo,
        destination_path: str,
    ) -> None:
        binary = self._rclone()
        for source in paths:
            source = Path(source)
            if not source.exists():
                raise RuntimeError(f"{source} does not exist")
            target_path = join_browser_path(destination_path, source.name)
            target = remote_target(destination, target_path)
            if source.is_dir():
                self._run_operation(binary, "copy", str(source), target, "--create-empty-src-dirs", timeout=None)
            else:
                self._run_operation(binary, "copyto", str(source), target, timeout=None)

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

    def rename_entry(self, remote: core.RemoteInfo, entry: BrowserEntry, new_name: str) -> str:
        normalized_name = normalize_browser_path(new_name)
        if not normalized_name or "/" in normalized_name or normalized_name != new_name.strip():
            raise RuntimeError("Enter a single file or folder name without slashes")
        old_path = normalize_browser_path(entry.path)
        new_path = join_browser_path(parent_browser_path(old_path), normalized_name)
        if old_path == new_path:
            return new_path

        new_local = self.offline_path(remote.name, new_path)
        with self._offline_lock:
            records = self._offline_records.get(remote.name, {})
            new_prefix = f"{new_path}/"
            if new_local.exists() or any(path == new_path or path.startswith(new_prefix) for path in records):
                raise RuntimeError(f"A cached item named {normalized_name} already exists")

        self._run_operation(
            self._rclone(),
            "moveto",
            remote_target(remote, old_path),
            remote_target(remote, new_path),
        )
        self._rename_offline_path(remote.name, old_path, new_path)
        self.index.rename_path(remote.name, old_path, new_path)
        return new_path

    def _rename_offline_path(self, remote_name: str, old_path: str, new_path: str) -> None:
        old = normalize_browser_path(old_path)
        new = normalize_browser_path(new_path)
        old_prefix = f"{old}/"
        with self._offline_lock:
            old_local = self.offline_path(remote_name, old)
            new_local = self.offline_path(remote_name, new)
            if old_local.exists():
                new_local.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_local), str(new_local))
            records = self._offline_records.get(remote_name, {})
            renamed_records: dict[str, dict[str, object]] = {}
            for path in list(records):
                if path != old and not path.startswith(old_prefix):
                    continue
                record = records.pop(path)
                renamed = new if path == old else f"{new}/{path[len(old_prefix):]}"
                renamed_records[renamed] = record
            if renamed_records:
                records.update(renamed_records)
                self._save_offline_manifest()

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
                timeout=None,
            )
            self._offline_marker(destination).touch()
            self._record_offline_tree(remote.name, entry, destination, protected=True)
        else:
            self._download_remote_file(
                binary,
                remote,
                entry.path,
                destination,
                timeout=None,
            )
            self.prepare_offline_open(remote.name, entry.path)
            self._record_offline_entry(remote.name, entry, protected=True)
        return destination

    def cache_file(self, remote: core.RemoteInfo, entry: BrowserEntry) -> Path:
        if entry.is_dir:
            raise RuntimeError("Folders are not opened through the file cache.")
        binary = self._rclone()
        destination = self.offline_path(remote.name, entry.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._download_remote_file(binary, remote, entry.path, destination, timeout=None)
        self.prepare_offline_open(remote.name, entry.path)
        self._record_offline_entry(remote.name, entry, protected=False)
        return destination

    def offline_changed(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        normalized = normalize_browser_path(path)
        if is_dir:
            prefix = f"{normalized}/" if normalized else ""
            for record_path, record in list(self._offline_records.get(remote_name, {}).items()):
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
        if int(recorded_size) == stat_result.st_size and int(recorded_mtime) == stat_result.st_mtime_ns:
            return False
        recorded_hash = str(record.get("local_sha256") or "")
        if recorded_hash:
            try:
                current_hash = _file_digest(destination)
            except OSError:
                return True
            if current_hash == recorded_hash:
                self._update_offline_record_state(remote_name, normalized, destination)
                return False
        return True

    def managed_file_paths(self, remote_name: str | None = None) -> dict[str, list[Path]]:
        result: dict[str, list[Path]] = {}
        for candidate_name, records in list(self._offline_records.items()):
            if remote_name is not None and candidate_name != remote_name:
                continue
            files: list[Path] = []
            for path, record in list(records.items()):
                if bool(record.get("is_dir")):
                    continue
                local = self.offline_path(candidate_name, path)
                if local.is_file():
                    files.append(local)
            if files:
                result[candidate_name] = files
        return result

    def managed_record_paths(self, remote_name: str) -> list[str]:
        records = self._offline_records.get(remote_name, {})
        return [
            path
            for path, record in list(records.items())
            if not bool(record.get("is_dir")) and self.offline_path(remote_name, path).is_file()
        ]

    def managed_record_paths_under(self, remote_name: str, path: str) -> list[str]:
        normalized = normalize_browser_path(path)
        prefix = f"{normalized}/" if normalized else ""
        records = self._offline_records.get(remote_name, {})
        return [
            record_path
            for record_path, record in list(records.items())
            if not bool(record.get("is_dir"))
            and (record_path == normalized or not normalized or record_path.startswith(prefix))
            and self.offline_path(remote_name, record_path).is_file()
        ]

    def managed_file_paths_under(self, remote_name: str, path: str) -> list[Path]:
        normalized = normalize_browser_path(path)
        prefix = f"{normalized}/" if normalized else ""
        files: list[Path] = []
        for record_path, record in list(self._offline_records.get(remote_name, {}).items()):
            if bool(record.get("is_dir")):
                continue
            if record_path == normalized or not normalized or record_path.startswith(prefix):
                local = self.offline_path(remote_name, record_path)
                if local.exists():
                    files.append(local)
        return files

    def remote_name_for_offline_path(self, path: Path) -> str | None:
        try:
            candidate = path.expanduser().resolve(strict=False)
        except OSError:
            candidate = path.expanduser()
        for remote_name in list(self._offline_records):
            root = self.cache_root / _safe_component(remote_name)
            try:
                candidate.relative_to(root.resolve(strict=False))
            except (OSError, ValueError):
                continue
            return remote_name
        return None

    def changed_managed_remote_names(self) -> list[str]:
        changed: list[str] = []
        for remote_name in list(self._offline_records):
            if self.changed_managed_paths(remote_name):
                changed.append(remote_name)
        return changed

    def changed_managed_paths(self, remote_name: str) -> list[str]:
        records = self._offline_records.get(remote_name, {})
        return [
            path
            for path, record in list(records.items())
            if not bool(record.get("is_dir")) and self.offline_changed(remote_name, path)
        ]

    def changed_offline_files(self, remote: core.RemoteInfo) -> list[OfflineConflict]:
        conflicts: list[OfflineConflict] = []
        for path, record in list(self._offline_records.get(remote.name, {}).items()):
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

    def changed_managed_files(
        self,
        remote: core.RemoteInfo,
        *,
        diagnostics: list[str] | None = None,
        include_remote_checks: bool = False,
        paths: Iterable[str] | None = None,
    ) -> list[OfflineConflict]:
        try:
            binary = self._rclone()
        except RuntimeError as exc:
            _diagnostic_append(diagnostics, f"rclone: unavailable ({exc})")
            return []
        _diagnostic_append(diagnostics, f"remote: {remote.name} provider={remote.provider} backend={remote.backend_type}")
        conflicts: list[OfflineConflict] = []
        failures: list[str] = []
        records = self._offline_records.get(remote.name, {})
        if paths is not None:
            candidate_paths = [normalize_browser_path(path) for path in paths]
        elif include_remote_checks:
            candidate_paths = self.managed_record_paths(remote.name)
        else:
            candidate_paths = self.changed_managed_paths(remote.name)
        _diagnostic_append(diagnostics, f"candidate_paths: {len(candidate_paths)}")
        if not candidate_paths:
            if include_remote_checks:
                _diagnostic_append(diagnostics, "no managed files to check")
            else:
                _diagnostic_append(diagnostics, "no locally changed managed files")
            return []
        for path in candidate_paths:
            record = records.get(path)
            if not isinstance(record, dict) or bool(record.get("is_dir")):
                continue
            local = self.offline_path(remote.name, path)
            _diagnostic_append(diagnostics, f"file: {path}")
            _diagnostic_append(diagnostics, f"  local_path: {local}")
            if not local.is_file():
                _diagnostic_append(diagnostics, "  skip: local file is missing")
                continue
            previous_hash = str(record.get("local_sha256") or "")
            if not previous_hash:
                _diagnostic_append(diagnostics, "  baseline: missing local hash; recording current local state")
                self._update_offline_record_state(remote.name, path, local)
                continue
            try:
                local_hash = _file_digest(local)
            except OSError:
                _diagnostic_append(diagnostics, "  skip: could not hash local file")
                continue
            local_changed = local_hash != previous_hash
            with suppress(OSError):
                stat_result = local.stat()
                _diagnostic_append(
                    diagnostics,
                    f"  local: size={stat_result.st_size} mtime_ns={stat_result.st_mtime_ns}",
                )
            _diagnostic_append(diagnostics, f"  baseline_hash: {_short_digest(previous_hash)}")
            _diagnostic_append(diagnostics, f"  local_hash: {_short_digest(local_hash)} changed={local_changed}")
            current = self.remote_current_path(remote.name, path)
            metadata: dict[str, object] | None = None
            cloud_changed: bool | None = None
            has_remote_metadata = self._record_has_remote_metadata(record)
            if local_changed or include_remote_checks or has_remote_metadata:
                started = time.perf_counter()
                try:
                    metadata = self._remote_file_metadata(
                        binary,
                        remote,
                        path,
                        timeout=RCLONE_METADATA_TIMEOUT_SECONDS,
                    )
                except RuntimeError as exc:
                    _diagnostic_append(
                        diagnostics,
                        f"  metadata: failed after {time.perf_counter() - started:.3f}s: {exc}",
                    )
                else:
                    _diagnostic_append(
                        diagnostics,
                        "  metadata: "
                        f"ok in {time.perf_counter() - started:.3f}s "
                        f"size={metadata.get('Size')} modtime={metadata.get('ModTime')} "
                        f"hashes={sorted(_normalized_hashes(metadata.get('Hashes')))}",
                    )
                if metadata is not None:
                    if include_remote_checks and not local_changed and not has_remote_metadata:
                        self._record_remote_metadata(remote.name, path, metadata)
                        _diagnostic_append(diagnostics, "  decision: initialized remote metadata baseline")
                        continue
                    cloud_changed = not self._remote_metadata_matches_record(record, metadata)
                    _diagnostic_append(diagnostics, f"  cloud_changed_by_metadata: {cloud_changed}")
                elif include_remote_checks and not local_changed:
                    _diagnostic_append(diagnostics, "  decision: skipped remote check; metadata unavailable")
                    continue
            try:
                if cloud_changed is not False:
                    started = time.perf_counter()
                    self._download_remote_file(binary, remote, path, current, timeout=RCLONE_CACHE_SYNC_TIMEOUT_SECONDS)
                    _diagnostic_append(
                        diagnostics,
                        f"  cloud_download: ok in {time.perf_counter() - started:.3f}s",
                    )
            except RuntimeError as exc:
                _diagnostic_append(
                    diagnostics,
                    f"  cloud_download: failed after {time.perf_counter() - started:.3f}s: {exc}",
                )
                failures.append(f"{path}: {exc}")
                continue
            if cloud_changed is not False:
                try:
                    current_hash = _file_digest(current)
                except OSError:
                    _diagnostic_append(diagnostics, "  skip: could not hash downloaded cloud copy")
                    continue
                cloud_changed = current_hash != previous_hash
                _diagnostic_append(
                    diagnostics,
                    f"  cloud_hash: {_short_digest(current_hash)} changed={cloud_changed}",
                )
            if not local_changed and not cloud_changed:
                self._update_offline_record_state(remote.name, path, local)
                self._record_remote_metadata(remote.name, path, metadata)
                _diagnostic_append(diagnostics, "  decision: no change")
                continue
            if not local_changed and cloud_changed:
                shutil.copy2(current, local)
                self._update_offline_record_state(remote.name, path, local)
                self._record_remote_metadata(remote.name, path, metadata)
                _diagnostic_append(diagnostics, "  decision: downloaded cloud change to local cache")
                continue
            if local_changed and not cloud_changed:
                try:
                    started = time.perf_counter()
                    imported_google_doc = self._upload_remote_file(
                        binary,
                        remote,
                        path,
                        local,
                        timeout=RCLONE_CACHE_SYNC_TIMEOUT_SECONDS,
                        remote_metadata=metadata,
                    )
                    _diagnostic_append(
                        diagnostics,
                        f"  upload: ok in {time.perf_counter() - started:.3f}s",
                    )
                except RuntimeError as exc:
                    _diagnostic_append(
                        diagnostics,
                        f"  upload: failed after {time.perf_counter() - started:.3f}s: {exc}",
                    )
                    failures.append(f"{path}: {exc}")
                    continue
                if imported_google_doc:
                    try:
                        self._download_remote_file(
                            binary,
                            remote,
                            path,
                            current,
                            timeout=RCLONE_CACHE_SYNC_TIMEOUT_SECONDS,
                        )
                        shutil.copy2(current, local)
                    except RuntimeError as exc:
                        _diagnostic_append(diagnostics, f"  canonical_download: failed: {exc}")
                    else:
                        _diagnostic_append(diagnostics, "  canonical_download: refreshed local cache")
                metadata_after = None
                if imported_google_doc:
                    with suppress(RuntimeError):
                        metadata_after = self._remote_file_metadata(
                            binary,
                            remote,
                            path,
                            timeout=RCLONE_METADATA_TIMEOUT_SECONDS,
                        )
                self._update_offline_record_state(remote.name, path, local)
                self._clear_record_remote_metadata(remote.name, path)
                if metadata_after is not None:
                    self._record_remote_metadata(remote.name, path, metadata_after)
                _diagnostic_append(
                    diagnostics,
                    f"  post_upload_dirty: {self.offline_changed(remote.name, path)}",
                )
                with suppress(OSError):
                    current.unlink()
                continue
            if local_hash == current_hash:
                self._update_offline_record_state(remote.name, path, local)
                self._record_remote_metadata(remote.name, path, metadata)
                _diagnostic_append(diagnostics, "  decision: local and cloud are identical")
                continue
            _diagnostic_append(diagnostics, "  decision: conflict")
            conflicts.append(
                OfflineConflict(
                    remote_name=remote.name,
                    path=path,
                    offline_path=local,
                    mounted_path=current,
                    offline_mtime=local.stat().st_mtime,
                    mounted_mtime=current.stat().st_mtime,
                )
            )
        if failures:
            sample = failures[0]
            suffix = f" ({len(failures) - 1} more)" if len(failures) > 1 else ""
            _diagnostic_append(diagnostics, f"failures: {sample}{suffix}")
            raise RuntimeError(f"Some local changes could not be synced: {sample}{suffix}")
        _diagnostic_append(diagnostics, f"conflicts: {len(conflicts)}")
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

    def resolve_managed_conflict(self, remote: core.RemoteInfo, conflict: OfflineConflict, choice: str) -> Path:
        if choice not in {"newer", "older", "keep_both"}:
            raise ValueError(f"Unknown managed conflict choice: {choice}")
        binary = self._rclone()
        local_newer = conflict.offline_is_newer
        if choice == "keep_both":
            kept_remote_path = _conflict_copy_remote_path(conflict.path)
            self._upload_remote_file(binary, remote, kept_remote_path, conflict.offline_path)
            shutil.copy2(conflict.mounted_path, conflict.offline_path)
            self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
            with suppress(OSError):
                conflict.mounted_path.unlink()
            return conflict.offline_path
        use_local = local_newer if choice == "newer" else not local_newer
        if use_local:
            imported_google_doc = self._upload_remote_file(binary, remote, conflict.path, conflict.offline_path)
            if imported_google_doc:
                self._download_remote_file(binary, remote, conflict.path, conflict.mounted_path)
                shutil.copy2(conflict.mounted_path, conflict.offline_path)
            self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
            with suppress(OSError):
                conflict.mounted_path.unlink()
            return conflict.offline_path
        shutil.copy2(conflict.mounted_path, conflict.offline_path)
        self._update_offline_record_state(conflict.remote_name, conflict.path, conflict.offline_path)
        with suppress(OSError):
            conflict.mounted_path.unlink()
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

    def free_cache(self, remote_name: str, path: str) -> int:
        return self._free_cache_records(remote_name, path)

    def free_remote_cache(self, remote_name: str) -> int:
        return self._free_cache_records(remote_name, "")

    def free_all_resolved_cache(self) -> int:
        removed = 0
        for remote_name in list(self._offline_records):
            removed += self._free_cache_records(remote_name, "")
        return removed

    def remove_remote_offline(self, remote_name: str) -> int:
        return self._remove_offline_tree(remote_name)

    def remove_all_offline(self) -> int:
        removed = 0
        for remote_name in list(self._offline_records):
            removed += self._remove_offline_tree(remote_name)
        return removed

    def prepare_offline_open(self, remote_name: str, path: str) -> Path:
        """Return the local cache path and repair permissions for external apps.

        Offline files are local snapshots, not live two-way sync. Some desktop
        apps need write permission to create locks or temporary state beside the
        document, so the local cache must stay user-readable and user-writable.
        Mountlet records snapshot metadata and prompts for conflict handling
        when a locally changed snapshot differs from the mounted cloud file.
        """
        destination = self.offline_path(remote_name, path)
        if destination.is_dir():
            self._make_tree_writable(destination)
        else:
            self._make_file_writable(destination)
        return destination

    def is_offline(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        return self.offline_content_state(remote_name, path, is_dir=is_dir).offline

    def offline_content_state(self, remote_name: str, path: str, *, is_dir: bool = False) -> OfflineContentState:
        normalized = normalize_browser_path(path)
        records = self._offline_records.get(remote_name, {})
        record = records.get(normalized)
        destination = self.offline_path(remote_name, normalized)
        if not is_dir:
            if record is not None and not bool(record.get("is_dir")):
                protected = bool(record.get("protected"))
                return OfflineContentState(offline=protected, protected=protected, temporary=not protected)
            if destination.is_file():
                return OfflineContentState(offline=True, protected=True)
            return OfflineContentState()
        full_offline = bool(
            record is not None
            and bool(record.get("is_dir"))
            and bool(record.get("protected"))
            and bool(record.get("complete", True))
        )
        destination = self.offline_path(remote_name, path)
        if record is None and self._offline_marker(destination).exists():
            full_offline = True
        prefix = f"{normalized}/" if normalized else ""
        protected = full_offline
        temporary = False
        for record_path, candidate in records.items():
            if record_path == normalized:
                if not bool(candidate.get("is_dir")) or bool(candidate.get("complete", True)):
                    protected = protected or bool(candidate.get("protected"))
                    temporary = temporary or not bool(candidate.get("protected"))
                continue
            if normalized and not record_path.startswith(prefix):
                continue
            if not normalized and not record_path:
                continue
            if bool(candidate.get("is_dir")) and not bool(candidate.get("complete", True)):
                continue
            protected = protected or bool(candidate.get("protected"))
            temporary = temporary or not bool(candidate.get("protected"))
        if not protected and not temporary and destination.is_dir():
            try:
                for child in destination.rglob("*"):
                    if child.name.startswith(".mountlet-offline"):
                        continue
                    if child.is_file():
                        protected = True
                        break
            except OSError:
                pass
        return OfflineContentState(
            offline=full_offline,
            protected=protected,
            temporary=temporary,
            partial=(protected or temporary) and not full_offline,
        )

    def is_partially_offline(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        if not is_dir:
            return False
        return self.has_offline_content(remote_name, path, is_dir=True) and not self.is_offline(
            remote_name,
            path,
            is_dir=True,
        )

    def is_cached(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        normalized = normalize_browser_path(path)
        if normalized in self._offline_records.get(remote_name, {}):
            return True
        destination = self.offline_path(remote_name, path)
        if destination.is_dir():
            return is_dir
        return destination.is_file()

    def has_offline_content(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        """Return whether an entry can lead to a usable offline snapshot.

        A folder can be useful offline even when that folder itself was not
        downloaded as a whole. For example, saving Reports/2026/a.pdf should
        keep Reports and Reports/2026 visibly available while the remote is
        unmounted.
        """
        state = self.offline_content_state(remote_name, path, is_dir=is_dir)
        return state.protected

    def has_cached_content(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        if self.is_cached(remote_name, path, is_dir=is_dir):
            return True
        if not is_dir:
            return False
        normalized = normalize_browser_path(path)
        prefix = f"{normalized}/" if normalized else ""
        records = self._offline_records.get(remote_name, {})
        return any((not normalized or record_path.startswith(prefix)) for record_path in records)

    def has_protected_content(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        return self.offline_content_state(remote_name, path, is_dir=is_dir).protected

    def has_temporary_cache_content(self, remote_name: str, path: str, *, is_dir: bool = False) -> bool:
        return self.offline_content_state(remote_name, path, is_dir=is_dir).temporary

    def offline_path(self, remote_name: str, path: str) -> Path:
        root = self.cache_root / _safe_component(remote_name)
        relative = normalize_browser_path(path)
        return root.joinpath(*PurePosixPath(relative).parts) if relative else root

    def remote_current_path(self, remote_name: str, path: str) -> Path:
        root = self.cache_root / _safe_component(remote_name) / REMOTE_CURRENT_DIR
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
                    is_dir = bool(record.get("is_dir"))
                    if "complete" in record:
                        complete = bool(record.get("complete"))
                    elif is_dir:
                        complete = self._offline_marker(self.offline_path(str(remote_name), path)).exists() or bool(
                            record.get("modified")
                        )
                    else:
                        complete = True
                    remote_records[path] = {
                        "is_dir": is_dir,
                        "size": max(int(record.get("size") or 0), 0),
                        "modified": str(record.get("modified") or ""),
                        "cached_at": str(record.get("cached_at") or ""),
                        "local_size": _optional_int(record.get("local_size")),
                        "local_mtime_ns": _optional_int(record.get("local_mtime_ns")),
                        "local_sha256": str(record.get("local_sha256") or ""),
                        "remote_size": _optional_int(record.get("remote_size")),
                        "remote_modtime": str(record.get("remote_modtime") or ""),
                        "remote_hashes": _normalized_hashes(record.get("remote_hashes")),
                        "protected": bool(record.get("protected", True)),
                        "complete": complete,
                    }
            if remote_records:
                normalized[str(remote_name)] = remote_records
        return normalized

    def _save_offline_manifest(self) -> None:
        with self._offline_lock:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.manifest_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "remotes": self._offline_records}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.manifest_path)

    def _record_offline_entry(self, remote_name: str, entry: BrowserEntry, *, protected: bool = True) -> None:
        with self._offline_lock:
            records = self._offline_records.setdefault(remote_name, {})
            cached_at = datetime.now().astimezone().isoformat(timespec="seconds")
            for ancestor in _ancestor_paths(entry.path):
                current = records.setdefault(
                    ancestor,
                    {
                        "is_dir": True,
                        "size": 0,
                        "modified": "",
                        "cached_at": cached_at,
                        "protected": protected,
                        "complete": False,
                    },
                )
                if protected:
                    current["protected"] = True
            records[normalize_browser_path(entry.path)] = {
                "is_dir": entry.is_dir,
                "size": max(entry.size, 0),
                "modified": entry.modified,
                "cached_at": cached_at,
                "protected": protected,
                "complete": True,
                **self._offline_file_state(remote_name, entry.path),
            }
            self._save_offline_manifest()

    def _record_offline_tree(self, remote_name: str, entry: BrowserEntry, directory: Path, *, protected: bool = True) -> None:
        with self._offline_lock:
            self._record_offline_entry(remote_name, entry, protected=protected)
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
                    "protected": protected,
                    "complete": True,
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
        with self._offline_lock:
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

    def _record_remote_metadata(
        self,
        remote_name: str,
        path: str,
        metadata: dict[str, object] | None,
        *,
        save: bool = True,
    ) -> None:
        if metadata is None:
            return
        with self._offline_lock:
            record = self._offline_records.get(remote_name, {}).get(normalize_browser_path(path))
            if record is None:
                return
            remote_size = _optional_int(metadata.get("Size"))
            if remote_size is not None:
                record["remote_size"] = remote_size
            modtime = str(metadata.get("ModTime") or "")
            if modtime:
                record["remote_modtime"] = modtime
            normalized_hashes = _normalized_hashes(metadata.get("Hashes"))
            if normalized_hashes:
                record["remote_hashes"] = normalized_hashes
            mimetype = str(metadata.get("MimeType") or "")
            if mimetype:
                record["remote_mimetype"] = mimetype
            if save:
                self._save_offline_manifest()

    def _remote_metadata_matches_record(self, record: dict[str, object], metadata: dict[str, object]) -> bool:
        recorded_hashes = _normalized_hashes(record.get("remote_hashes"))
        current_hashes = _normalized_hashes(metadata.get("Hashes"))
        shared = set(recorded_hashes).intersection(current_hashes)
        if shared:
            return all(recorded_hashes[key] == current_hashes[key] for key in shared)
        recorded_size = _optional_int(record.get("remote_size"))
        current_size = _optional_int(metadata.get("Size"))
        recorded_modtime = str(record.get("remote_modtime") or "")
        current_modtime = str(metadata.get("ModTime") or "")
        if recorded_size is not None and current_size is not None and recorded_modtime and current_modtime:
            return recorded_size == current_size and recorded_modtime == current_modtime
        return False

    def _record_has_remote_metadata(self, record: dict[str, object]) -> bool:
        if _normalized_hashes(record.get("remote_hashes")):
            return True
        return _optional_int(record.get("remote_size")) is not None and bool(str(record.get("remote_modtime") or ""))

    def _clear_record_remote_metadata(self, remote_name: str, path: str) -> None:
        with self._offline_lock:
            record = self._offline_records.get(remote_name, {}).get(normalize_browser_path(path))
            if record is None:
                return
            record.pop("remote_size", None)
            record.pop("remote_modtime", None)
            record.pop("remote_hashes", None)
            record.pop("remote_mimetype", None)
            self._save_offline_manifest()

    def _remove_offline_records(self, remote_name: str, path: str) -> None:
        with self._offline_lock:
            records = self._offline_records.get(remote_name)
            if not records:
                return
            normalized = normalize_browser_path(path)
            prefix = f"{normalized}/"
            for record_path in list(records):
                if record_path == normalized or record_path.startswith(prefix):
                    records.pop(record_path, None)
            for ancestor in _ancestor_paths(normalized):
                if ancestor in records:
                    records[ancestor]["complete"] = False
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

    def _remove_offline_tree(self, remote_name: str) -> int:
        with self._offline_lock:
            records = self._offline_records.get(remote_name)
            removed = sum(1 for record in (records or {}).values() if not bool(record.get("is_dir")))
            root = self.cache_root / _safe_component(remote_name)
            if root.exists():
                self._make_tree_writable(root)
                shutil.rmtree(root)
            self._offline_records.pop(remote_name, None)
            self._remove_empty_parents(root, self.cache_root)
            self._save_offline_manifest()
            return removed

    def _rclone(self) -> str:
        binary = core.find_rclone()
        if not binary:
            raise RuntimeError("rclone was not found")
        return binary

    def _command(self, binary: str, *arguments: str) -> list[str]:
        return [binary, "--config", core.CONFIG_PATH, *arguments]

    def _run_operation(self, binary: str, *arguments: str, timeout: int | None = RCLONE_FILE_OPERATION_TIMEOUT_SECONDS) -> None:
        command = self._command(binary, *arguments)
        if self.operation_output_callback is not None and timeout is None:
            self._run_operation_streaming(command)
            return
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                **core.PLATFORM.command_process_options(),
            )
        except subprocess.TimeoutExpired as exc:
            operation = arguments[0] if arguments else "operation"
            raise RuntimeError(f"rclone {operation} timed out after {timeout} seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")

    def _run_operation_streaming(self, command: list[str]) -> None:
        callback = self.operation_output_callback
        if callback is None:
            return
        operation = command[3] if len(command) > 3 else "operation"
        if operation in {"copy", "copyto", "move", "moveto", "sync"} and not any(
            argument in {"--progress", "-P"} for argument in command
        ):
            command = [*command, "--progress"]
        callback(f"$ {' '.join(command)}\n")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **core.PLATFORM.command_process_options(),
            )
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        output: list[str] = []
        buffer: list[str] = []
        assert process.stdout is not None
        while True:
            char = process.stdout.read(1)
            if char == "":
                if process.poll() is not None:
                    break
                continue
            if char in {"\n", "\r"}:
                if buffer:
                    line = "".join(buffer)
                    output.append(line)
                    callback(f"{line}\n")
                    buffer = []
                continue
            buffer.append(char)
        if buffer:
            line = "".join(buffer)
            output.append(line)
            callback(f"{line}\n")
        returncode = process.wait()
        callback(f"[rclone exited with code {returncode}]\n")
        if returncode != 0:
            detail = "\n".join(output[-20:]).strip()
            raise RuntimeError(detail or f"rclone exited with code {returncode}")

    def _remote_file_metadata(
        self,
        binary: str,
        remote: core.RemoteInfo,
        path: str,
        *,
        timeout: int = RCLONE_METADATA_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        try:
            result = subprocess.run(
                self._remote_metadata_command(binary, remote, path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                **core.PLATFORM.command_process_options(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"rclone metadata check timed out after {timeout} seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"rclone exited with code {result.returncode}")
        try:
            metadata = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("rclone returned invalid file metadata") from exc
        if not isinstance(metadata, dict) or bool(metadata.get("IsDir")):
            raise RuntimeError("rclone did not return file metadata")
        return metadata

    def _download_remote_file(
        self,
        binary: str,
        remote: core.RemoteInfo,
        path: str,
        destination: Path,
        *,
        timeout: int | None = RCLONE_FILE_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if timeout == RCLONE_FILE_OPERATION_TIMEOUT_SECONDS:
            self._run_operation(binary, "copyto", remote_target(remote, path), str(destination))
        else:
            self._run_operation(binary, "copyto", remote_target(remote, path), str(destination), timeout=timeout)
        self._make_file_writable(destination)

    def _upload_remote_file(
        self,
        binary: str,
        remote: core.RemoteInfo,
        path: str,
        source: Path,
        *,
        timeout: int | None = RCLONE_FILE_OPERATION_TIMEOUT_SECONDS,
        remote_metadata: dict[str, object] | None = None,
    ) -> bool:
        import_flags = self._drive_document_import_flags(remote, source, remote_metadata)
        try:
            if timeout == RCLONE_FILE_OPERATION_TIMEOUT_SECONDS:
                self._run_operation(binary, "copyto", str(source), remote_target(remote, path), *import_flags)
            else:
                self._run_operation(binary, "copyto", str(source), remote_target(remote, path), *import_flags, timeout=timeout)
        except RuntimeError as exc:
            if import_flags or not self._drive_document_upload_needs_import_flags(remote, source, exc):
                raise
            import_flags = self._drive_document_import_flags(remote, source, {"MimeType": GOOGLE_APPS_MIME_PREFIX})
            if timeout == RCLONE_FILE_OPERATION_TIMEOUT_SECONDS:
                self._run_operation(binary, "copyto", str(source), remote_target(remote, path), *import_flags)
            else:
                self._run_operation(binary, "copyto", str(source), remote_target(remote, path), *import_flags, timeout=timeout)
        return bool(import_flags)

    def _remote_metadata_command(self, binary: str, remote: core.RemoteInfo, path: str) -> list[str]:
        arguments = ["lsjson", remote_target(remote, path), "--stat", "--hash"]
        if remote.backend_type.casefold() != "drive":
            arguments.append("--no-mimetype")
        return self._command(binary, *arguments)

    def _drive_document_import_flags(
        self,
        remote: core.RemoteInfo,
        source: Path,
        remote_metadata: dict[str, object] | None,
    ) -> tuple[str, ...]:
        if remote.backend_type.casefold() != "drive":
            return ()
        suffix = source.suffix.lower().lstrip(".")
        if suffix not in DRIVE_EDITABLE_DOCUMENT_FORMATS:
            return ()
        if not self._metadata_is_google_document(remote_metadata):
            return ()
        return (
            "--drive-export-formats",
            DRIVE_EDITABLE_DOCUMENT_FORMATS_VALUE,
            "--drive-import-formats",
            DRIVE_EDITABLE_DOCUMENT_FORMATS_VALUE,
        )

    def _metadata_is_google_document(self, metadata: dict[str, object] | None) -> bool:
        if metadata is None:
            return False
        return str(metadata.get("MimeType") or "").startswith(GOOGLE_APPS_MIME_PREFIX)

    def _drive_document_upload_needs_import_flags(
        self,
        remote: core.RemoteInfo,
        source: Path,
        error: RuntimeError,
    ) -> bool:
        if remote.backend_type.casefold() != "drive":
            return False
        suffix = source.suffix.lower().lstrip(".")
        if suffix not in DRIVE_EDITABLE_DOCUMENT_FORMATS:
            return False
        return GOOGLE_DOC_IMPORT_REQUIRED_MESSAGE in str(error)

    def _free_cache_records(self, remote_name: str, path: str) -> int:
        with self._offline_lock:
            records = self._offline_records.get(remote_name)
            if not records:
                return 0
            normalized = normalize_browser_path(path)
            prefix = f"{normalized}/" if normalized else ""
            candidates = [
                record_path
                for record_path, record in records.items()
                if not bool(record.get("protected"))
                and not bool(record.get("is_dir"))
                and (record_path == normalized or not normalized or record_path.startswith(prefix))
                and not self.offline_changed(remote_name, record_path)
            ]
            removed = 0
            for record_path in candidates:
                destination = self.offline_path(remote_name, record_path)
                self._make_file_writable(destination)
                with suppress(OSError):
                    destination.unlink()
                    removed += 1
                records.pop(record_path, None)
            self._prune_unprotected_empty_directories(remote_name)
            self._save_offline_manifest()
            return removed

    def _prune_unprotected_empty_directories(self, remote_name: str) -> None:
        records = self._offline_records.get(remote_name)
        if not records:
            return
        for record_path, record in sorted(list(records.items()), key=lambda item: item[0].count("/"), reverse=True):
            if not bool(record.get("is_dir")) or bool(record.get("protected")):
                continue
            if any(parent_browser_path(candidate) == record_path for candidate in records if candidate != record_path):
                continue
            directory = self.offline_path(remote_name, record_path)
            with suppress(OSError):
                directory.rmdir()
            records.pop(record_path, None)
        if not records:
            self._offline_records.pop(remote_name, None)

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


def _normalized_hashes(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(candidate)
        for key, candidate in value.items()
        if str(key).strip() and str(candidate).strip()
    }


def _short_digest(value: str) -> str:
    return value[:12] if value else ""


def _diagnostic_append(diagnostics: list[str] | None, message: str) -> None:
    if diagnostics is not None:
        diagnostics.append(message)


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


def _conflict_copy_remote_path(path: str) -> str:
    normalized = normalize_browser_path(path)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    parsed = PurePosixPath(normalized)
    name = parsed.name
    suffix = "".join(parsed.suffixes[-1:])
    stem = name[: -len(suffix)] if suffix else name
    copy_name = f"{stem} (Mountlet offline {timestamp}){suffix}"
    parent = parent_browser_path(normalized)
    return join_browser_path(parent, copy_name)


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
    "OfflineContentState",
    "TransferItem",
    "format_file_size",
    "join_browser_path",
    "normalize_browser_path",
    "parent_browser_path",
    "remote_target",
]
