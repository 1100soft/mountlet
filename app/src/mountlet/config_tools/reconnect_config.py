#!/usr/bin/env python3

"""Force rclone to reconnect selected remotes."""

from __future__ import annotations

import argparse
from pathlib import Path

from .shared import (
    default_config_path,
    print_remote_list,
    resolve_remote_selection,
    reconnect_remotes,
)


def reconnect_command(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    if not config_path.exists():
        print(f"[!] Config file not found: {config_path}")
        return 1

    selected, missing = resolve_remote_selection(config_path, args.remote)
    for name in missing:
        print(f"[!] Remote not found in config: {name}")

    if not selected:
        print("[!] No remotes available for reconnection.")
        return 1

    print_remote_list(selected, config_path, label="Reconnecting remotes from")
    reconnect_remotes(selected, args.reconnect_auto_confirm)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run 'rclone config reconnect' for selected remotes.")
    parser.add_argument(
        "--config",
        help="Config file to read remotes from (default: detected rclone path).",
    )
    parser.add_argument(
        "--remote",
        action="append",
        default=[],
        help="Specific remote to reconnect (repeatable). Defaults to all remotes in the config.",
    )
    parser.add_argument(
        "--no-auto-confirm",
        dest="reconnect_auto_confirm",
        action="store_false",
        help="Do not pass --auto-confirm to reconnect commands.",
    )
    parser.set_defaults(reconnect_auto_confirm=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return reconnect_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
