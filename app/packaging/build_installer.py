from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


def _project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def _windows_iscc() -> str:
    if found := shutil.which("ISCC.exe") or shutil.which("iscc"):
        return found
    candidates = (
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Inno Setup 6 was not found.")


def _build_windows(root: Path, dist: Path, output: Path, version: str) -> None:
    source = dist / "Mountlet"
    executable = source / "Mountlet.exe"
    if not executable.is_file():
        raise RuntimeError(f"Windows bundle is missing: {executable}")
    if output.suffix.casefold() != ".exe":
        raise ValueError("The Windows installer name must end in .exe")
    subprocess.run(
        [
            _windows_iscc(),
            f"/DAppVersion={version}",
            f"/DSourceDir={source}",
            f"/DOutputDir={output.parent}",
            f"/DOutputBaseName={output.stem}",
            f"/DBundledRclone={1 if _bundle_has_rclone(source, 'Windows') else 0}",
            str(root / "packaging" / "windows" / "mountlet.iss"),
        ],
        check=True,
    )


def _installed_size_kib(path: Path) -> int:
    return max(1, sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) // 1024)


def _linux_architecture() -> str:
    machine = platform.machine().casefold()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported Debian package architecture: {machine}")


def _bundle_has_rclone(source: Path, system: str) -> bool:
    name = "rclone.exe" if system == "Windows" else "rclone"
    return any(
        (source / relative / name).is_file()
        for relative in (Path("vendor") / "rclone", Path("_internal") / "vendor" / "rclone")
    )


def _build_linux(root: Path, dist: Path, output: Path, version: str) -> None:
    source = dist / "Mountlet"
    executable = source / "Mountlet"
    if not executable.is_file():
        raise RuntimeError(f"Linux bundle is missing: {executable}")
    if output.suffix.casefold() != ".deb":
        raise ValueError("The Linux installer name must end in .deb")

    with tempfile.TemporaryDirectory(prefix="mountlet-deb-") as temporary:
        package_root = Path(temporary) / "mountlet"
        app_dir = package_root / "opt" / "mountlet"
        shutil.copytree(source, app_dir)

        bin_dir = package_root / "usr" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "mountlet").symlink_to("/opt/mountlet/Mountlet")

        applications = package_root / "usr" / "share" / "applications"
        applications.mkdir(parents=True)
        (applications / "com.ericholt.mountlet.desktop").write_text(
            "\n".join(
                (
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Mountlet",
                    "Comment=Mount cloud storage folders",
                    "Exec=/opt/mountlet/Mountlet",
                    "Icon=mountlet",
                    "Terminal=false",
                    "StartupWMClass=Mountlet",
                    "Categories=Utility;FileTools;",
                    "",
                )
            ),
            encoding="utf-8",
        )

        icon_dir = package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        icon_dir.mkdir(parents=True)
        shutil.copy2(root / "src" / "mountlet" / "assets" / "icon.png", icon_dir / "mountlet.png")

        debian = package_root / "DEBIAN"
        debian.mkdir()
        control_lines = [
            "Package: mountlet",
            f"Version: {version}",
            f"Architecture: {_linux_architecture()}",
            "Maintainer: Eric Holt",
            f"Installed-Size: {_installed_size_kib(app_dir)}",
            "Section: utils",
            "Priority: optional",
            "Depends: python3 (>= 3.10)",
        ]
        if not _bundle_has_rclone(app_dir, "Linux"):
            control_lines.append("Recommends: rclone")
        control_lines.extend(
            (
                "Suggests: fuse3",
                "Description: Desktop controls for rclone cloud storage",
                " Mountlet uses rclone for cloud access. FUSE is optional and only needed",
                " for native folder mounting.",
                "",
            )
        )
        (debian / "control").write_text(
            "\n".join(control_lines),
            encoding="utf-8",
        )
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(package_root), str(output)],
            check=True,
        )


def _build_macos(dist: Path, output: Path) -> None:
    source = dist / "Mountlet.app"
    if not source.is_dir():
        raise RuntimeError(f"macOS app bundle is missing: {source}")
    if output.suffix.casefold() != ".dmg":
        raise ValueError("The macOS installer name must end in .dmg")

    with tempfile.TemporaryDirectory(prefix="mountlet-dmg-") as temporary:
        staging = Path(temporary) / "Mountlet"
        staging.mkdir()
        shutil.copytree(source, staging / source.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        output.unlink(missing_ok=True)
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Mountlet",
                "-srcfolder",
                str(staging),
                "-format",
                "UDZO",
                "-ov",
                str(output),
            ],
            check=True,
        )


def build_installer(name: str, dist: Path, output_dir: Path, system: str, root: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / name
    version = _project_version(root)
    if system == "Windows":
        _build_windows(root, dist, output, version)
    elif system == "Darwin":
        _build_macos(dist, output)
    else:
        _build_linux(root, dist, output, version)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Installer was not created: {output}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a native installer from the Mountlet bundle.")
    parser.add_argument("--name", required=True, help="Installer file name, including its extension.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = build_installer(
        args.name,
        args.dist_dir.resolve(),
        args.output_dir.resolve(),
        platform.system(),
        root,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
