from __future__ import annotations

import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--packaging-smoke-test"]:
        print(f"Mountlet {__version__}")
        return 0
    if args == ["--packaging-rclone-smoke-test"]:
        from .config_tools.shared import run_rclone_version

        version = run_rclone_version()
        print(version)
        return 0 if version.startswith("rclone v") else 1

    from . import tray

    return int(tray.main([]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
