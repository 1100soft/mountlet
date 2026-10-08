#!/usr/bin/env python3

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from . import __version__

Command = Callable[[list[str] | None], int | None]


def _run_module_main(module: Any, argv: list[str] | None = None) -> int:
    return int(module.main(argv) or 0)


def run_menu(argv: list[str] | None = None) -> int:
    from . import tui
    from .config_tools import setup_wizard

    if argv:
        print("The menu command does not accept options.", file=sys.stderr)
        return 2
    if not setup_wizard.ensure_ready_for_menu():
        return 1
    result = tui.main()
    return int(result or 0)


def run_path(argv: list[str] | None = None) -> int:
    from .config_tools import path_config

    return _run_module_main(path_config, argv)


def run_setup(argv: list[str] | None = None) -> int:
    from .config_tools import setup_wizard

    return _run_module_main(setup_wizard, argv)


def run_verify(argv: list[str] | None = None) -> int:
    from .config_tools import verify_config

    return _run_module_main(verify_config, argv)


def run_reconnect(argv: list[str] | None = None) -> int:
    from .config_tools import reconnect_config

    return _run_module_main(reconnect_config, argv)


def run_export(argv: list[str] | None = None) -> int:
    from .config_tools import export_config

    return _run_module_main(export_config, argv)


def run_import(argv: list[str] | None = None) -> int:
    from .config_tools import import_config

    return _run_module_main(import_config, argv)


def run_tray(argv: list[str] | None = None) -> int:
    from . import tray

    return _run_module_main(tray, argv)


def run_debug(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args in (["-h"], ["--help"]):
        print("Usage:")
        print("  mountlet debug expire-trial")
        print()
        print("Debug commands:")
        print("  expire-trial  End the local trial immediately for license-flow testing.")
        return 0
    if args == ["expire-trial"]:
        from . import license_control

        status_before = license_control.current_status()
        license_control.expire_trial_for_debug()
        status_after = license_control.current_status()
        print(f"Before: {status_before.summary}")
        print(f"After:  {status_after.summary}")
        return 0
    print("[!] Unknown debug command.", file=sys.stderr)
    print("Run 'mountlet debug --help'.", file=sys.stderr)
    return 2


COMMANDS: dict[str, tuple[str, Command]] = {
    "menu": ("Open the interactive mount menu.", run_menu),
    "tray": ("Open the desktop tray app. This is also the default.", run_tray),
    "setup": ("Prepare the app for first use.", run_setup),
    "path": ("Show config, state, cache, and rclone paths.", run_path),
    "verify": ("Check whether configured remotes are reachable.", run_verify),
    "reconnect": ("Refresh credentials for one or more remotes.", run_reconnect),
    "export": ("Export an rclone configuration backup bundle.", run_export),
    "import": ("Import an rclone configuration backup bundle.", run_import),
    "debug": ("Run developer/debug helpers.", run_debug),
}

ALIASES = {
    "paths": "path",
    "tui": "menu",
}


def print_help() -> None:
    print("mountlet")
    print()
    print("Usage:")
    print("  mountlet                 Open the desktop tray app")
    print("  mountlet <command> [options]")
    print()
    print("Commands:")
    for name, (description, _) in COMMANDS.items():
        print(f"  {name:<10} {description}")
    print()
    print("Run 'mountlet <command> --help' for command-specific options.")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "-V"}:
        print(f"mountlet {__version__}")
        return 0
    if not args:
        return run_tray([])

    command_name = args.pop(0)
    if command_name in {"-h", "--help", "help"}:
        if command_name == "help" and args:
            command_name = ALIASES.get(args[0], args[0])
            command = COMMANDS.get(command_name)
            if command:
                return int(command[1](["--help"]) or 0)
        print_help()
        return 0

    command_name = ALIASES.get(command_name, command_name)
    command = COMMANDS.get(command_name)
    if not command:
        print(f"[!] Unknown command: {command_name}", file=sys.stderr)
        print_help()
        return 2

    result = command[1](args)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
