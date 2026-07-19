from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path


def build_channel() -> str:
    configured = os.environ.get("MOUNTLET_BUILD_CHANNEL", "").strip().lower()
    if configured:
        return configured
    ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    if ref_name == "wip":
        return "preview"
    if ref_name == "main" or ref_name.startswith("v"):
        return "production"
    return "local"


def build_info_data() -> dict[str, str]:
    channel = build_channel()
    report_api_url = os.environ.get("MOUNTLET_DEFAULT_REPORT_API_URL", "").strip()
    license_api_url = os.environ.get("MOUNTLET_DEFAULT_LICENSE_API_URL", "").strip()
    license_site_url = os.environ.get("MOUNTLET_DEFAULT_LICENSE_SITE_URL", "").strip()
    if not report_api_url and channel == "preview":
        report_api_url = "https://wip.mountlet.pages.dev/api/report"
    if not license_api_url and channel == "preview":
        license_api_url = "https://wip.mountlet.pages.dev/api/license"
    if not license_site_url and channel == "preview":
        license_site_url = "https://wip.mountlet.pages.dev"
    return {
        "channel": channel,
        "licenseApiUrl": license_api_url,
        "licenseSiteUrl": license_site_url,
        "reportApiUrl": report_api_url,
    }


def rclone_binary_name() -> str:
    return "rclone.exe" if sys.platform == "win32" else "rclone"


def staged_rclone(root: Path) -> Path | None:
    configured = os.environ.get("MOUNTLET_BUNDLED_RCLONE_PATH", "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_file() else None
    candidate = root / "vendor" / "rclone" / rclone_binary_name()
    return candidate if candidate.is_file() else None


def write_launcher(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                'APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"',
                'PYTHON_BIN="${MOUNTLET_PYTHON:-python3}"',
                'export PYTHONPATH="$APP_DIR/lib${PYTHONPATH:+:$PYTHONPATH}"',
                'if [ -x "$APP_DIR/vendor/rclone/rclone" ]; then',
                '  export RCLONE_PATH="$APP_DIR/vendor/rclone/rclone"',
                "fi",
                'exec "$PYTHON_BIN" -m mountlet.desktop "$@"',
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_build_info(lib_dir: Path) -> None:
    package = lib_dir / "mountlet"
    if not package.is_dir():
        raise RuntimeError(f"Mountlet package was not installed into {lib_dir}")
    (package / "mountlet-build-info.json").write_text(
        json.dumps(build_info_data(), sort_keys=True),
        encoding="utf-8",
    )


def build_linux_bundle(root: Path, dist: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["name"] != "mountlet":
        raise RuntimeError("This script must run from the Mountlet app root.")
    bundle = dist / "Mountlet"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    lib_dir = bundle / "lib"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--target",
            str(lib_dir),
            f"{root}[desktop]",
        ],
        check=True,
    )
    install_build_info(lib_dir)

    rclone = staged_rclone(root)
    if rclone is not None:
        target = bundle / "vendor" / "rclone" / "rclone"
        target.parent.mkdir(parents=True)
        shutil.copy2(rclone, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    write_launcher(bundle / "Mountlet")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Linux Mountlet bundle without freezing Qt.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    bundle = build_linux_bundle(root, args.dist_dir.resolve())
    print(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
