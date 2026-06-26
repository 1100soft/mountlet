from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from pathlib import Path


def bundle_executable(dist: Path, system: str) -> Path:
    if system == "Darwin":
        return dist / "Mountlet.app" / "Contents" / "MacOS" / "Mountlet"
    suffix = ".exe" if system == "Windows" else ""
    return dist / "Mountlet" / f"Mountlet{suffix}"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dist = Path(os.environ.get("MOUNTLET_DIST_DIR", root / "dist")).resolve()
    system = platform.system()
    executable = bundle_executable(dist, system)
    if not executable.is_file():
        raise RuntimeError(f"Packaged executable is missing: {executable}")

    result = subprocess.run(
        [str(executable), "--packaging-smoke-test"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not result.stdout.startswith("Mountlet "):
        raise RuntimeError(f"Unexpected smoke-test output: {result.stdout!r}")

    if system == "Darwin":
        plist_path = dist / "Mountlet.app" / "Contents" / "Info.plist"
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        if info.get("LSUIElement") is not True:
            raise RuntimeError("The macOS bundle is not configured as a menu-bar app.")

    print(f"Verified {executable}: {result.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
