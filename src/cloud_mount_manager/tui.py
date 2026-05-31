#!/usr/bin/env python3

from __future__ import annotations

import sys
import time
from typing import Dict, List

from . import core
from .config_tools.shared import ensure_app_directories

USE_COLOR = True
CACHE_USAGE: Dict[str, str] = {}


if core.IS_WINDOWS:
    try:
        import ctypes

        _kernel32 = ctypes.windll.kernel32
        _STDOUT = _kernel32.GetStdHandle(-11)

        def _set_color(code: int) -> None:
            _kernel32.SetConsoleTextAttribute(_STDOUT, code)

        def _reset_color() -> None:
            _set_color(7)

        def print_maybe_color(line_prefix: str, mounted_line: str, normal_line: str, mounted: bool) -> None:
            if USE_COLOR and mounted and sys.stdout.isatty():
                print(line_prefix, end="")
                _set_color(10)
                print(mounted_line, end="")
                _reset_color()
                print()
            else:
                print(line_prefix + (mounted_line if mounted else normal_line))

    except Exception:

        def print_maybe_color(line_prefix: str, mounted_line: str, normal_line: str, mounted: bool) -> None:
            print(line_prefix + (mounted_line if mounted else normal_line))

else:

    def print_maybe_color(line_prefix: str, mounted_line: str, normal_line: str, mounted: bool) -> None:
        if USE_COLOR and sys.stdout.isatty():
            if mounted:
                print(f"{line_prefix}\033[92m{mounted_line}\033[0m")
            else:
                print(line_prefix + normal_line)
        else:
            print(line_prefix + (mounted_line if mounted else normal_line))


def mount_all(remotes: List[core.RemoteInfo]) -> str:
    mounted, failures = core.mount_all(remotes)
    messages: List[str] = []
    if mounted:
        messages.append("[*] mounted: " + ", ".join(mounted))
        for remote in remotes:
            if remote.name in mounted:
                CACHE_USAGE[remote.name] = core.get_storage_usage(remote)
    for failure in failures:
        messages.append(failure)
    return " | ".join(messages) if messages else "[*] nothing to mount."


def refresh_all(remotes: List[core.RemoteInfo]) -> str:
    updated: List[str] = []
    messages: List[str] = []
    for remote in remotes:
        if core.is_mounted(remote):
            success, message = core.refresh_remote(remote)
            messages.append(message)
            if success:
                CACHE_USAGE[remote.name] = core.get_storage_usage(remote)
                updated.append(remote.name)
            else:
                CACHE_USAGE.pop(remote.name, None)
    if not messages:
        return "[*] nothing to refresh."
    summary = "[*] refreshed: " + ", ".join(updated) if updated else "[*] nothing refreshed."
    return "\n".join([summary, *messages])


def unmount_all(remotes: List[core.RemoteInfo]) -> str:
    unmounted, failures = core.unmount_all(remotes)
    for name in unmounted:
        CACHE_USAGE.pop(name, None)
    messages: List[str] = []
    if unmounted:
        messages.append("[*] unmounted: " + ", ".join(unmounted))
    messages.extend(failures)
    if not messages:
        return "[*] nothing to unmount."
    return " | ".join(messages)


def verify_all(remotes: List[core.RemoteInfo]) -> str:
    return "\n".join(core.verify_all(remotes))


def toggle_remote(remote: core.RemoteInfo) -> str:
    if core.is_mounted(remote):
        _, message = core.unmount_remote(remote)
        core.wait_for(remote, False)
        CACHE_USAGE.pop(remote.name, None)
        return message
    success, message = core.mount_remote(remote)
    if success:
        CACHE_USAGE[remote.name] = core.get_storage_usage(remote)
    else:
        CACHE_USAGE.pop(remote.name, None)
    return message


def clear_screen() -> None:
    if core.IS_WINDOWS:
        import os

        os.system("cls")
    else:
        sys.stdout.write("\33[2J\33[H")
        sys.stdout.flush()


def display(remotes: List[core.RemoteInfo], status_line: str = "") -> None:
    clear_screen()
    print("cloud mount manager")
    if core.BASE_DIR_NOTE and not status_line:
        status_line = core.BASE_DIR_NOTE
    print(status_line or "")
    if not remotes:
        print("[!] No remotes found in rclone config.")
    for idx, remote in enumerate(remotes, 1):
        mounted = core.is_mounted(remote)
        marker = "[*]" if mounted else "[ ]"
        usage = CACHE_USAGE.get(remote.name, "")
        prefix = f"{idx:2d}. "
        mount_line = f"{marker} {remote.mount_path}  {usage}" if usage else f"{marker} {remote.mount_path}"
        normal_line = f"{marker} {remote.mount_path}"
        label = f"{remote.display_name:<30}"
        print_maybe_color(prefix, f"{label} {mount_line}", f"{label} {normal_line}", mounted)
    print(
        "\nnumber = mount/unmount,  'a' = mount all,  'u' = unmount all,  "
        "'r' = refresh,  'v' = verify,  'q' = quit"
    )


def main() -> None:
    ensure_app_directories()
    status = core.BASE_DIR_NOTE or ""
    try:
        while True:
            remotes = core.load_remotes()
            display(remotes, status)
            try:
                choice = input(" > ").strip()
            except EOFError:
                choice = "q"

            if choice == "":
                continue

            if choice.lower() == "q":
                status = "[*] leaving mounted remotes as they are."
                display(remotes, status)
                break
            if choice.lower() == "a":
                status = mount_all(remotes)
                continue
            if choice.lower() == "u":
                status = unmount_all(remotes)
                continue
            if choice.lower() == "r":
                status = refresh_all(remotes)
                continue
            if choice.lower() == "v":
                status = verify_all(remotes)
                continue

            try:
                idx = int(choice) - 1
                if 0 <= idx < len(remotes):
                    remote = remotes[idx]
                    status = toggle_remote(remote)
                    time.sleep(0.2)
                else:
                    status = "[!] invalid selection"
            except Exception as exc:
                status = f"[!] invalid input: {exc}"
    except KeyboardInterrupt:
        remotes = core.load_remotes()
        display(remotes, "[*] leaving mounted remotes as they are.")
        return


if __name__ == "__main__":
    main()
