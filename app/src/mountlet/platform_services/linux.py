from __future__ import annotations

import os
import posixpath
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from .base import OperationResult, PlatformServices, UserDirectories


class LinuxPlatformServices(PlatformServices):
    system_name = "Linux"

    def __init__(self) -> None:
        self._mount_cache_lock = threading.Lock()
        self._mount_cache_time = 0.0
        self._mount_cache: frozenset[str] = frozenset()

    def user_directories(self, app_name: str) -> UserDirectories:
        home = Path.home() if not all(
            name in os.environ for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")
        ) else None
        config = Path(os.environ["XDG_CONFIG_HOME"] if "XDG_CONFIG_HOME" in os.environ else home / ".config")
        state = Path(
            os.environ["XDG_STATE_HOME"] if "XDG_STATE_HOME" in os.environ else home / ".local" / "state"
        )
        cache = Path(os.environ["XDG_CACHE_HOME"] if "XDG_CACHE_HOME" in os.environ else home / ".cache")
        return UserDirectories(config / app_name, state / app_name, cache / app_name)

    def default_mount_base(self) -> Path:
        return Path.home() / "Mountlet" / "mounted"

    def legacy_mount_bases(self) -> tuple[Path, ...]:
        return (Path.home() / "gdrive", Path.home() / "GDrive", Path("/mnt/gdrive"))

    def is_mounted(self, path: str) -> bool:
        """Consult mountinfo so disconnected FUSE endpoints still count."""
        target = posixpath.normpath(str(path).replace("\\", "/"))
        return target in self._mounted_paths()

    def _mounted_paths(self) -> frozenset[str]:
        now = time.monotonic()
        with self._mount_cache_lock:
            if now - self._mount_cache_time < 0.1:
                return self._mount_cache
            try:
                lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
            except OSError:
                self._mount_cache_time = now
                self._mount_cache = frozenset()
                return self._mount_cache
            paths: set[str] = set()
            for line in lines:
                fields = line.split()
                if len(fields) < 5:
                    continue
                mountpoint = re.sub(
                    r"\\([0-7]{3})",
                    lambda match: chr(int(match.group(1), 8)),
                    fields[4],
                )
                paths.add(posixpath.normpath(mountpoint))
            self._mount_cache_time = now
            self._mount_cache = frozenset(paths)
            return self._mount_cache

    def invalidate_mount_cache(self) -> None:
        with self._mount_cache_lock:
            self._mount_cache_time = 0.0
            self._mount_cache = frozenset()

    def unmount_commands(self, path: str) -> tuple[list[str], ...]:
        command = shutil.which("fusermount3") or shutil.which("fusermount") or shutil.which("umount")
        if not command:
            return ()
        if Path(command).name == "umount":
            return ([command, path],)
        return ([command, "-u", path],)

    def detach_disconnected_mount(self, path: str) -> OperationResult:
        command = shutil.which("fusermount3") or shutil.which("fusermount")
        if not command:
            return OperationResult(False, "No FUSE unmount command was found.")
        try:
            result = subprocess.run(
                [command, "-u", "-z", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return OperationResult(False, f"Could not detach disconnected FUSE mount: {exc}")
        if result.returncode == 0:
            self.invalidate_mount_cache()
            return OperationResult(True)
        return OperationResult(False, "The disconnected FUSE mount could not be detached.")

    def autostart_path(self, app_name: str) -> Path:
        config = (
            Path(os.environ["XDG_CONFIG_HOME"])
            if "XDG_CONFIG_HOME" in os.environ
            else Path.home() / ".config"
        )
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

    def open_external_terminal(self, command: Sequence[str], *, title: str = "Mountlet") -> OperationResult:
        shell_command = " ".join(shlex.quote(part) for part in command)
        command_line = f"{shell_command}; printf '\\nPress Enter to close this terminal...'; read _"
        candidates: tuple[tuple[str, ...], ...] = (
            ("x-terminal-emulator", "-T", title, "-e", "sh", "-lc", command_line),
            ("konsole", "--title", title, "-e", "sh", "-lc", command_line),
            ("gnome-terminal", "--title", title, "--", "sh", "-lc", command_line),
            ("xfce4-terminal", "--title", title, "-e", f"sh -lc {shlex.quote(command_line)}"),
            ("xterm", "-T", title, "-e", "sh", "-lc", command_line),
        )
        for candidate in candidates:
            executable = shutil.which(candidate[0])
            if not executable:
                continue
            try:
                subprocess.Popen((executable, *candidate[1:]), close_fds=True)
            except OSError:
                continue
            return OperationResult(True)
        return OperationResult(False, "No terminal emulator was found. Install x-terminal-emulator, Konsole, GNOME Terminal, XFCE Terminal, or xterm.")

    def mount_driver_available(self) -> bool:
        return bool(shutil.which("fusermount3") or shutil.which("fusermount"))

    def mount_driver_config_paths(self) -> tuple[Path, ...]:
        return (Path("/etc/fuse.conf"),)

    def prerequisite_guidance(self) -> tuple[str, ...]:
        return (
            "Install rclone: sudo apt install rclone",
            "Install FUSE: sudo apt install fuse3",
        )
