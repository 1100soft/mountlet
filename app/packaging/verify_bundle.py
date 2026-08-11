from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from pathlib import Path

from stage_rclone import WINDOWS_SHIM_MAX_BYTES


def bundle_executable(dist: Path, system: str) -> Path:
    if system == "Darwin":
        return dist / "Mountlet.app" / "Contents" / "MacOS" / "Mountlet"
    suffix = ".exe" if system == "Windows" else ""
    return dist / "Mountlet" / f"Mountlet{suffix}"


def _truthy(value: str | None) -> bool:
    return value is not None and value.casefold() in {"1", "true", "yes", "on"}


def bundled_rclone_candidates(dist: Path, system: str) -> list[Path]:
    name = "rclone.exe" if system == "Windows" else "rclone"
    if system == "Darwin":
        app = dist / "Mountlet.app" / "Contents"
        roots = [
            app / "MacOS",
            app / "MacOS" / "_internal",
            app / "Frameworks",
            app / "Frameworks" / "_internal",
            app / "Resources",
            app / "Resources" / "_internal",
        ]
    else:
        app = dist / "Mountlet"
        roots = [app, app / "_internal"]
    candidates: list[Path] = []
    for root in roots:
        candidates.extend((root / "vendor" / "rclone").glob(f"*/{name}"))
        candidates.append(root / "vendor" / "rclone" / name)
    return candidates


def _verify_bundled_rclone(dist: Path, executable: Path, system: str) -> None:
    candidates = [candidate for candidate in bundled_rclone_candidates(dist, system) if candidate.is_file()]
    if not candidates:
        searched = "\n".join(str(candidate) for candidate in bundled_rclone_candidates(dist, system))
        raise RuntimeError(f"Bundled rclone was expected but not found. Searched:\n{searched}")
    bundled = candidates[0]
    if system == "Windows" and bundled.stat().st_size < WINDOWS_SHIM_MAX_BYTES:
        raise RuntimeError(f"Bundled rclone looks like a package-manager shim, not the real binary: {bundled}")

    direct = subprocess.run(
        [str(bundled), "version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    direct_output = direct.stdout.strip() or direct.stderr.strip()
    if direct.returncode != 0 or not direct_output.startswith("rclone v"):
        raise RuntimeError(f"Bundled rclone does not run correctly: {direct_output!r}")

    app_result = subprocess.run(
        [str(executable), "--packaging-rclone-smoke-test"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    app_output = app_result.stdout.strip() or app_result.stderr.strip()
    if app_result.returncode != 0 or not app_output.startswith("rclone v"):
        raise RuntimeError(f"Packaged Mountlet cannot execute bundled rclone: {app_output!r}")


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

    startup_result = subprocess.run(
        [str(executable), "--packaging-startup-import-test"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not startup_result.stdout.startswith("Mountlet ") or "startup imports ok" not in startup_result.stdout:
        raise RuntimeError(f"Unexpected startup import output: {startup_result.stdout!r}")

    if system == "Darwin":
        plist_path = dist / "Mountlet.app" / "Contents" / "Info.plist"
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        if info.get("LSUIElement") is not True:
            raise RuntimeError("The macOS bundle is not configured as a menu-bar app.")
        minimum = info.get("LSMinimumSystemVersion")
        if not minimum:
            raise RuntimeError("The macOS bundle does not declare LSMinimumSystemVersion.")

    if _truthy(os.environ.get("MOUNTLET_EXPECT_BUNDLED_RCLONE")):
        _verify_bundled_rclone(dist, executable, system)

    print(f"Verified {executable}: {result.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
