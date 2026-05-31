#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import core
from .shared import (
    app_cache_dir,
    app_config_dir,
    app_config_file,
    app_state_dir,
    default_config_path,
    ensure_app_directories,
    find_rclone,
    read_remotes,
    verify_remotes,
)


@dataclass
class Readiness:
    ready: bool
    messages: list[str]
    remotes: list[str]


def _status(ok: bool, message: str) -> str:
    return ("OK   " if ok else "TODO ") + message


def _fuse_available() -> bool:
    if platform.system() == "Windows":
        return True
    if shutil.which("fusermount3") or shutil.which("fusermount"):
        return True
    return False


def _print_paths() -> None:
    print("App files:")
    print(f"  Settings: {app_config_file()}")
    print(f"  State:    {app_state_dir()}")
    print(f"  Cache:    {app_cache_dir()}")
    print(f"  Mounts:   {core.BASE_MOUNT_DIR}")
    print(f"  rclone:   {default_config_path()}")


def check_readiness() -> Readiness:
    messages: list[str] = []
    ensure_app_directories()
    core.ensure_base_mount_dir()
    Path(core.BASE_MOUNT_DIR).mkdir(parents=True, exist_ok=True)

    rclone_bin = find_rclone()
    if not rclone_bin:
        messages.append("Install rclone: sudo apt install rclone")

    if not _fuse_available():
        messages.append("Install FUSE: sudo apt install fuse3")

    config_path = default_config_path()
    if not config_path.exists():
        messages.append(f"Create an rclone config: {config_path}")
        remotes: list[str] = []
    else:
        remotes = read_remotes(config_path)
        if not remotes:
            messages.append("Add at least one cloud storage connection to rclone.")

    return Readiness(ready=not messages, messages=messages, remotes=remotes)


def ensure_ready_for_menu() -> bool:
    readiness = check_readiness()
    if readiness.ready:
        return True

    print("Cloud Mount Manager needs a little setup first.")
    print()
    for message in readiness.messages:
        print(f"- {message}")
    print()
    print("Run:")
    print("  cloud-mount-manager setup")
    if find_rclone():
        print()
        print("If you still need to connect cloud storage, run:")
        print("  cloud-mount-manager setup --configure-rclone")
    return False


def _run_rclone_config(rclone_bin: str) -> int:
    print()
    print("Opening rclone setup. Follow the prompts to add a cloud storage remote.")
    result = subprocess.run([rclone_bin, "config"])
    return result.returncode


def _next_steps(rclone_bin: str | None, fuse_ok: bool, remotes: list[str], failures: list[str]) -> list[str]:
    steps: list[str] = []
    if not rclone_bin:
        steps.append("Install rclone: sudo apt install rclone")
    if not fuse_ok:
        steps.append("Install FUSE: sudo apt install fuse3")
    if not remotes:
        if rclone_bin:
            steps.append("Add cloud storage: cloud-mount-manager setup --configure-rclone")
        else:
            steps.append("After installing rclone, add cloud storage: cloud-mount-manager setup --configure-rclone")
    if failures:
        steps.append("Reconnect credentials: cloud-mount-manager reconnect --remote <name>")
    return steps


def setup_command(args: argparse.Namespace) -> int:
    print("Cloud Mount Manager setup")
    print()

    dirs = ensure_app_directories()
    core.ensure_base_mount_dir()
    mount_dir = Path(core.BASE_MOUNT_DIR)
    mount_dir.mkdir(parents=True, exist_ok=True)

    print(_status(True, "Created user app folders."))
    for path in dirs.values():
        print(f"     {path}")
    print(_status(mount_dir.exists(), f"Prepared mount folder: {mount_dir}"))

    rclone_bin = find_rclone()
    print(_status(bool(rclone_bin), f"Found rclone: {rclone_bin}" if rclone_bin else "Install rclone. On Ubuntu: sudo apt install rclone"))

    fuse_ok = _fuse_available()
    print(_status(fuse_ok, "Found FUSE mount support." if fuse_ok else "Install FUSE. On Ubuntu: sudo apt install fuse3"))

    config_path = default_config_path()
    config_exists = config_path.exists()
    remotes = read_remotes(config_path) if config_exists else []

    if not config_exists:
        print(_status(False, f"No rclone config found at {config_path}"))
    elif remotes:
        print(_status(True, f"Found {len(remotes)} rclone remote(s)."))
        for remote in remotes:
            print(f"     {remote}")
    else:
        print(_status(False, f"No remotes found in {config_path}"))

    if rclone_bin and args.configure_rclone and not remotes:
        if _run_rclone_config(rclone_bin) != 0:
            print("[!] rclone setup did not finish cleanly.")
            return 1
        remotes = read_remotes(config_path)
        if remotes:
            print(_status(True, f"Found {len(remotes)} rclone remote(s) after setup."))
        else:
            print(_status(False, "No remotes were added."))

    if remotes and not args.skip_verify:
        print()
        print("Checking cloud access...")
        results = verify_remotes(remotes)
        failures = [name for name, (ok, _) in results.items() if not ok]
    else:
        failures = []

    print()
    _print_paths()
    print()

    ready = bool(rclone_bin and fuse_ok and remotes and not failures)
    if ready:
        print("Ready. Open the menu with:")
        print("  cloud-mount-manager")
        return 0

    print("A few things still need attention before mounting.")
    for number, step in enumerate(_next_steps(rclone_bin, fuse_ok, remotes, failures), start=1):
        print(f"  {number}. {step}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Cloud Mount Manager for first use.")
    parser.add_argument(
        "--configure-rclone",
        action="store_true",
        help="Open rclone's setup flow if no remotes are configured.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Do not check cloud access during setup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return setup_command(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
