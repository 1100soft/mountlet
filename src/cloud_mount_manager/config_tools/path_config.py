#!/usr/bin/env python3

"""Print the default rclone configuration path."""

from __future__ import annotations

from .shared import default_config_path


def main() -> int:
    print(default_config_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
