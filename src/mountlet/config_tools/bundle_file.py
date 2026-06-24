#!/usr/bin/env python3

"""Portable single-file Mountlet configuration bundles."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .shared import (
    app_config_file,
    app_mounts_file,
    apply_permissions,
    default_config_path,
    ensure_dir,
    find_client_secrets,
    timestamp,
)

BUNDLE_EXTENSION = ".mountlet"
BUNDLE_VERSION = 1
MANIFEST_NAME = "manifest.json"
RCLONE_CONFIG_NAME = "rclone.conf"
APP_CONFIG_NAME = "config.toml"
MOUNTS_CONFIG_NAME = "mounts.toml"
SECRET_PREFIX = "secrets/"
BACKUP_DIR_NAME = "backups"


def default_export_path() -> Path:
    return Path.home() / f"mountlet-config{BUNDLE_EXTENSION}"


def backup_dir() -> Path:
    return app_config_file().parent / BACKUP_DIR_NAME


def export_bundle_file(destination: Path, *, overwrite: bool = False) -> Path:
    destination = _bundle_path(destination)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    ensure_dir(destination.parent)

    source_conf = default_config_path()
    if not source_conf.exists():
        raise FileNotFoundError(source_conf)

    files = _bundle_sources(source_conf)
    manifest = {
        "format": "mountlet-config-bundle",
        "version": BUNDLE_VERSION,
        "files": sorted(files),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
        for archive_name, source in files.items():
            archive.write(source, archive_name)
    apply_permissions(destination)
    return destination


def import_bundle_file(source: Path, *, backup: bool = True) -> Path | None:
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    with zipfile.ZipFile(source) as archive:
        _validate_archive(archive)
        backup_path = backup_current_config() if backup else None
        _extract_config_file(archive, RCLONE_CONFIG_NAME, default_config_path())
        _extract_config_file(archive, APP_CONFIG_NAME, app_config_file(), required=False)
        _extract_config_file(archive, MOUNTS_CONFIG_NAME, app_mounts_file(), required=False)
        for name in archive.namelist():
            if not name.startswith(SECRET_PREFIX) or name.endswith("/"):
                continue
            destination = default_config_path().parent / Path(name).name
            _extract_config_file(archive, name, destination, required=False)
    return backup_path


def backup_current_config() -> Path | None:
    sources = _bundle_sources(default_config_path())
    if not sources:
        return None
    destination = backup_dir() / f"mountlet-config-backup-{timestamp()}{BUNDLE_EXTENSION}"
    ensure_dir(destination.parent)
    manifest = {
        "format": "mountlet-config-bundle",
        "version": BUNDLE_VERSION,
        "backup": True,
        "files": sorted(sources),
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
        for archive_name, source in sources.items():
            archive.write(source, archive_name)
    apply_permissions(destination)
    return destination


def _bundle_sources(source_conf: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if source_conf.exists():
        files[RCLONE_CONFIG_NAME] = source_conf
    if app_config_file().exists():
        files[APP_CONFIG_NAME] = app_config_file()
    if app_mounts_file().exists():
        files[MOUNTS_CONFIG_NAME] = app_mounts_file()
    for secret in find_client_secrets(source_conf.parent):
        files[f"{SECRET_PREFIX}{secret.name}"] = secret
    return files


def _bundle_path(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.suffix.casefold() != BUNDLE_EXTENSION:
        expanded = expanded.with_suffix(BUNDLE_EXTENSION)
    return expanded.resolve()


def _validate_archive(archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    if RCLONE_CONFIG_NAME not in names:
        raise ValueError("This bundle does not contain rclone.conf.")
    if MANIFEST_NAME not in names:
        return
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("This bundle has an invalid manifest.") from exc
    if manifest.get("format") != "mountlet-config-bundle":
        raise ValueError("This is not a Mountlet config bundle.")
    if int(manifest.get("version", 0)) > BUNDLE_VERSION:
        raise ValueError("This bundle was created by a newer Mountlet version.")


def _extract_config_file(
    archive: zipfile.ZipFile,
    archive_name: str,
    destination: Path,
    *,
    required: bool = True,
) -> None:
    if archive_name not in archive.namelist():
        if required:
            raise ValueError(f"This bundle does not contain {archive_name}.")
        return
    ensure_dir(destination.parent)
    with archive.open(archive_name) as source:
        destination.write_bytes(source.read())
    apply_permissions(destination)


__all__ = [
    "BUNDLE_EXTENSION",
    "backup_current_config",
    "backup_dir",
    "default_export_path",
    "export_bundle_file",
    "import_bundle_file",
]
