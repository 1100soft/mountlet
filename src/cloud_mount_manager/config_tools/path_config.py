#!/usr/bin/env python3

"""Print Cloud Mount Manager and rclone path locations."""

from __future__ import annotations

import argparse

from .shared import (
    app_cache_dir,
    app_config_dir,
    app_config_file,
    app_state_dir,
    default_config_path,
    ensure_app_directories,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print user-specific configuration and data paths.")
    parser.add_argument("--ensure", action="store_true", help="Create Cloud Mount Manager user directories.")
    parser.add_argument("--rclone-config", action="store_true", help="Print only the rclone config path.")
    parser.add_argument(
        "--app-config",
        action="store_true",
        help="Print only the Cloud Mount Manager config file path.",
    )
    parser.add_argument("--state", action="store_true", help="Print only the Cloud Mount Manager state directory.")
    parser.add_argument("--cache", action="store_true", help="Print only the Cloud Mount Manager cache directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.ensure:
        ensure_app_directories()

    selected = {
        "rclone_config": args.rclone_config,
        "app_config": args.app_config,
        "state": args.state,
        "cache": args.cache,
    }
    show_all = not any(selected.values())
    paths = [
        ("rclone config", "rclone_config", default_config_path()),
        ("app config", "app_config", app_config_file()),
        ("app config directory", "app_config", app_config_dir()),
        ("state directory", "state", app_state_dir()),
        ("cache directory", "cache", app_cache_dir()),
    ]
    for label, key, path in paths:
        if show_all or selected[key]:
            print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
