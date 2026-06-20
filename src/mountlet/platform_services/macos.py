from __future__ import annotations

import plistlib
import shutil
from pathlib import Path
from typing import Sequence

from .base import PlatformServices, UserDirectories


class MacOSPlatformServices(PlatformServices):
    system_name = "Darwin"

    def user_directories(self, app_name: str) -> UserDirectories:
        home = Path.home()
        support = home / "Library" / "Application Support" / app_name
        return UserDirectories(support, support / "State", home / "Library" / "Caches" / app_name)

    def default_mount_base(self) -> Path:
        return Path.home() / "Mountlet"

    def unmount_commands(self, path: str) -> tuple[list[str], ...]:
        commands: list[list[str]] = []
        umount = shutil.which("umount")
        diskutil = shutil.which("diskutil")
        if umount:
            commands.append([umount, path])
        if diskutil:
            commands.extend(([diskutil, "unmount", path], [diskutil, "unmount", "force", path]))
        return tuple(commands)

    def autostart_path(self, app_name: str) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"com.mountlet.{app_name}.plist"

    def set_start_at_login(
        self,
        app_name: str,
        enabled: bool,
        *,
        command: Sequence[str],
        destination: Path | None = None,
    ) -> None:
        path = destination or self.autostart_path(app_name)
        if not enabled:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(
                {
                    "Label": f"com.mountlet.{app_name}",
                    "ProgramArguments": list(command),
                    "RunAtLoad": True,
                },
                handle,
            )

    def prerequisite_guidance(self) -> tuple[str, ...]:
        return ("Install rclone.", "Install macFUSE to enable filesystem mounts.")

    def mount_driver_available(self) -> bool:
        return Path("/Library/Filesystems/macfuse.fs").exists() or Path(
            "/Library/Filesystems/osxfuse.fs"
        ).exists()
