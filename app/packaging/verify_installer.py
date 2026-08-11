from __future__ import annotations

import argparse
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from stage_rclone import WINDOWS_SHIM_MAX_BYTES


def _truthy(value: str | None) -> bool:
    return value is not None and value.casefold() in {"1", "true", "yes", "on"}


def _verify_windows_bundled_rclone(destination: Path, executable: Path) -> None:
    candidates = [
        destination / "vendor" / "rclone" / "rclone.exe",
        destination / "_internal" / "vendor" / "rclone" / "rclone.exe",
    ]
    candidates.extend((destination / "vendor" / "rclone").glob("*/rclone.exe"))
    candidates.extend((destination / "_internal" / "vendor" / "rclone").glob("*/rclone.exe"))
    bundled = next((candidate for candidate in candidates if candidate.is_file()), None)
    if bundled is None:
        raise RuntimeError("The installed bundled-rclone build does not contain rclone.exe.")
    if bundled.stat().st_size < WINDOWS_SHIM_MAX_BYTES:
        raise RuntimeError(f"The installed rclone.exe looks like a package-manager shim: {bundled}")
    app_result = subprocess.run(
        [str(executable), "--packaging-rclone-smoke-test"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = app_result.stdout.strip() or app_result.stderr.strip()
    if app_result.returncode != 0 or not output.startswith("rclone v"):
        raise RuntimeError(f"The installed app cannot run bundled rclone: {output!r}")


def _verify_windows(installer: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mountlet-install-") as temporary:
        destination = Path(temporary) / "Mountlet"
        subprocess.run(
            [
                str(installer),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/PACKAGINGTEST",
                f"/DIR={destination}",
            ],
            check=True,
            timeout=120,
        )
        executable = destination / "Mountlet.exe"
        result = subprocess.run(
            [str(executable), "--packaging-smoke-test"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if not result.stdout.startswith("Mountlet "):
            raise RuntimeError(f"Unexpected installed output: {result.stdout!r}")
        if _truthy(os.environ.get("MOUNTLET_EXPECT_BUNDLED_RCLONE")):
            _verify_windows_bundled_rclone(destination, executable)
        uninstaller = destination / "unins000.exe"
        subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            check=True,
            timeout=120,
        )


def _verify_linux(installer: Path) -> None:
    result = subprocess.run(
        ["dpkg-deb", "--field", str(installer), "Package"],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != "mountlet":
        raise RuntimeError(f"Unexpected Debian package name: {result.stdout!r}")
    contents = subprocess.run(
        ["dpkg-deb", "--contents", str(installer)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for expected in ("./opt/mountlet/Mountlet", "./usr/bin/mountlet"):
        if expected not in contents:
            raise RuntimeError(f"Debian package is missing {expected}")


def _verify_macos(installer: Path) -> None:
    subprocess.run(["hdiutil", "verify", str(installer)], check=True, timeout=120)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Mountlet native installer.")
    parser.add_argument("installer", type=Path)
    args = parser.parse_args(argv)
    installer = args.installer.resolve()
    if not installer.is_file():
        raise RuntimeError(f"Installer is missing: {installer}")
    system = platform.system()
    if system == "Windows":
        _verify_windows(installer)
    elif system == "Darwin":
        _verify_macos(installer)
    else:
        _verify_linux(installer)
    print(f"Verified installer: {installer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
