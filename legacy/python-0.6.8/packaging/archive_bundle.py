from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path


def archive_bundle(name: str, dist: Path, output_dir: Path, system: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        source = dist / "Mountlet.app"
        output = output_dir / f"{name}.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(source), str(output)],
            check=True,
        )
        return output
    if system == "Windows":
        source = dist / "Mountlet"
        output_base = output_dir / name
        shutil.make_archive(str(output_base), "zip", root_dir=dist, base_dir=source.name)
        return output_base.with_suffix(".zip")

    source = dist / "Mountlet"
    output = output_dir / f"{name}.tar.gz"
    with tarfile.open(output, "w:gz", compresslevel=6) as archive:
        archive.add(source, arcname=source.name, recursive=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive a verified Mountlet native bundle.")
    parser.add_argument("--name", required=True, help="Artifact base name.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)

    output = archive_bundle(
        args.name,
        args.dist_dir.resolve(),
        args.output_dir.resolve(),
        platform.system(),
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Archive was not created: {output}")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
