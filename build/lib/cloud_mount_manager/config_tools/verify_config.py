#!/usr/bin/env python3

"""Verify access to rclone remotes, optionally auto-reconnecting failures."""

from __future__ import annotations

import argparse
from pathlib import Path

from .shared import (
    default_config_path,
    print_remote_list,
    resolve_remote_selection,
    verify_remotes,
    reconnect_remotes,
)


def verify_command(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    if not config_path.exists():
        print(f"[!] Config file not found: {config_path}")
        return 1

    selected, missing = resolve_remote_selection(config_path, args.remote)
    for name in missing:
        print(f"[!] Remote not found in config: {name}")

    if not selected:
        print("[!] No remotes available for verification.")
        return 1

    print_remote_list(selected, config_path, label="Verifying remotes from")
    results = verify_remotes(selected)
    if not results:
        return 1

    failures = [name for name, (ok, _) in results.items() if not ok]

    if args.auto_reconnect and failures:
        print("[i] Attempting to reconnect failing remotes...")
        reconnect_remotes(failures, args.reconnect_auto_confirm)
        print_remote_list(failures, config_path, label="Re-checking remotes after reconnect from")
        results.update(verify_remotes(failures))
        failures = [name for name, (ok, _) in results.items() if not ok]

    if failures:
        for name in failures:
            print(f"[!] {name}: still failing after verification.")
        return 1

    print("[✓] All selected remotes verified.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify rclone remotes and optionally auto-reconnect them.")
    parser.add_argument(
        "--config",
        help="Config file to read remotes from (default: detected rclone path).",
    )
    parser.add_argument(
        "--remote",
        action="append",
        default=[],
        help="Specific remote to verify (repeatable). Defaults to all remotes in the config.",
    )
    parser.add_argument(
        "--no-auto-reconnect",
        dest="auto_reconnect",
        action="store_false",
        help="Do not attempt automatic reconnect for failing remotes.",
    )
    parser.add_argument(
        "--no-reconnect-auto-confirm",
        dest="reconnect_auto_confirm",
        action="store_false",
        help="When auto-reconnecting, do not pass --auto-confirm to rclone.",
    )
    parser.set_defaults(auto_reconnect=True, reconnect_auto_confirm=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return verify_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
