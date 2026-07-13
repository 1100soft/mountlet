from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import core
from .cloud_browser import BrowserEntry, normalize_browser_path, parent_browser_path
from .config_tools.shared import app_state_dir, ensure_app_directories


INDEX_FILE = "metadata-index.sqlite3"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IndexedEntry:
    remote_name: str
    remote_display: str
    provider: str
    backend_type: str
    name: str
    path: str
    parent_path: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    updated_at: float = 0.0


class MetadataIndex:
    def __init__(self, path: Path | None = None) -> None:
        ensure_app_directories()
        self.path = path or app_state_dir() / INDEX_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    remote_name TEXT NOT NULL,
                    remote_display TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    backend_type TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    parent_path TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    name_folded TEXT NOT NULL,
                    is_dir INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0,
                    modified TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (remote_name, path)
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(remote_name, parent_path)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name_folded)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_entries_path ON entries(path)")
            connection.commit()

    def upsert_folder(self, remote: core.RemoteInfo, parent_path: str, entries: Iterable[BrowserEntry]) -> None:
        normalized_parent = normalize_browser_path(parent_path)
        now = time.time()
        values = list(entries)
        current_paths = {normalize_browser_path(entry.path) for entry in values}
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM entries WHERE remote_name = ? AND parent_path = ?"
                + (" AND path NOT IN (%s)" % ",".join("?" for _ in current_paths) if current_paths else ""),
                (remote.name, normalized_parent, *sorted(current_paths)),
            )
            connection.executemany(
                """
                INSERT INTO entries (
                    remote_name, remote_display, provider, backend_type, path, parent_path,
                    name, name_folded, is_dir, size, modified, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(remote_name, path) DO UPDATE SET
                    remote_display = excluded.remote_display,
                    provider = excluded.provider,
                    backend_type = excluded.backend_type,
                    parent_path = excluded.parent_path,
                    name = excluded.name,
                    name_folded = excluded.name_folded,
                    is_dir = excluded.is_dir,
                    size = excluded.size,
                    modified = excluded.modified,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        remote.name,
                        remote.display_name,
                        remote.provider,
                        remote.backend_type,
                        normalize_browser_path(entry.path),
                        normalized_parent,
                        entry.name,
                        entry.name.casefold(),
                        1 if entry.is_dir else 0,
                        max(int(entry.size or 0), 0),
                        entry.modified,
                        now,
                    )
                    for entry in values
                ],
            )
            connection.commit()

    def upsert_entries(self, remote: core.RemoteInfo, entries: Iterable[BrowserEntry]) -> None:
        grouped: dict[str, list[BrowserEntry]] = {}
        for entry in entries:
            grouped.setdefault(parent_browser_path(entry.path), []).append(entry)
        for parent_path, children in grouped.items():
            self.upsert_folder(remote, parent_path, children)

    def cached_folder(self, remote_name: str, parent_path: str) -> list[BrowserEntry]:
        normalized_parent = normalize_browser_path(parent_path)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT name, path, is_dir, size, modified
                FROM entries
                WHERE remote_name = ? AND parent_path = ?
                ORDER BY is_dir DESC, name_folded ASC
                """,
                (remote_name, normalized_parent),
            ).fetchall()
        return [
            BrowserEntry(
                name=str(row["name"]),
                path=str(row["path"]),
                is_dir=bool(row["is_dir"]),
                size=max(int(row["size"] or 0), 0),
                modified=str(row["modified"] or ""),
            )
            for row in rows
        ]

    def search(self, query: str, *, remotes: Iterable[core.RemoteInfo] = (), limit: int = 100) -> list[IndexedEntry]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []
        remote_names = [remote.name for remote in remotes]
        clauses = ["name_folded LIKE ?"] + ["(name_folded LIKE ? OR path LIKE ?)" for _ in terms[1:]]
        params: list[object] = [f"%{terms[0]}%"]
        for term in terms[1:]:
            params.extend((f"%{term}%", f"%{term}%"))
        if remote_names:
            clauses.append("remote_name IN (%s)" % ",".join("?" for _ in remote_names))
            params.extend(remote_names)
        params.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT remote_name, remote_display, provider, backend_type, name, path,
                       parent_path, is_dir, size, modified, updated_at
                FROM entries
                WHERE {' AND '.join(clauses)}
                ORDER BY is_dir ASC, name_folded ASC, path ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_indexed_entry_from_row(row) for row in rows]

    def count_entries(self, remote_name: str | None = None) -> int:
        with closing(self._connect()) as connection:
            if remote_name:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM entries WHERE remote_name = ?",
                    (remote_name,),
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS count FROM entries").fetchone()
        return int(row["count"] if row else 0)

    def remove_remote(self, remote_name: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM entries WHERE remote_name = ?", (remote_name,))
            connection.commit()


def _indexed_entry_from_row(row: sqlite3.Row) -> IndexedEntry:
    return IndexedEntry(
        remote_name=str(row["remote_name"]),
        remote_display=str(row["remote_display"] or row["remote_name"]),
        provider=str(row["provider"] or ""),
        backend_type=str(row["backend_type"] or ""),
        name=str(row["name"]),
        path=str(row["path"]),
        parent_path=str(row["parent_path"] or ""),
        is_dir=bool(row["is_dir"]),
        size=max(int(row["size"] or 0), 0),
        modified=str(row["modified"] or ""),
        updated_at=float(row["updated_at"] or 0),
    )

