from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path


WINDOWS_SHIM_MAX_BYTES = 1_000_000
RCLONE_DOWNLOAD_BASE = "https://downloads.rclone.org/rclone-current-osx-{arch}.zip"


def _binary_name(system: str) -> str:
    return "rclone.exe" if system == "Windows" else "rclone"


def _candidate_is_windows_shim(path: Path) -> bool:
    normalized = tuple(part.casefold() for part in path.parts)
    if len(normalized) >= 3 and normalized[-3:] == ("chocolatey", "bin", "rclone.exe"):
        return True
    if len(normalized) >= 3 and normalized[-3:] == ("scoop", "shims", "rclone.exe"):
        return True
    if "winget" in normalized and "links" in normalized:
        return True
    try:
        return path.stat().st_size < WINDOWS_SHIM_MAX_BYTES
    except OSError:
        return True


def _runs_like_rclone(path: Path) -> bool:
    try:
        result = subprocess.run(
            [str(path), "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = result.stdout.strip() or result.stderr.strip()
    return result.returncode == 0 and output.startswith("rclone v")


def _valid_rclone_binary(path: Path, system: str) -> bool:
    if not path.is_file():
        return False
    if system == "Windows" and _candidate_is_windows_shim(path):
        return False
    return _runs_like_rclone(path)


def _windows_real_rclone_candidates() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    program_files = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
    ]
    chocolatey = Path(os.environ.get("ChocolateyInstall", "C:/ProgramData/chocolatey"))
    scoop = Path(os.environ.get("SCOOP", home / "scoop"))
    direct = [
        *(root / "rclone" / "rclone.exe" for root in program_files),
        local / "rclone" / "rclone.exe",
        home / "rclone" / "rclone.exe",
        Path("C:/rclone/rclone.exe"),
        scoop / "apps" / "rclone" / "current" / "rclone.exe",
        chocolatey / "lib" / "rclone" / "tools" / "rclone.exe",
        chocolatey / "lib" / "rclone.portable" / "tools" / "rclone.exe",
    ]
    discovered: list[Path] = []
    for root in (
        chocolatey / "lib" / "rclone",
        chocolatey / "lib" / "rclone.portable",
        scoop / "apps" / "rclone",
        local / "Microsoft" / "WinGet" / "Packages",
    ):
        if root.exists():
            discovered.extend(root.rglob("rclone.exe"))
    return [*direct, *discovered]


def _macos_download_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).casefold()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return "amd64"


def download_official_macos_rclone(destination: Path, *, arch: str | None = None) -> Path:
    resolved_arch = arch or _macos_download_arch()
    url = RCLONE_DOWNLOAD_BASE.format(arch=resolved_arch)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"rclone-current-osx-{resolved_arch}.zip"
    urllib.request.urlretrieve(url, archive)
    with zipfile.ZipFile(archive) as handle:
        members = [name for name in handle.namelist() if name.endswith("/rclone")]
        if not members:
            raise RuntimeError(f"The downloaded rclone archive did not contain a macOS rclone binary: {url}")
        member = members[0]
        target = destination / "rclone"
        with handle.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    target.chmod(target.stat().st_mode | 0o755)
    return target


def resolve_rclone_source(source: Path | None, system: str) -> Path:
    candidates: list[Path] = []
    if source is not None:
        candidates.append(source)
    else:
        found = shutil.which(_binary_name(system)) or shutil.which("rclone")
        if found:
            candidates.append(Path(found))
    if system == "Windows":
        candidates.extend(_windows_real_rclone_candidates())

    for candidate in dict.fromkeys(candidate.resolve() for candidate in candidates):
        if _valid_rclone_binary(candidate, system):
            return candidate

    hint = " Pass the real rclone.exe path explicitly; package-manager shims cannot be bundled." if system == "Windows" else ""
    raise RuntimeError(f"A usable rclone binary was not found.{hint}")


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

    system = platform.system()
    if system == "Darwin" and args.rclone is None:
        with tempfile.TemporaryDirectory(prefix="mountlet-rclone-") as tempdir:
            source = download_official_macos_rclone(Path(tempdir))
            target = stage_rclone(source.resolve(), args.destination.resolve(), system)
    else:
        source = resolve_rclone_source(args.rclone, system)
        target = stage_rclone(source.resolve(), args.destination.resolve(), system)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
