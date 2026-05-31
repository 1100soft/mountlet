#!/usr/bin/env python3

from __future__ import annotations

import sys
from collections.abc import Callable

from . import __version__
from . import tui
from .config_tools import export_config, import_config, path_config, reconnect_config, setup_wizard, verify_config

Command = Callable[[list[str] | None], int | None]


def run_menu(argv: list[str] | None = None) -> int:
    if argv:
        print("The menu command does not accept options.", file=sys.stderr)
        return 2
    if not setup_wizard.ensure_ready_for_menu():
        return 1
    result = tui.main()
    return int(result or 0)


def run_path(argv: list[str] | None = None) -> int:
    return int(path_config.main(argv) or 0)


def run_setup(argv: list[str] | None = None) -> int:
    return int(setup_wizard.main(argv) or 0)


def run_verify(argv: list[str] | None = None) -> int:
    return int(verify_config.main(argv) or 0)


def run_reconnect(argv: list[str] | None = None) -> int:
    return int(reconnect_config.main(argv) or 0)


def run_export(argv: list[str] | None = None) -> int:
    return int(export_config.main(argv) or 0)


def run_import(argv: list[str] | None = None) -> int:
    return int(import_config.main(argv) or 0)


COMMANDS: dict[str, tuple[str, Command]] = {
    "menu": ("Open the interactive mount menu.", run_menu),
    "setup": ("Prepare the app for first use.", run_setup),
    "path": ("Show config, state, cache, and rclone paths.", run_path),
    "verify": ("Check whether configured remotes are reachable.", run_verify),
    "reconnect": ("Refresh credentials for one or more remotes.", run_reconnect),
    "export": ("Export an rclone configuration backup bundle.", run_export),
    "import": ("Import an rclone configuration backup bundle.", run_import),
}

ALIASES = {
    "paths": "path",
    "tui": "menu",
}


def print_help() -> None:
    print("cloud-mount-manager")
    print()
    print("Usage:")
    print("  cloud-mount-manager")
    print("  cloud-mount-manager <command> [options]")
    print()
    print("Commands:")
    for name, (description, _) in COMMANDS.items():
        print(f"  {name:<10} {description}")
    print()
    print("Run 'cloud-mount-manager <command> --help' for command-specific options.")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "-V"}:
        print(f"cloud-mount-manager {__version__}")
        return 0
    if not args:
        return run_menu()

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
