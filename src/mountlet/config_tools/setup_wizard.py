#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .. import core
from ..platform_services import get_platform
from ..settings import ensure_default_config_files
from .shared import (
    app_cache_dir,
    app_config_file,
    app_mounts_file,
    app_state_dir,
    default_config_path,
    ensure_app_directories,
    ensure_rclone_config,
    find_rclone,
    read_remotes,
    verify_remotes,
)


@dataclass
class Readiness:
    ready: bool
    messages: list[str]
    remotes: list[str]


@dataclass(frozen=True)
class Prerequisite:
    key: str
    label: str
    ready: bool
    detail: str
    help_url: str


def _status(ok: bool, message: str) -> str:
    return ("OK   " if ok else "TODO ") + message


def _fuse_available() -> bool:
    return get_platform().mount_driver_available()


def _install_guidance() -> tuple[str, ...]:
    return get_platform().prerequisite_guidance()


def check_prerequisites() -> tuple[Prerequisite, ...]:
    platform = get_platform()
    guidance = _install_guidance()
    rclone_bin = find_rclone()
    fuse_ok = _fuse_available()
    driver_name = {
        "Windows": "WinFsp",
        "Darwin": "macFUSE",
    }.get(platform.system_name, "FUSE")
    driver_url = {
        "Windows": "https://winfsp.dev/rel/",
        "Darwin": "https://macfuse.github.io/",
    }.get(platform.system_name, "https://rclone.org/install/#installing-on-linux")
    return (
        Prerequisite(
            key="rclone",
            label="rclone",
            ready=bool(rclone_bin),
            detail=f"Found {rclone_bin}" if rclone_bin else guidance[0],
            help_url="https://rclone.org/install/",
        ),
        Prerequisite(
            key="mount_driver",
            label=driver_name,
            ready=fuse_ok,
            detail=f"Found {driver_name} mount support." if fuse_ok else guidance[1],
            help_url=driver_url,
        ),
    )


def _mountlet_command() -> str:
    if shutil.which("mountlet"):
        return "mountlet"
    invoked_as = sys.argv[0]
    platform = get_platform()
    launcher = PureWindowsPath(invoked_as) if platform.system_name == "Windows" else Path(invoked_as)
    if launcher.name.lower() not in {"mountlet", "mountlet.exe"}:
        return "mountlet"
    if platform.system_name == "Windows":
        return f"& '{invoked_as.replace(chr(39), chr(39) * 2)}'"
    return shlex.quote(invoked_as)


def _print_paths() -> None:
    print("App files:")
    print(f"  Settings: {app_config_file()}")
    print(f"  Mount settings: {app_mounts_file()}")
    print(f"  State:    {app_state_dir()}")
    print(f"  Cache:    {app_cache_dir()}")
    print(f"  Mounts:   {core.BASE_MOUNT_DIR}")
    print(f"  rclone:   {default_config_path()}")


def check_readiness() -> Readiness:
    messages: list[str] = []
    ensure_app_directories()
    ensure_default_config_files()
    core.ensure_base_mount_dir()
    Path(core.BASE_MOUNT_DIR).mkdir(parents=True, exist_ok=True)

    prerequisites = check_prerequisites()
    messages.extend(item.detail for item in prerequisites if not item.ready)
    rclone_bin = find_rclone()

    config_path = default_config_path()
    if rclone_bin:
        try:
            ensure_rclone_config(config_path)
        except OSError as exc:
            messages.append(f"Cannot create the rclone config at {config_path}: {exc}")
    remotes = read_remotes(config_path) if config_path.exists() else []

    return Readiness(ready=not messages, messages=messages, remotes=remotes)


def ensure_ready_for_menu() -> bool:
    readiness = check_readiness()
    if readiness.ready:
        return True

    print("Mountlet needs a little setup first.")
    print()
    for message in readiness.messages:
        print(f"- {message}")
    print()
    print("Run:")
    command = _mountlet_command()
    print(f"  {command} setup")
    if find_rclone():
        print()
        print("If you still need to connect cloud storage, run:")
        print(f"  {command} setup --configure-rclone")
    return False


def _run_rclone_config(rclone_bin: str) -> int:
    print()
    print("Opening rclone setup. Follow the prompts to add a cloud storage remote.")
    result = subprocess.run([rclone_bin, "config"])
    return result.returncode


def _next_steps(rclone_bin: str | None, fuse_ok: bool, remotes: list[str], failures: list[str]) -> list[str]:
    steps: list[str] = []
    command = _mountlet_command()
    if not rclone_bin:
        steps.append(_install_guidance()[0])
    if not fuse_ok:
        steps.append(_install_guidance()[1])
    if not remotes:
        if rclone_bin:
            steps.append(f"Add cloud storage: {command} setup --configure-rclone")
        else:
            steps.append(f"After installing rclone, add cloud storage: {command} setup --configure-rclone")
    if failures:
        steps.append(f"Reconnect credentials: {command} reconnect --remote <name>")
    return steps


def setup_command(args: argparse.Namespace) -> int:
    print("Mountlet setup")
    print()

    dirs = ensure_app_directories()
    ensure_default_config_files()
    core.ensure_base_mount_dir()
    mount_dir = Path(core.BASE_MOUNT_DIR)
    mount_dir.mkdir(parents=True, exist_ok=True)

    print(_status(True, "Created user app folders."))
    for path in dirs.values():
        print(f"     {path}")
    print(_status(mount_dir.exists(), f"Prepared mount folder: {mount_dir}"))

    rclone_bin = find_rclone()
    print(
        _status(
            bool(rclone_bin),
            f"Found rclone: {rclone_bin}" if rclone_bin else _install_guidance()[0],
        )
    )

    fuse_ok = _fuse_available()
    print(
        _status(
            fuse_ok,
            "Found filesystem mount support." if fuse_ok else _install_guidance()[1],
        )
    )

    config_path = default_config_path()
    if rclone_bin and not config_path.exists():
        try:
            ensure_rclone_config(config_path)
            print(_status(True, f"Created empty rclone config: {config_path}"))
        except OSError as exc:
            print(_status(False, f"Could not create rclone config: {exc}"))
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

    ready = bool(rclone_bin and fuse_ok and config_exists and not failures)
    if ready:
        if not remotes:
            print("Ready. Add cloud storage with the + button in the tray app, or run:")
            print(f"  {_mountlet_command()} setup --configure-rclone")
        print("Ready. Open the menu with:")
        print(f"  {_mountlet_command()}")
        return 0

    print("A few things still need attention before mounting.")
    for number, step in enumerate(_next_steps(rclone_bin, fuse_ok, remotes, failures), start=1):
        print(f"  {number}. {step}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare Mountlet for first use.")
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
