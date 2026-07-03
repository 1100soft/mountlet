#!/usr/bin/env python3

"""Portable single-file Mountlet configuration bundles."""

from __future__ import annotations

import configparser
import hashlib
import io
import json
import os
import platform
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .shared import (
    app_config_file,
    app_mounts_file,
    apply_permissions,
    default_config_path,
    ensure_dir,
    find_client_secrets,
    timestamp,
)
from ..settings import AppSettings, load_app_settings, load_mount_settings, save_app_settings

BUNDLE_EXTENSION = ".mountlet"
BUNDLE_VERSION = 1
MANIFEST_NAME = "manifest.json"
PAYLOAD_NAME = "payload.bin"
RCLONE_CONFIG_NAME = "rclone.conf"
APP_CONFIG_NAME = "config.toml"
MOUNTS_CONFIG_NAME = "mounts.toml"
SECRET_PREFIX = "secrets/"
BACKUP_DIR_NAME = "backups"
KDF_ITERATIONS = 390_000
SALT_BYTES = 16
NONCE_BYTES = 12
AAD = b"mountlet-config-bundle-v1"
# rclone refreshes OAuth tokens during normal use; that should not mark the
# user's operation-level config as changed.
VOLATILE_RCLONE_KEYS = {"token"}


class BundlePasswordRequired(ValueError):
    """Raised when an encrypted bundle is imported without a password."""


class BundlePasswordInvalid(ValueError):
    """Raised when an encrypted bundle password cannot decrypt the payload."""


def default_export_path() -> Path:
    return Path.home() / f"mountlet-config{BUNDLE_EXTENSION}"


def backup_dir() -> Path:
    return app_config_file().parent / BACKUP_DIR_NAME


def current_config_fingerprint(source_conf: Path | None = None) -> str:
    return _operation_config_hash(source_conf or default_config_path())


def bundle_metadata(source: Path, *, password: str | None = None) -> dict[str, object]:
    source = Path(source).expanduser()
    with zipfile.ZipFile(source) as archive:
        manifest = _validate_archive(archive)
        if not manifest.get("encrypted") or password is None:
            return dict(manifest)
        payload = _decrypt_payload(archive, manifest, password)
    with zipfile.ZipFile(io.BytesIO(payload)) as decrypted:
        return dict(_validate_archive(decrypted))


def export_bundle_file(destination: Path, *, overwrite: bool = False, password: str | None = None) -> Path:
    destination = _bundle_path(destination)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    ensure_dir(destination.parent)

    source_conf = default_config_path()
    if not source_conf.exists():
        raise FileNotFoundError(source_conf)

    files = _bundle_sources(source_conf)
    payload_manifest = _manifest(files)
    if password:
        payload = _archive_bytes(files, payload_manifest)
        salt = os.urandom(SALT_BYTES)
        nonce = os.urandom(NONCE_BYTES)
        encrypted = AESGCM(_derive_key(password, salt)).encrypt(nonce, payload, AAD)
        wrapper_manifest = {
            "format": "mountlet-config-bundle",
            "version": BUNDLE_VERSION,
            "encrypted": True,
            "created_at": payload_manifest["created_at"],
            "device": payload_manifest["device"],
            "system": payload_manifest.get("system", ""),
            "system_release": payload_manifest.get("system_release", ""),
            "platform": payload_manifest.get("platform", ""),
            "config_hash": payload_manifest["config_hash"],
            "cipher": "AES-256-GCM",
            "kdf": "PBKDF2-HMAC-SHA256",
            "iterations": KDF_ITERATIONS,
            "salt": salt.hex(),
            "nonce": nonce.hex(),
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(wrapper_manifest, indent=2, sort_keys=True))
            archive.writestr(PAYLOAD_NAME, encrypted)
    else:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_archive(archive, files, payload_manifest)
    apply_permissions(destination)
    return destination


