from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import Mapping, Sequence

from .base import OperationResult, PlatformServices, UserDirectories


class LinuxPlatformServices(PlatformServices):
    system_name = "Linux"

    def user_directories(self, app_name: str) -> UserDirectories:
        home = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser()
        state = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state")).expanduser()
        cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")).expanduser()
        return UserDirectories(config / app_name, state / app_name, cache / app_name)

    def default_mount_base(self) -> Path:
        return Path.home() / "cloud_mounts"

    def legacy_mount_bases(self) -> tuple[Path, ...]:
        return (Path.home() / "gdrive", Path.home() / "GDrive", Path("/mnt/gdrive"))

    def unmount_commands(self, path: str) -> tuple[list[str], ...]:
        command = shutil.which("fusermount3") or shutil.which("fusermount") or shutil.which("umount")
        if not command:
            return ()
        if Path(command).name == "umount":
            return ([command, path], [command, "-l", path])
        return ([command, "-u", path], [command, "-uz", path])

    def autostart_path(self, app_name: str) -> Path:
        config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
        return config / "autostart" / f"{app_name}.desktop"

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
        exec_value = " ".join(shlex.quote(part) for part in command)
        path.write_text(
            "\n".join(
                (
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=Mountlet",
                    "Comment=Mount cloud storage folders with Mountlet",
                    f"Exec={exec_value}",
                    "Terminal=false",
                    "X-GNOME-Autostart-enabled=true",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def graphical_session_available(self, environment: Mapping[str, str] | None = None) -> OperationResult:
        env = environment or os.environ
        if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
            return OperationResult(True)
        if env.get("XDG_RUNTIME_DIR") and env.get("DBUS_SESSION_BUS_ADDRESS"):
            return OperationResult(True)
        return OperationResult(False, "No graphical desktop session was detected.")

    def mount_driver_available(self) -> bool:
        return bool(shutil.which("fusermount3") or shutil.which("fusermount"))

    def mount_driver_config_paths(self) -> tuple[Path, ...]:
        return (Path("/etc/fuse.conf"),)

    def prerequisite_guidance(self) -> tuple[str, ...]:
        return (
            "Install rclone: sudo apt install rclone",
            "Install FUSE: sudo apt install fuse3",
        )
