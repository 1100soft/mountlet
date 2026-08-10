from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

try:
    import resource
except ImportError:  # Windows
    resource = None


REQUIRED_REGRESSION_TESTS = (
    "tests.test_ui_zoom.UiZoomTests.test_production_qt_namespace_constructs_file_browser",
    "tests.test_ui_zoom.UiZoomTests.test_file_list_integer_height_has_no_scrollbar_at_every_zoom",
)


def _workflow_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _child_usage() -> tuple[float, int | None]:
    if resource is None:
        return 0.0, None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak = int(usage.ru_maxrss)
    # Linux and the BSDs report KiB; macOS reports bytes.
    peak_bytes = peak if sys.platform == "darwin" else peak * 1024
    return float(usage.ru_utime + usage.ru_stime), peak_bytes


def _write_resource_report(path: str, *, wall_seconds: float, cpu_seconds: float, peak_bytes: int | None) -> None:
    payload = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "wall_seconds": round(wall_seconds, 3),
        "cpu_seconds": round(cpu_seconds, 3),
        "peak_child_rss_bytes": peak_bytes,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resource-report",
        default=os.environ.get("MOUNTLET_TEST_RESOURCE_REPORT", ""),
        help="write test CPU, wall-time, and peak-memory measurements as JSON",
    )
    args = parser.parse_args()
    started = time.perf_counter()
    cpu_before, _peak_before = _child_usage()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode:
        detail = (result.stdout + result.stderr)[-10000:]
        print(f"::error title=Unit tests failed::{_workflow_escape(detail)}")
        return result.returncode
    # Discovery alone succeeds if a regression test is accidentally removed
    # during cleanup.  Run release-critical behavioral tests by stable name so
    # deletion or renaming fails the release gate instead of reducing coverage
    # silently.
    required = subprocess.run(
        [sys.executable, "-m", "unittest", *REQUIRED_REGRESSION_TESTS],
        capture_output=True,
        text=True,
    )
    sys.stdout.write(required.stdout)
    sys.stderr.write(required.stderr)
    if required.returncode:
        detail = (required.stdout + required.stderr)[-10000:]
        print(f"::error title=Required regression test failed::{_workflow_escape(detail)}")
    cpu_after, peak_bytes = _child_usage()
    wall_seconds = time.perf_counter() - started
    cpu_seconds = max(cpu_after - cpu_before, 0.0)
    peak_text = f"{peak_bytes / (1024 * 1024):.1f} MiB" if peak_bytes is not None else "unavailable"
    print(
        f"Test resources: wall={wall_seconds:.2f}s cpu={cpu_seconds:.2f}s "
        f"peak_child_rss={peak_text} logical_cpus={os.cpu_count() or 'unknown'}"
    )
    if args.resource_report:
        _write_resource_report(
            args.resource_report,
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            peak_bytes=peak_bytes,
        )
    return required.returncode


if __name__ == "__main__":
    raise SystemExit(main())
