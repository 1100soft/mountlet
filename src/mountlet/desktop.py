from __future__ import annotations

import sys
import time
import traceback

from . import __version__


FAST_EXIT_SECONDS = 5.0


def _write_startup_log(message: str) -> None:
    try:
        from .config_tools.shared import app_state_dir

        path = app_state_dir() / "startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message, encoding="utf-8")
    except Exception:
        print(message, file=sys.stderr)


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

    started = time.monotonic()
    try:
        from . import tray

        result = int(tray.main([]) or 0)
    except Exception:
        detail = f"Mountlet {__version__} failed during desktop startup.\n\n{traceback.format_exc()}"
        _write_startup_log(detail)
        return 1

    elapsed = time.monotonic() - started
    if result != 0 or elapsed < FAST_EXIT_SECONDS:
        _write_startup_log(
            f"Mountlet {__version__} exited during desktop startup.\n\n"
            f"Exit code: {result}\n"
            f"Elapsed: {elapsed:.2f}s\n"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
