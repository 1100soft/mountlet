from __future__ import annotations

import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--packaging-smoke-test"]:
        print(f"Mountlet {__version__}")
        return 0

    from . import tray

    return int(tray.main([]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
