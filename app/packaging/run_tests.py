from __future__ import annotations

import subprocess
import sys


def _workflow_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
