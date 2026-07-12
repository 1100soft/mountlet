from __future__ import annotations

import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path

from . import __version__


FAST_EXIT_SECONDS = 5.0
_RUNTIME_LOG_HANDLE = None


def _write_startup_log(message: str) -> None:
    try:
        from .config_tools.shared import app_state_dir

        path = app_state_dir() / "startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(message, encoding="utf-8")
    except Exception:
        print(message, file=sys.stderr)


def _runtime_log_path() -> Path | None:
    try:
        from .config_tools.shared import app_state_dir

        path = app_state_dir() / "runtime.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        return None


def _append_runtime_log(message: str) -> None:
    path = _runtime_log_path()
    if path is None:
        print(message, file=sys.stderr)
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        print(message, file=sys.stderr)


def _install_runtime_logging() -> None:
    global _RUNTIME_LOG_HANDLE
    path = _runtime_log_path()
    if path is not None and _RUNTIME_LOG_HANDLE is None:
        try:
            _RUNTIME_LOG_HANDLE = path.open("a", encoding="utf-8")
            _RUNTIME_LOG_HANDLE.write(f"\nMountlet {__version__} runtime started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            _RUNTIME_LOG_HANDLE.flush()
            faulthandler.enable(file=_RUNTIME_LOG_HANDLE, all_threads=True)
        except Exception:
            _RUNTIME_LOG_HANDLE = None
    previous_excepthook = sys.excepthook
    previous_threading_excepthook = getattr(threading, "excepthook", None)

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        _append_runtime_log("Unhandled exception:\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
        previous_excepthook(exc_type, exc, tb)

    def threading_excepthook(args: threading.ExceptHookArgs) -> None:
        _append_runtime_log(
            "Unhandled thread exception:\n"
            + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        )
        if previous_threading_excepthook is not None:
            previous_threading_excepthook(args)

    sys.excepthook = excepthook
    if previous_threading_excepthook is not None:
        threading.excepthook = threading_excepthook


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--packaging-smoke-test"]:
        print(f"Mountlet {__version__}")
        return 0
    if args == ["--packaging-startup-import-test"]:
        from . import tray  # noqa: F401

        print(f"Mountlet {__version__} startup imports ok")
        return 0
    if args == ["--packaging-rclone-smoke-test"]:
        from .config_tools.shared import run_rclone_version

        version = run_rclone_version()
        print(version)
        return 0 if version.startswith("rclone v") else 1

    _install_runtime_logging()
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
