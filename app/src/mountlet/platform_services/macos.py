from __future__ import annotations

import json
import plistlib
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .base import OperationResult, PlatformServices, UserDirectories


class MacOSPlatformServices(PlatformServices):
    system_name = "Darwin"

    def user_directories(self, app_name: str) -> UserDirectories:
        home = Path.home()
        support = home / "Library" / "Application Support" / app_name
        return UserDirectories(support, support / "State", home / "Library" / "Caches" / app_name)

    def default_mount_base(self) -> Path:
        return Path.home() / "Mountlet" / "mounted"

    def rclone_candidates(self) -> tuple[Path, ...]:
        return (
            Path("/opt/homebrew/bin/rclone"),
            Path("/usr/local/bin/rclone"),
        )

    def open_external_terminal(self, command: Sequence[str], *, title: str = "Mountlet") -> OperationResult:
        shell_command = " ".join(shlex.quote(part) for part in command)
        script = (
            'tell application "Terminal"\n'
            "  activate\n"
            f"  do script {json.dumps(shell_command)}\n"
            "end tell"
        )
        try:
            subprocess.Popen(["osascript", "-e", script], close_fds=True)
        except OSError as exc:
            return OperationResult(False, f"Could not open Terminal: {exc}")
        return OperationResult(True)

    def unmount_commands(self, path: str) -> tuple[list[str], ...]:
        commands: list[list[str]] = []
        umount = shutil.which("umount")
        diskutil = shutil.which("diskutil")
        if umount:
            commands.append([umount, path])
        if diskutil:
            commands.extend(([diskutil, "unmount", path], [diskutil, "unmount", "force", path]))
        return tuple(commands)

    def is_mounted(self, path: str) -> bool:
        if super().is_mounted(path):
            return True
        try:
            result = subprocess.run(
                ["/sbin/mount"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        mount_path = str(path).replace("\\", "/").rstrip("/")
        return any(f" on {mount_path} (" in line for line in result.stdout.splitlines())

    def mount_start_timeout_seconds(self) -> float:
        return 30.0

    def mount_timeout_guidance(self) -> str:
        return (
            "rclone was still running, but macOS did not report the folder as mounted. "
            "If this is the first macFUSE use after installation, allow macFUSE in "
            "System Settings, then restart Mountlet and try again."
        )

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
