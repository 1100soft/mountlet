from __future__ import annotations

import argparse
import platform
import shutil
from pathlib import Path


def _binary_name(system: str) -> str:
    return "rclone.exe" if system == "Windows" else "rclone"


def stage_rclone(source: Path, destination: Path, system: str) -> Path:
    if not source.is_file():
        raise RuntimeError(f"rclone binary is missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / _binary_name(system)
    shutil.copy2(source, target)
    if system != "Windows":
        target.chmod(target.stat().st_mode | 0o755)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage a rclone binary for Mountlet bundled builds.")
    parser.add_argument("rclone", nargs="?", type=Path, help="Path to the rclone binary. Defaults to PATH lookup.")
    parser.add_argument("--destination", type=Path, default=Path("vendor") / "rclone")
    args = parser.parse_args(argv)

    source = args.rclone
    if source is None:
        found = shutil.which(_binary_name(platform.system())) or shutil.which("rclone")
        if not found:
            raise RuntimeError("rclone was not found. Pass the binary path explicitly.")
        source = Path(found)
    target = stage_rclone(source.resolve(), args.destination.resolve(), platform.system())
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
