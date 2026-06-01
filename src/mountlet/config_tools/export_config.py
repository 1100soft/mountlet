#!/usr/bin/env python3

"""Export the current rclone configuration and secrets into a directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from .shared import (
    copy_file,
    default_config_path,
    find_client_secrets,
    ensure_dir,
    print_remote_list,
    read_remotes,
)


def export_bundle(args: argparse.Namespace) -> int:
    source_conf = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    if not source_conf.exists():
        print(f"[!] Config file not found: {source_conf}")
        return 1

    destination = Path(args.destination).expanduser().resolve()
    ensure_dir(destination)

    copied_conf = copy_file(source_conf, destination / source_conf.name, backup=args.backup, dry_run=args.dry_run)
    if copied_conf:
        action = "would copy" if args.dry_run else "copied"
        print(f"[*] {action} {source_conf} -> {copied_conf}")

    secrets: list[Path] = []
    for secret in args.client_secret:
        path = Path(secret).expanduser().resolve()
        if path.exists():
            secrets.append(path)
        else:
            print(f"[!] client secret missing: {path}")

    if args.auto_discover_secrets:
        for item in find_client_secrets(source_conf.parent):
            if item not in secrets:
                secrets.append(item)

    for secret in secrets:
        dest_secret = destination / secret.name
        copied_secret = copy_file(secret, dest_secret, backup=args.backup, dry_run=args.dry_run)
        if copied_secret:
            action = "would copy" if args.dry_run else "copied"
            print(f"[*] {action} {secret} -> {copied_secret}")

    if not args.dry_run:
        remotes = read_remotes(source_conf)
        print_remote_list(remotes, source_conf)
        print(f"[i] Bundle ready at {destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export rclone configuration and secrets into a directory.")
    parser.add_argument("destination", help="Directory where the bundle will be written.")
    parser.add_argument(
        "--config",
        help="Override source rclone.conf (default: detected path).",
    )
    parser.add_argument(
        "--client-secret",
        action="append",
        default=[],
        help="Explicit client_secret JSON files to include (repeatable).",
    )
    parser.add_argument(
        "--no-auto-discover",
        dest="auto_discover_secrets",
        action="store_false",
        help="Disable searching for client_secret*.json next to the config file.",
    )
    parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Do not backup existing files in destination.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without copying.")
    parser.set_defaults(auto_discover_secrets=True, backup=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return export_bundle(args)


if __name__ == "__main__":
    raise SystemExit(main())
