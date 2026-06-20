#!/usr/bin/env python3

from __future__ import annotations

import configparser
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from ..platform_services import get_platform

APP_NAME = "mountlet"
LEGACY_APP_NAMES = ("cloud-mount-manager",)


def default_config_path() -> Path:
    env_config = os.environ.get("RCLONE_CONFIG")
    if env_config:
        return Path(env_config).expanduser()
    return get_platform().default_rclone_config()


def _platform_user_dir_for(kind: str, app_name: str) -> Path:
    directories = get_platform().user_directories(app_name)
    return getattr(directories, kind)


def _platform_user_dir(kind: str) -> Path:
    return _platform_user_dir_for(kind, APP_NAME)


def app_config_dir() -> Path:
    return _platform_user_dir("config")


def app_state_dir() -> Path:
    return _platform_user_dir("state")


def app_cache_dir() -> Path:
    return _platform_user_dir("cache")


def app_config_file() -> Path:
    return app_config_dir() / "config.toml"


def app_mounts_file() -> Path:
    return app_config_dir() / "mounts.toml"


def legacy_app_config_dirs() -> List[Path]:
    return [_platform_user_dir_for("config", name) for name in LEGACY_APP_NAMES]


def ensure_app_directories() -> Dict[str, Path]:
    paths = {
        "config": app_config_dir(),
        "state": app_state_dir(),
        "cache": app_cache_dir(),
    }
    for path in paths.values():
        ensure_dir(path)
    return paths


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def ensure_rclone_config(path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    ensure_dir(config_path.parent)
    if not config_path.exists():
        config_path.touch(mode=0o600)
    apply_permissions(config_path)
    return config_path


def timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_file(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.{timestamp()}.bak")
    shutil.copy2(path, backup)
    return backup


def apply_permissions(path: Path) -> None:
    get_platform().apply_private_permissions(path)


def copy_file(src: Path, dest: Path, *, backup: bool, dry_run: bool) -> Path | None:
    if not src.exists():
        return None
    if dest.exists() and backup and not dry_run:
        backup_file(dest)
    if dry_run:
        return dest
    shutil.copy2(src, dest)
    apply_permissions(dest)
    return dest


def find_client_secrets(default_dir: Path) -> List[Path]:
    return sorted(Path(default_dir).glob("client_secret*.json"))


_RCLONE_CACHED: str | None = None


def find_rclone() -> str | None:
    global _RCLONE_CACHED
    if _RCLONE_CACHED is not None:
        return _RCLONE_CACHED

    _RCLONE_CACHED = get_platform().find_rclone()
    return _RCLONE_CACHED


def run_rclone_version() -> str:
    binary = find_rclone()
    if not binary:
        return "rclone binary not found. Install rclone or set RCLONE_PATH."
    try:
        output = subprocess.check_output([binary, "version"], stderr=subprocess.STDOUT, text=True)
        return output.strip()
    except subprocess.CalledProcessError as exc:
        return f"rclone version check failed: {exc.output.strip() or exc}"


def read_remotes(config_path: Path) -> List[str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, configparser.Error):
        return []
    return [section for section in parser.sections()]


def print_remote_list(remotes: List[str], source: Path, label: str = "Remotes discovered in") -> None:
    if remotes:
        print(f"[i] {label} {source}:")
        for name in remotes:
            print(f"    - {name}")
    else:
        print(f"[!] No remotes detected in {source}.")


def resolve_remote_selection(config_path: Path, requested: List[str]) -> Tuple[List[str], List[str]]:
    available = read_remotes(config_path)
    if not requested:
        return available, []

    available_set = set(available)
    selected: List[str] = []
    missing: List[str] = []
    for raw in requested:
        name = raw.rstrip(":")
        if name in available_set:
            if name not in selected:
                selected.append(name)
        else:
            missing.append(raw)
    return selected, missing


def verify_remotes(remotes: List[str]) -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}
    if not remotes:
        print("[!] No remotes available for verification.")
        return results

    binary = find_rclone()
    if not binary:
        print("[!] rclone executable not found; cannot verify remotes.")
        for remote in remotes:
            results[remote] = (False, "rclone binary missing")
        return results

    for remote in remotes:
        result = subprocess.run(
            [binary, "about", f"{remote}:"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        detail = result.stderr.strip() or result.stdout.strip()
        success = result.returncode == 0
        summary = (
            detail.splitlines()[0]
            if detail
            else ("credentials accepted" if success else f"exit code {result.returncode}")
        )
        if success:
            print(f"[✓] {remote}: {summary}")
        else:
            print(f"[!] {remote}: verification failed ({summary})")
        results[remote] = (success, summary)
    return results


def reconnect_remotes(remotes: List[str], auto_confirm: bool) -> None:
    if not remotes:
        print("[!] No remotes available for reconnection.")
        return
    binary = find_rclone()
    if not binary:
        print("[!] rclone executable not found; cannot run reconnect.")
        return
    for remote in remotes:
        remote_arg = f"{remote}:"
        print(f"[>] Launching 'rclone config reconnect {remote_arg}' (Ctrl+C to skip)...")
        sys.stdout.flush()
        try:
            cmd = [binary, "config", "reconnect", remote_arg]
            if auto_confirm:
                cmd.append("--auto-confirm")
            result = subprocess.run(cmd, stdin=sys.stdin)
        except KeyboardInterrupt:
            print(f"[-] Reconnect for {remote_arg} skipped by user.")
            continue
        if result.returncode == 0:
            print(f"[✓] {remote}: reconnect completed.")
        else:
            print(f"[!] {remote}: reconnect exited with code {result.returncode}.")
            print(f"    Run manually: {' '.join(cmd)}")


__all__ = [
    "APP_NAME",
    "LEGACY_APP_NAMES",
    "default_config_path",
    "app_config_dir",
    "app_state_dir",
    "app_cache_dir",
    "app_config_file",
    "app_mounts_file",
    "legacy_app_config_dirs",
    "ensure_app_directories",
    "ensure_dir",
    "ensure_rclone_config",
    "copy_file",
    "find_client_secrets",
    "print_remote_list",
    "run_rclone_version",
    "resolve_remote_selection",
    "verify_remotes",
    "reconnect_remotes",
    "find_rclone",
    "read_remotes",
]
