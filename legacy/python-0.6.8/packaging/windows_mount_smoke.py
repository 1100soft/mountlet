from __future__ import annotations

import os
import tempfile
from pathlib import Path


def main() -> int:
    if os.name != "nt":
        return 0

    with tempfile.TemporaryDirectory(prefix="mountlet-windows-mount-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        expected = "Mountlet Windows mount smoke test"
        (source / "fixture.txt").write_text(expected, encoding="utf-8")

        config = root / "rclone.conf"
        config.write_text(
            "[Fixture]\n"
            "type = alias\n"
            f"remote = {source}\n",
            encoding="utf-8",
        )
        mount_base = root / "mounts"
        os.environ["RCLONE_CONFIG"] = str(config)
        os.environ["MOUNTLET_MOUNT_BASE"] = str(mount_base)

        from mountlet import core

        remote = core.load_remotes()[0]
        mounted, detail = core.mount_remote(remote)
        if not mounted:
            raise RuntimeError(detail)

        try:
            actual = (Path(remote.mount_path) / "fixture.txt").read_text(encoding="utf-8")
            if actual != expected:
                raise RuntimeError(f"Unexpected mounted file content: {actual!r}")
        finally:
            unmounted, unmount_detail = core.unmount_remote(remote)
            if not unmounted:
                raise RuntimeError(unmount_detail)

    print("Verified a Mountlet-managed rclone/WinFsp directory mount.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
