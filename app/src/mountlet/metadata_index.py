from __future__ import annotations

import sqlite3
import time
import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable

from . import core
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
    match_quality: str = ""
    match_score: int = 0


SEARCH_QUALITY_LABELS = {
    "exact": "Exact filename match",
    "phrase": "Phrase match in filename",
    "filename": "All terms match the filename",
    "mixed": "Terms match the filename and parent path",
}


def search_quality_label(quality: str) -> str:
    return SEARCH_QUALITY_LABELS.get(quality, "Search match")


def _search_terms(query: str) -> list[tuple[str, bool]]:
    """Parse ordinary AND terms while preserving text inside double quotes."""
    parsed: list[tuple[str, bool]] = []
    for match in re.finditer(r'"([^"]+)"|([^\s"]+)', query):
        phrase = match.group(1)
        value = phrase if phrase is not None else match.group(2)
        folded = str(value or "").strip().casefold()
        if folded:
            parsed.append((folded, phrase is not None))
    return parsed


def _like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remote_index (
                    remote_name TEXT PRIMARY KEY,
                    remote_display TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    backend_type TEXT NOT NULL DEFAULT '',
                    fully_indexed_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            connection.commit()

    def upsert_folder(self, remote: core.RemoteInfo, parent_path: str, entries: Iterable[Any]) -> None:
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

    def upsert_entries(self, remote: core.RemoteInfo, entries: Iterable[Any]) -> None:
        grouped: dict[str, list[Any]] = {}
        for entry in entries:
            grouped.setdefault(parent_browser_path(entry.path), []).append(entry)
        for parent_path, children in grouped.items():
            self.upsert_folder(remote, parent_path, children)

    def cached_folder(self, remote_name: str, parent_path: str) -> list[IndexedEntry]:
        normalized_parent = normalize_browser_path(parent_path)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT remote_name, remote_display, provider, backend_type, name, path,
                       parent_path, is_dir, size, modified, updated_at
                FROM entries
                WHERE remote_name = ? AND parent_path = ?
                ORDER BY is_dir DESC, name_folded ASC
                """,
                (remote_name, normalized_parent),
            ).fetchall()
        return [_indexed_entry_from_row(row) for row in rows]

    def search(self, query: str, *, remotes: Iterable[core.RemoteInfo] = (), limit: int = 100) -> list[IndexedEntry]:
        terms = _search_terms(query)
        result_limit = max(int(limit), 0)
        if not terms or result_limit <= 0:
            return []
        remote_names = [remote.name for remote in remotes]
        parameters: dict[str, object] = {
            "normalized_query": " ".join(term for term, _is_phrase in terms),
            "limit": result_limit,
        }
        name_matches: list[str] = []
        combined_matches: list[str] = []
        phrase_name_matches: list[str] = []
        for index, (term, is_phrase) in enumerate(terms):
            parameter = f"term_{index}"
            pattern = f"%{_like_literal(term)}%"
            parameters[parameter] = pattern
            name_match = f"name_folded LIKE :{parameter} ESCAPE '\\'"
            name_matches.append(name_match)
            combined_matches.append(
                f"({name_match} OR lower(parent_path) LIKE :{parameter} ESCAPE '\\')"
            )
            if is_phrase:
                phrase_name_matches.append(name_match)

        all_name = " AND ".join(name_matches)
        any_name = " OR ".join(name_matches)
        all_combined = " AND ".join(combined_matches)
        phrase_in_name = " OR ".join(phrase_name_matches) if phrase_name_matches else "0"
        quality_case = (
            "CASE "
            "WHEN name_folded = :normalized_query THEN 'exact' "
            f"WHEN ({all_name}) AND ({phrase_in_name}) THEN 'phrase' "
            f"WHEN ({all_name}) THEN 'filename' "
            "ELSE 'mixed' END"
        )
        score_case = (
            "CASE "
            "WHEN name_folded = :normalized_query THEN 500 "
            f"WHEN ({all_name}) AND ({phrase_in_name}) THEN 450 "
            f"WHEN ({all_name}) THEN 400 "
            "ELSE 250 END"
        )
        clauses = [f"({all_combined})", f"({any_name})"]
        if remote_names:
            remote_parameters: list[str] = []
            for index, remote_name in enumerate(remote_names):
                parameter = f"remote_{index}"
                parameters[parameter] = remote_name
                remote_parameters.append(f":{parameter}")
            clauses.append(f"remote_name IN ({','.join(remote_parameters)})")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT remote_name, remote_display, provider, backend_type, name, path,
                       parent_path, is_dir, size, modified, updated_at,
                       {quality_case} AS match_quality,
                       {score_case} AS match_score
                FROM entries
                WHERE {' AND '.join(clauses)}
                ORDER BY match_score DESC, is_dir ASC, name_folded ASC, path ASC
                LIMIT :limit
                """,
                parameters,
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

    def is_remote_fully_indexed(self, remote_name: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT fully_indexed_at FROM remote_index WHERE remote_name = ?",
                (remote_name,),
            ).fetchone()
        return bool(row and float(row["fully_indexed_at"] or 0) > 0)

    def mark_remote_fully_indexed(self, remote: core.RemoteInfo) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO remote_index (
                    remote_name, remote_display, provider, backend_type, fully_indexed_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(remote_name) DO UPDATE SET
                    remote_display = excluded.remote_display,
                    provider = excluded.provider,
                    backend_type = excluded.backend_type,
                    fully_indexed_at = excluded.fully_indexed_at
                """,
                (remote.name, remote.display_name, remote.provider, remote.backend_type, time.time()),
            )
            connection.commit()

    def remove_remote(self, remote_name: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM entries WHERE remote_name = ?", (remote_name,))
            connection.execute("DELETE FROM remote_index WHERE remote_name = ?", (remote_name,))
            connection.commit()

    def rename_remote(self, old_name: str, new_remote: core.RemoteInfo) -> None:
        if old_name == new_remote.name:
            return
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE entries
                SET remote_name = ?,
                    remote_display = ?,
                    provider = ?,
                    backend_type = ?
                WHERE remote_name = ?
                """,
                (
                    new_remote.name,
                    new_remote.display_name,
                    new_remote.provider,
                    new_remote.backend_type,
                    old_name,
                ),
            )
            connection.execute(
                """
                UPDATE remote_index
                SET remote_name = ?,
                    remote_display = ?,
                    provider = ?,
                    backend_type = ?
                WHERE remote_name = ?
                """,
                (
                    new_remote.name,
                    new_remote.display_name,
                    new_remote.provider,
                    new_remote.backend_type,
                    old_name,
                ),
            )
            connection.commit()

    def rename_path(self, remote_name: str, old_path: str, new_path: str) -> None:
        old = normalize_browser_path(old_path)
        new = normalize_browser_path(new_path)
        if not old or not new or old == new:
            return
        old_prefix = f"{old}/"
        new_prefix = f"{new}/"
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT path FROM entries
                WHERE remote_name = ? AND (path = ? OR substr(path, 1, ?) = ?)
                ORDER BY length(path) ASC
                """,
                (remote_name, old, len(old_prefix), old_prefix),
            ).fetchall()
            connection.execute(
                """
                DELETE FROM entries
                WHERE remote_name = ? AND (path = ? OR substr(path, 1, ?) = ?)
                """,
                (remote_name, new, len(new_prefix), new_prefix),
            )
            for row in rows:
                current = str(row["path"])
                renamed = new if current == old else f"{new}/{current[len(old_prefix):]}"
                connection.execute(
                    """
                    UPDATE entries
                    SET path = ?, parent_path = ?, name = ?, name_folded = ?
                    WHERE remote_name = ? AND path = ?
                    """,
                    (
                        renamed,
                        parent_browser_path(renamed),
                        PurePosixPath(renamed).name,
                        PurePosixPath(renamed).name.casefold(),
                        remote_name,
                        current,
                    ),
                )
            connection.commit()


def _indexed_entry_from_row(row: sqlite3.Row) -> IndexedEntry:
    columns = set(row.keys())
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
        match_quality=str(row["match_quality"] or "") if "match_quality" in columns else "",
        match_score=int(row["match_score"] or 0) if "match_score" in columns else 0,
    )


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


def parent_browser_path(path: str) -> str:
    normalized = normalize_browser_path(path)
    return normalize_browser_path(str(PurePosixPath(normalized).parent)) if normalized else ""