def import_bundle_file(source: Path, *, backup: bool = True, password: str | None = None) -> Path | None:
    source = Path(source).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    with _open_bundle_archive(source, password=password) as archive:
        backup_path = backup_current_config() if backup else None
        _extract_config_file(archive, RCLONE_CONFIG_NAME, default_config_path())
        _merge_app_config_from_archive(archive, app_config_file())
        _extract_config_file(archive, MOUNTS_CONFIG_NAME, app_mounts_file(), required=False)
        for name in archive.namelist():
            if not name.startswith(SECRET_PREFIX) or name.endswith("/"):
                continue
            destination = default_config_path().parent / Path(name).name
            _extract_config_file(archive, name, destination, required=False)
    return backup_path


def is_encrypted_bundle(source: Path) -> bool:
    source = Path(source).expanduser()
    with zipfile.ZipFile(source) as archive:
        manifest = _read_manifest(archive)
    return bool(manifest.get("encrypted"))


def backup_current_config() -> Path | None:
    sources = _bundle_sources(default_config_path())
    if not sources:
        return None
    destination = backup_dir() / f"mountlet-config-backup-{timestamp()}{BUNDLE_EXTENSION}"
    ensure_dir(destination.parent)
    manifest = _manifest(sources, backup=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_archive(archive, sources, manifest)
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


def _manifest(files: dict[str, Path], *, backup: bool = False) -> dict[str, object]:
    manifest: dict[str, object] = {
        "format": "mountlet-config-bundle",
        "version": BUNDLE_VERSION,
        "files": sorted(files),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "device": platform.node() or "unknown device",
        "system": platform.system() or "Unknown",
        "system_release": platform.release() or "",
        "platform": platform.platform() or "",
        "config_hash": _operation_config_hash(),
    }
    if backup:
        manifest["backup"] = True
    return manifest


def _config_hash(files: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for archive_name in sorted(files):
        source = files[archive_name]
        if not source.exists():
            continue
        digest.update(archive_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _operation_config_hash(source_conf: Path | None = None) -> str:
    source_conf = source_conf or default_config_path()
    digest = hashlib.sha256()
    _hash_rclone_config(digest, RCLONE_CONFIG_NAME, source_conf)
    app_settings = load_app_settings()
    _hash_json(digest, APP_CONFIG_NAME, _shared_app_settings_payload(app_settings))
    mount_settings = load_mount_settings()
    _hash_json(
        digest,
        MOUNTS_CONFIG_NAME,
        {
            name: {
                "mount_path": value.mount_path,
                "remote_path": value.remote_path,
                "mount_flags": value.mount_flags,
                "auto_mount": value.auto_mount,
                "enabled": value.enabled,
            }
            for name, value in sorted(mount_settings.items())
        },
    )
    for secret in find_client_secrets(source_conf.parent):
        _hash_file(digest, f"{SECRET_PREFIX}{secret.name}", secret)
    return digest.hexdigest()


def _merge_app_config_from_archive(archive: zipfile.ZipFile, destination: Path) -> None:
    if APP_CONFIG_NAME not in archive.namelist():
        return
    ensure_dir(destination.parent)
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        handle.write(archive.read(APP_CONFIG_NAME))
        temporary = Path(handle.name)
    try:
        incoming = load_app_settings(temporary)
    finally:
        temporary.unlink(missing_ok=True)

    local = load_app_settings(destination)
    merged = AppSettings(
        mount_base=local.mount_base,
        auto_mount=incoming.auto_mount,
        auto_mount_delay=incoming.auto_mount_delay,
        start_at_login=local.start_at_login,
        file_manager=local.file_manager,
        open_folder_behavior=local.open_folder_behavior,
        focus_file_manager=local.focus_file_manager,
        integrated_file_edits=incoming.integrated_file_edits,
        remote_sync_interval_seconds=incoming.remote_sync_interval_seconds,
        config_sync_remote=incoming.config_sync_remote,
        config_sync_path=incoming.config_sync_path,
        shortcuts=dict(incoming.shortcuts),
    )
    save_app_settings(merged, destination)
    apply_permissions(destination)


def _shared_app_settings_payload(settings: AppSettings) -> dict[str, object]:
    return {
        "auto_mount": settings.auto_mount,
        "auto_mount_delay": settings.auto_mount_delay,
        "integrated_file_edits": settings.integrated_file_edits,
        "remote_sync_interval_seconds": settings.remote_sync_interval_seconds,
        "config_sync_remote": settings.config_sync_remote,
        "config_sync_path": settings.config_sync_path,
        "shortcuts": {
            key: list(settings.shortcuts.get(key, ()))
            for key in sorted(settings.shortcuts)
        },
    }


def _hash_file(digest: "hashlib._Hash", archive_name: str, source: Path) -> None:
    if not source.exists():
        return
    digest.update(archive_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(source.read_bytes())
    digest.update(b"\0")


def _hash_rclone_config(digest: "hashlib._Hash", archive_name: str, source: Path) -> None:
    if not source.exists():
        return
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with source.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeDecodeError, configparser.Error):
        _hash_file(digest, archive_name, source)
        return
    payload: dict[str, dict[str, str]] = {}
    for section in sorted(parser.sections(), key=str.casefold):
        values: dict[str, str] = {}
        for key, value in parser.items(section):
            normalized_key = key.strip()
            if normalized_key.casefold() in VOLATILE_RCLONE_KEYS:
                continue
            values[normalized_key] = value.strip()
        payload[section] = values
    _hash_json(digest, archive_name, payload)


def _hash_json(digest: "hashlib._Hash", archive_name: str, value: object) -> None:
    digest.update(archive_name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\0")


def _archive_bytes(files: dict[str, Path], manifest: dict[str, object]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_archive(archive, files, manifest)
    return output.getvalue()


def _write_archive(archive: zipfile.ZipFile, files: dict[str, Path], manifest: dict[str, object]) -> None:
    archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
    for archive_name, source in files.items():
        archive.write(source, archive_name)


def _bundle_path(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.suffix.casefold() != BUNDLE_EXTENSION:
        expanded = expanded.with_suffix(BUNDLE_EXTENSION)
    return expanded.resolve()


def _open_bundle_archive(source: Path, *, password: str | None) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(source)
    try:
        manifest = _validate_archive(archive)
        if not manifest.get("encrypted"):
            return archive
        if not password:
            raise BundlePasswordRequired("This bundle is encrypted. Enter its password to import it.")
        payload = _decrypt_payload(archive, manifest, password)
    except Exception:
        archive.close()
        raise
    archive.close()
    decrypted = zipfile.ZipFile(io.BytesIO(payload))
    _validate_archive(decrypted)
    return decrypted


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    if MANIFEST_NAME not in archive.namelist():
        return {}
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("This bundle has an invalid manifest.") from exc
    if not isinstance(manifest, dict):
        raise ValueError("This bundle has an invalid manifest.")
    return manifest


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, object]:
    names = set(archive.namelist())
    manifest = _read_manifest(archive)
    if not manifest:
        if RCLONE_CONFIG_NAME not in names:
            raise ValueError("This bundle does not contain rclone.conf.")
        return manifest
    if manifest.get("format") != "mountlet-config-bundle":
        raise ValueError("This is not a Mountlet config bundle.")
    if int(manifest.get("version", 0)) > BUNDLE_VERSION:
        raise ValueError("This bundle was created by a newer Mountlet version.")
    if manifest.get("encrypted"):
        if PAYLOAD_NAME not in names:
            raise ValueError("This encrypted bundle does not contain a payload.")
        return manifest
    if RCLONE_CONFIG_NAME not in names:
        raise ValueError("This bundle does not contain rclone.conf.")
    return manifest


def _derive_key(password: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    ).derive(password.encode("utf-8"))


def _decrypt_payload(archive: zipfile.ZipFile, manifest: dict[str, object], password: str) -> bytes:
    try:
        salt = bytes.fromhex(str(manifest["salt"]))
        nonce = bytes.fromhex(str(manifest["nonce"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("This encrypted bundle has invalid encryption metadata.") from exc
    try:
        return AESGCM(_derive_key(password, salt)).decrypt(nonce, archive.read(PAYLOAD_NAME), AAD)
    except InvalidTag as exc:
        raise BundlePasswordInvalid("The bundle password is incorrect.") from exc


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
    "BundlePasswordInvalid",
    "BundlePasswordRequired",
    "backup_current_config",
    "backup_dir",
    "bundle_metadata",
    "current_config_fingerprint",
    "default_export_path",
    "export_bundle_file",
    "import_bundle_file",
    "is_encrypted_bundle",
]
