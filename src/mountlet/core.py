#!/usr/bin/env python3

from __future__ import annotations

import configparser
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .settings import load_app_settings, load_mount_settings

_ENV_CONFIG = os.environ.get("RCLONE_CONFIG")
CONFIG_PATH = (
    os.path.expanduser(_ENV_CONFIG)
    if _ENV_CONFIG
    else (
        os.path.expanduser("~/.config/rclone/rclone.conf")
        if platform.system() != "Windows"
        else os.path.expandvars("%APPDATA%\\rclone\\rclone.conf")
    )
)
IS_WINDOWS = platform.system() == "Windows"
DEFAULT_HOME_MOUNT = os.path.expanduser("~/cloud_mounts")

ENV_BASE_VARS = ("MOUNTLET_MOUNT_BASE", "CLOUD_MOUNT_BASE", "GDRIVE_MOUNT_BASE")
PRIMARY_BASE_ENV = ENV_BASE_VARS[0]


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "remote"


def _configured_mount_dir() -> str | None:
    configured = None
    for env in ENV_BASE_VARS:
        val = os.environ.get(env)
        if val:
            configured = os.path.expanduser(val)
            break
    if not configured:
        configured = load_app_settings().mount_base
    return configured


def _mount_dir_candidates() -> List[str]:
    configured = _configured_mount_dir()
    candidates: List[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(
        [
            DEFAULT_HOME_MOUNT,
            os.path.expanduser("~/gdrive"),
            os.path.expanduser("~/GDrive"),
            "/mnt/gdrive",
        ]
    )
    return candidates


def _resolve_base_mount_dir(create: bool = False) -> Tuple[str, str | None]:
    configured = _configured_mount_dir()
    candidates = _mount_dir_candidates()

    for cand in candidates:
        if create:
            try:
                os.makedirs(cand, exist_ok=True)
            except OSError:
                continue
        if os.path.isdir(cand) and os.access(cand, os.W_OK | os.X_OK):
            note = f"[i] Using mount directory {cand}. Set {PRIMARY_BASE_ENV} to override."
            if configured and os.path.abspath(cand) == os.path.abspath(configured):
                note = f"[i] Using configured mount directory {cand}."
            elif cand == DEFAULT_HOME_MOUNT:
                note = f"[i] Using default mount directory {cand}. Set {PRIMARY_BASE_ENV} to override."
            return cand, note

    fallback_user = os.getuid() if hasattr(os, "getuid") else "user"
    fallback = os.path.join(tempfile.gettempdir(), f"mountlet-{fallback_user}")
    if create:
        os.makedirs(fallback, exist_ok=True)
    return (
        fallback,
        f"[!] Using fallback mount directory {fallback}. Set {PRIMARY_BASE_ENV} to choose a different location.",
    )


BASE_MOUNT_DIR, BASE_DIR_NOTE = _resolve_base_mount_dir()


def ensure_base_mount_dir() -> Tuple[str, str | None]:
    global BASE_MOUNT_DIR, BASE_DIR_NOTE
    BASE_MOUNT_DIR, BASE_DIR_NOTE = _resolve_base_mount_dir(create=True)
    return BASE_MOUNT_DIR, BASE_DIR_NOTE


@dataclass
class RemoteInfo:
    name: str
    alias: str
    provider: str
    backend_type: str
    mount_path: str
    flags: List[str] = field(default_factory=list)
    extra_info: Dict[str, str] = field(default_factory=dict)
    auto_mount: bool = False
    remote_path: str = ""

    @property
    def display_name(self) -> str:
        if self.provider:
            return f"{self.alias} ({self.provider})"
        return self.alias


@dataclass(frozen=True)
class StorageUsage:
    text: str
    used: int | None = None
    total: int | None = None

    @property
    def percent(self) -> int | None:
        if not self.total:
            return None
        used = max(self.used or 0, 0)
        return min(round((used / self.total) * 100), 100)


@dataclass(frozen=True)
class DriveOAuthCredentials:
    remote_name: str
    client_id: str
    client_secret: str
    remote_names: Tuple[str, ...] = ()


PIDS: Dict[str, int] = {}
OAUTH_BACKEND_TYPES = {"drive", "dropbox", "onedrive", "box", "pcloud"}
RCLONE_STATUS_TIMEOUT_SECONDS = 20
RCLONE_CONNECT_TIMEOUT_SECONDS = 20


TYPE_FLAG_PRESETS: Dict[str, List[str]] = {
    "drive": [
        "--vfs-cache-mode",
        "full",
        "--vfs-read-ahead",
        "64M",
        "--vfs-cache-max-size",
        "2G",
        "--buffer-size",
        "32M",
        "--dir-cache-time",
        "72h",
        "--poll-interval",
        "30s",
        "--attr-timeout",
        "1s",
        "--no-modtime",
        "--vfs-fast-fingerprint",
    ],
    "dropbox": [
        "--vfs-cache-mode",
        "full",
        "--buffer-size",
        "16M",
    ],
    "s3": [
        "--vfs-cache-mode",
        "full",
        "--buffer-size",
        "32M",
        "--attr-timeout",
        "1s",
    ],
    "webdav": [
        "--vfs-cache-mode",
        "full",
        "--buffer-size",
        "16M",
    ],
}

DEFAULT_FLAGS = ["--vfs-cache-mode", "full"]
COMMON_SAFE_RCLONE_KEYS = ("description",)
SAFE_RCLONE_CONFIG_KEYS: Dict[str, Tuple[str, ...]] = {
    "drive": ("shared_with_me", "root_folder_id", "team_drive", "scope"),
    "onedrive": ("drive_type", "region", "drive_id"),
    "webdav": ("url", "vendor"),
    "s3": ("provider", "region", "endpoint", "env_auth", "storage_class", "acl"),
}
S3_PROVIDER_DISPLAY_NAMES = {
    "cloudflare": "Cloudflare R2",
    "minio": "MinIO",
    "aws": "Amazon S3",
    "wasabi": "Wasabi",
    "other": "S3",
}


def _parse_remote_name(name: str, backend_type: str) -> Tuple[str, str]:
    alias = name
    provider = backend_type or "misc"

    if "@" in name:
        alias, provider = name.split("@", 1)
    elif "__" in name:
        alias, provider = name.rsplit("__", 1)
    else:
        return alias.strip() or name, provider

    alias = alias.strip() or name
    provider = provider.strip() or backend_type or "misc"
    return alias, provider


def _build_mount_path(provider: str, alias: str) -> str:
    provider_component = _slugify(provider.lower())
    alias_component = _slugify(alias)
    return os.path.join(BASE_MOUNT_DIR, provider_component, alias_component)


def _resolve_configured_mount_path(path: str) -> str:
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(BASE_MOUNT_DIR, expanded)


def _cleanup_mount_dir(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        return

    base_abs = os.path.abspath(BASE_MOUNT_DIR)
    parent = os.path.abspath(os.path.dirname(path))
    while os.path.commonpath([base_abs, parent]) == base_abs and parent != base_abs:
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.abspath(os.path.dirname(parent))


def _load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding="utf-8")
    return config


def _save_config(config: configparser.ConfigParser) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        config.write(handle)


def _safe_rclone_keys(backend_type: str) -> Tuple[str, ...]:
    return SAFE_RCLONE_CONFIG_KEYS.get(backend_type.lower(), ()) + COMMON_SAFE_RCLONE_KEYS


def editable_rclone_fields(remote: RemoteInfo) -> Dict[str, str]:
    config = _load_config()
    if not config.has_section(remote.name):
        return {}
    section = config[remote.name]
    fields: Dict[str, str] = {}
    for key in _safe_rclone_keys(remote.backend_type):
        if key in section or key in SAFE_RCLONE_CONFIG_KEYS.get(remote.backend_type.lower(), ()):
            fields[key] = section.get(key, "")
    return fields


def save_rclone_fields(remote_name: str, updates: Dict[str, str]) -> None:
    config = _load_config()
    if not config.has_section(remote_name):
        return
    backend_type = config[remote_name].get("type", "").lower()
    allowed = set(_safe_rclone_keys(backend_type))
    for key, value in updates.items():
        if key not in allowed:
            continue
        text = value.strip()
        if text:
            config[remote_name][key] = text
        else:
            config.remove_option(remote_name, key)
    _save_config(config)


def drive_oauth_credentials() -> List[DriveOAuthCredentials]:
    config = _load_config()
    groups: Dict[Tuple[str, str], List[str]] = {}
    for remote_name in config.sections():
        section = config[remote_name]
        if section.get("type", "").lower() != "drive":
            continue
        client_id = section.get("client_id", "").strip()
        client_secret = section.get("client_secret", "").strip()
        if client_id and client_secret:
            groups.setdefault((client_id, client_secret), []).append(remote_name)
    credentials: List[DriveOAuthCredentials] = []
    for (client_id, client_secret), remote_names in groups.items():
        names = tuple(remote_names)
        credentials.append(
            DriveOAuthCredentials(
                remote_name=_drive_credential_group_label(names),
                client_id=client_id,
                client_secret=client_secret,
                remote_names=names,
            )
        )
    return credentials


def _drive_credential_group_label(remote_names: Tuple[str, ...]) -> str:
    if not remote_names:
        return "existing remote"
    if len(remote_names) == 1:
        return remote_names[0]
    return f"{remote_names[0]}, +{len(remote_names) - 1}"


def delete_rclone_remote(remote_name: str) -> bool:
    config = _load_config()
    if not config.remove_section(remote_name):
        return False
    _save_config(config)
    return True


def _build_flags(backend_type: str, extra_flags: List[str]) -> List[str]:
    flags = list(TYPE_FLAG_PRESETS.get(backend_type, DEFAULT_FLAGS))
    if backend_type == "drive" and "--links" not in flags:
        flags.append("--links")
    flags.extend(extra_flags)
    return flags


def _s3_provider_display_name(provider: str, fallback: str = "S3") -> str:
    normalized = provider.strip().lower()
    return S3_PROVIDER_DISPLAY_NAMES.get(normalized, provider.strip() or fallback)


def load_remotes(*, include_incomplete: bool = True) -> List[RemoteInfo]:
    config = _load_config()
    app_settings = load_app_settings()
    mount_settings = load_mount_settings()
    remotes: List[Tuple[int | None, int, RemoteInfo]] = []
    for config_index, name in enumerate(config.sections()):
        section = config[name]
        remote_settings = mount_settings.get(name)
        if remote_settings and not remote_settings.enabled:
            continue
        backend_type = section.get("type", "").lower()
        if not include_incomplete and not _remote_section_is_configured(backend_type, dict(section.items())):
            continue
        alias, provider = _parse_remote_name(name, backend_type)
        mount_provider = provider
        display_provider = (
            _s3_provider_display_name(section.get("provider", ""), provider)
            if backend_type == "s3"
            else provider
        )
        extra_flags_str = section.get("mount_flags", "").strip()
        extra_flags = shlex.split(extra_flags_str) if extra_flags_str else []
        if remote_settings:
            extra_flags.extend(remote_settings.mount_flags)
        mount_path = (
            _resolve_configured_mount_path(remote_settings.mount_path)
            if remote_settings and remote_settings.mount_path
            else _build_mount_path(mount_provider, alias)
        )
        auto_mount = (
            remote_settings.auto_mount
            if remote_settings and remote_settings.auto_mount is not None
            else app_settings.auto_mount
        )
        info = RemoteInfo(
            name=name,
            alias=alias,
            provider=display_provider,
            backend_type=backend_type,
            mount_path=mount_path,
            remote_path=remote_settings.remote_path if remote_settings and remote_settings.remote_path else "",
            flags=_build_flags(backend_type, extra_flags),
            extra_info=dict(section.items()),
            auto_mount=auto_mount,
        )
        order = remote_settings.order if remote_settings else None
        remotes.append((order, config_index, info))
    if any(order is not None for order, _config_index, _info in remotes):
        remotes.sort(
            key=lambda item: (
                item[0] is None,
                item[0] if item[0] is not None else item[1],
                item[1],
            )
        )
    return [info for _order, _config_index, info in remotes]


def _remote_section_is_configured(backend_type: str, values: Dict[str, str]) -> bool:
    if backend_type not in OAUTH_BACKEND_TYPES:
        if backend_type == "s3":
            provider = values.get("provider", "").strip()
            env_auth = values.get("env_auth", "").strip().lower() in {"true", "1", "yes", "on"}
            has_keys = bool(values.get("access_key_id", "").strip() and values.get("secret_access_key", "").strip())
            if not provider or not (env_auth or has_keys):
                return False
            if provider.lower() != "aws" and not values.get("endpoint", "").strip():
                return False
            return True
        if backend_type == "webdav":
            return values.get("url", "").strip().startswith(("http://", "https://"))
        return bool(backend_type)
    if backend_type == "onedrive":
        return bool(values.get("token") and values.get("drive_id") and values.get("drive_type"))
    return bool(values.get("token") or values.get("service_account_file"))


def find_rclone() -> str | None:
    env_path = os.environ.get("RCLONE_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    which = shutil.which("rclone.exe" if IS_WINDOWS else "rclone")
    if which:
        return which
    if IS_WINDOWS:
        candidate = r"C:\\Program Files\\rclone\\rclone.exe"
        if os.path.exists(candidate):
            return candidate
    return None


def mount_path(remote: RemoteInfo) -> str:
    return remote.mount_path


def remote_source(remote: RemoteInfo) -> str:
    path = remote.remote_path.strip().lstrip("/")
    return f"{remote.name}:{path}" if path else f"{remote.name}:"


def is_mounted_windows(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return True


def is_mounted(remote: RemoteInfo) -> bool:
    path = mount_path(remote)
    if IS_WINDOWS:
        return is_mounted_windows(path)
    return os.path.ismount(path)


def wait_for(remote: RemoteInfo, want_mounted: bool, timeout: float = 5.0, interval: float = 0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_mounted(remote) == want_mounted:
            return True
        time.sleep(interval)
    return False


def _launch_mount_process(remote: RemoteInfo, args: List[str], wait_timeout: float = 10.0) -> Tuple[bool, str]:
    popen_kwargs: Dict[str, int] = {}
    if IS_WINDOWS:
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        WIN_FLAGS = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        popen_kwargs["creationflags"] = WIN_FLAGS
    else:
        popen_kwargs["close_fds"] = True

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **popen_kwargs,
        )
    except Exception as exc:
        return False, f"[!] Failed to mount {remote.name}: {exc}"

    PIDS[remote.name] = proc.pid

    if wait_for(remote, True, timeout=wait_timeout):
        return True, f"[*] mounted {remote.name} at {remote.mount_path} (pid {proc.pid})."

    exit_code = proc.poll()
    if exit_code is None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        exit_code = proc.poll()

    PIDS.pop(remote.name, None)

    if exit_code is None:
        return False, f"[!] Timed out waiting for {remote.name} to mount at {remote.mount_path}."

    return False, f"[!] rclone exited with code {exit_code} while mounting {remote.name}."


def _ensure_mount_dir(path: str) -> Tuple[bool, str | None]:
    ensure_base_mount_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as exc:
        return False, f"[!] Cannot create mount dir {path}: {exc}"
    if not os.access(path, os.W_OK | os.X_OK):
        return False, f"[!] Mount dir {path} is not writable."
    already_mounted = is_mounted_windows(path) if IS_WINDOWS else os.path.ismount(path)
    if not already_mounted:
        try:
            if os.listdir(path):
                return False, (
                    f"[!] Mount dir {path} is not empty. Choose an empty folder or move the existing files first."
                )
        except OSError as exc:
            return False, f"[!] Cannot inspect mount dir {path}: {exc}"
    return True, None


def mount_remote(remote: RemoteInfo) -> Tuple[bool, str]:
    rclone_bin = find_rclone()
    if not rclone_bin:
        return False, "[!] rclone not found. Set RCLONE_PATH or add rclone to PATH."

    ok, err = _ensure_mount_dir(mount_path(remote))
    if not ok:
        return False, err or "[!] Unable to prepare mount directory."

    args = [rclone_bin, "mount", remote_source(remote), remote.mount_path]
    args.extend(remote.flags)

    success, message = _launch_mount_process(remote, args)
    if not success:
        return success, message

    connected, connection_message = check_remote_connection(remote, rclone_bin)
    if connected:
        return success, message

    unmounted, unmount_message = unmount_remote(remote)
    if not unmounted:
        return False, f"{connection_message}\n{unmount_message}"
    return False, connection_message


def check_remote_connection(remote: RemoteInfo, rclone_bin: str | None = None) -> Tuple[bool, str]:
    binary = rclone_bin or find_rclone()
    if not binary:
        return False, "[!] rclone not found. Set RCLONE_PATH or add rclone to PATH."
    source = remote_source(remote)
    try:
        result = subprocess.run(
            [binary, "lsf", source, "--max-depth", "1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=RCLONE_CONNECT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"[!] {remote.display_name} did not respond while checking {source}."
    except Exception as exc:
        return False, f"[!] Failed to check {remote.display_name}: {exc}"
    if result.returncode == 0:
        return True, f"[*] connected {remote.display_name}."
    detail = result.stderr.strip()
    summary = detail.splitlines()[0] if detail else f"exit code {result.returncode}"
    return False, f"[!] {remote.display_name} is not connected to {source}: {summary}"


def unmount_remote(remote: RemoteInfo) -> Tuple[bool, str]:
    path = mount_path(remote)
    if IS_WINDOWS:
        pid = PIDS.get(remote.name)
        if pid:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            PIDS.pop(remote.name, None)
        if os.path.exists(path):
            subprocess.run(
                ["cmd", "/c", "rmdir", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True, f"[*] unmounted {remote.name}."

    pid = PIDS.get(remote.name)
    unmount_cmd = shutil.which("fusermount3") or shutil.which("fusermount") or shutil.which("umount")
    if not unmount_cmd:
        return False, "[!] No unmount command found. Install fuse3/fuse or unmount manually."
    unmount_name = os.path.basename(unmount_cmd)
    commands = (
        [[unmount_cmd, "-u", path], [unmount_cmd, "-uz", path]]
        if unmount_name != "umount"
        else [[unmount_cmd, path], [unmount_cmd, "-l", path]]
    )
    last_result = None
    for args in commands:
        last_result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if last_result.returncode == 0:
            break
    if last_result is None or last_result.returncode != 0:
        return False, f"[!] Failed to unmount {remote.name} from {path}. Close files or folders using it and try again."
    if pid:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
        PIDS.pop(remote.name, None)
    _cleanup_mount_dir(path)
    return True, f"[*] unmounted {remote.name}."


def refresh_remote(remote: RemoteInfo) -> Tuple[bool, str]:
    if is_mounted(remote):
        unmount_remote(remote)
        wait_for(remote, False)
        time.sleep(0.5)
    return mount_remote(remote)


def mount_all(remotes: Iterable[RemoteInfo]) -> Tuple[List[str], List[str]]:
    mounted: List[str] = []
    failures: List[str] = []
    for remote in remotes:
        if is_mounted(remote):
            continue
        success, message = mount_remote(remote)
        if success:
            mounted.append(remote.name)
        else:
            failures.append(message)
    return mounted, failures


def unmount_all(remotes: Iterable[RemoteInfo]) -> Tuple[List[str], List[str]]:
    unmounted: List[str] = []
    failures: List[str] = []
    for remote in remotes:
        if is_mounted(remote):
            success, message = unmount_remote(remote)
            if success and wait_for(remote, False):
                unmounted.append(remote.name)
            else:
                failures.append(message)
    return unmounted, failures


def get_storage_usage_details(remote: RemoteInfo) -> StorageUsage:
    rclone_bin = find_rclone()
    if not rclone_bin:
        return StorageUsage("?")
    try:
        output = subprocess.check_output(
            [rclone_bin, "about", remote_source(remote), "--json"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=RCLONE_STATUS_TIMEOUT_SECONDS,
        )
        data = json.loads(output)
        used = int(data.get("used", 0))
        total = int(data.get("total", 0))
        used_gb = used / (1024 ** 3)
        total_gb = total / (1024 ** 3) if total else 0
        if total:
            return StorageUsage(f"{used_gb:.1f} / {total_gb:.1f} GB", used=used, total=total)
        return StorageUsage(f"{used_gb:.1f} GB used", used=used)
    except Exception:
        return StorageUsage("?")


def get_storage_usage(remote: RemoteInfo) -> str:
    return get_storage_usage_details(remote).text


def verify_remote(remote: RemoteInfo) -> Tuple[bool, str]:
    rclone_bin = find_rclone()
    if not rclone_bin:
        return False, "[!] rclone not found."
    try:
        result = subprocess.run(
            [rclone_bin, "about", remote_source(remote)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        return False, f"[!] Failed to verify {remote.name}: {exc}"

    detail = result.stderr.strip() or result.stdout.strip()
    summary = (
        detail.splitlines()[0]
        if detail
        else ("OK" if result.returncode == 0 else f"exit code {result.returncode}")
    )
    if result.returncode == 0:
        return True, f"[✓] {remote.name}: {summary}"
    return False, f"[!] {remote.name}: {summary}"


def verify_all(remotes: Iterable[RemoteInfo]) -> List[str]:
    messages: List[str] = []
    for remote in remotes:
        ok, msg = verify_remote(remote)
        messages.append(msg)
    return messages


__all__ = [
    "RemoteInfo",
    "BASE_MOUNT_DIR",
    "BASE_DIR_NOTE",
    "CONFIG_PATH",
    "ensure_base_mount_dir",
    "load_remotes",
    "editable_rclone_fields",
    "save_rclone_fields",
    "mount_remote",
    "check_remote_connection",
    "unmount_remote",
    "refresh_remote",
    "mount_all",
    "unmount_all",
    "is_mounted",
    "get_storage_usage",
    "get_storage_usage_details",
    "verify_remote",
    "verify_all",
    "wait_for",
]
