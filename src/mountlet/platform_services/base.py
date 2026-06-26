from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class UserDirectories:
    config: Path
    state: Path
    cache: Path


@dataclass(frozen=True)
class OperationResult:
    success: bool
    detail: str = ""


class PlatformServices:
    """Base contract for behavior that differs between operating systems."""

    system_name = "Unknown"

    def user_directories(self, app_name: str) -> UserDirectories:
        raise NotImplementedError

    def default_rclone_config(self) -> Path:
        return Path.home() / ".config" / "rclone" / "rclone.conf"

    def default_mount_base(self) -> Path:
        return Path.home() / "Mountlet" / "mounted"

    def legacy_mount_bases(self) -> tuple[Path, ...]:
        return ()

    def rclone_executable_names(self) -> tuple[str, ...]:
        return ("rclone",)

    def rclone_candidates(self) -> tuple[Path, ...]:
        return ()

    def find_rclone(self) -> str | None:
        env_path = os.environ.get("RCLONE_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.exists():
                return str(candidate)
        for name in self.rclone_executable_names():
            found = shutil.which(name)
            if found:
                return found
        for candidate in self.rclone_candidates():
            if candidate.exists():
                return str(candidate)
        return None

    def apply_private_permissions(self, path: Path) -> None:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def mount_process_options(self) -> dict[str, Any]:
        return {"close_fds": True, "start_new_session": True}

    def command_process_options(self) -> dict[str, Any]:
        """Options for short-lived, non-interactive child commands."""
        return {}

    def is_mounted(self, path: str) -> bool:
        return os.path.ismount(path)

    def prepare_mount_path(self, path: str) -> OperationResult:
        mountpoint = Path(path)
        try:
            mountpoint.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return OperationResult(False, f"Cannot create mount folder {path}: {exc}")
        if not os.access(mountpoint, os.W_OK | os.X_OK):
            return OperationResult(False, f"Mount folder {path} is not writable.")
        if not self.is_mounted(path):
            try:
                if any(mountpoint.iterdir()):
                    return OperationResult(
                        False,
                        f"Mount folder {path} is not empty. Choose an empty folder or move its files first.",
                    )
            except OSError as exc:
                return OperationResult(False, f"Cannot inspect mount folder {path}: {exc}")
        return OperationResult(True)

    def unmount_commands(self, path: str) -> tuple[list[str], ...]:
        command = shutil.which("umount")
        if not command:
            return ()
        return ([command, path], [command, "-l", path])

    def terminate_pid(self, pid: int) -> None:
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass

    def unmount(self, path: str, pid: int | None = None) -> OperationResult:
        commands = self.unmount_commands(path)
        if not commands:
            return OperationResult(False, "No supported unmount command was found.")
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0:
                if pid:
                    self.terminate_pid(pid)
                return OperationResult(True)
        return OperationResult(False, "The mount is busy or could not be released.")

    def autostart_path(self, app_name: str) -> Path:
        raise NotImplementedError

    def is_start_at_login_enabled(self, app_name: str) -> bool:
        try:
            return self.autostart_path(app_name).exists()
        except NotImplementedError:
            return False

    def set_start_at_login(
        self,
        app_name: str,
        enabled: bool,
        *,
        command: Sequence[str],
        destination: Path | None = None,
    ) -> None:
        raise NotImplementedError

    def graphical_session_available(self, environment: Mapping[str, str] | None = None) -> OperationResult:
        return OperationResult(True)

    def mount_driver_available(self) -> bool:
        return True

    def mount_driver_config_paths(self) -> tuple[Path, ...]:
        return ()

    def prerequisite_guidance(self) -> tuple[str, ...]:
        return ("Install rclone and the filesystem driver required by your operating system.",)
