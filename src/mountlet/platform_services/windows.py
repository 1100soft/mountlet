from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .base import OperationResult, PlatformServices, UserDirectories


class WindowsPlatformServices(PlatformServices):
    system_name = "Windows"

    def user_directories(self, app_name: str) -> UserDirectories:
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return UserDirectories(roaming / app_name, local / app_name / "State", local / app_name / "Cache")

    def default_rclone_config(self) -> Path:
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return roaming / "rclone" / "rclone.conf"

    def default_mount_base(self) -> Path:
        return Path.home() / "Mountlet"

    def rclone_executable_names(self) -> tuple[str, ...]:
        return ("rclone.exe", "rclone")

    def rclone_candidates(self) -> tuple[Path, ...]:
        home = Path(os.environ.get("USERPROFILE", Path.home()))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        chocolatey = Path(os.environ.get("ChocolateyInstall", "C:/ProgramData/chocolatey"))
        scoop = Path(os.environ.get("SCOOP", home / "scoop"))
        return (
            local / "Microsoft" / "WinGet" / "Links" / "rclone.exe",
            chocolatey / "bin" / "rclone.exe",
            scoop / "shims" / "rclone.exe",
            program_files / "rclone" / "rclone.exe",
            local / "rclone" / "rclone.exe",
            home / "rclone" / "rclone.exe",
            Path("C:/rclone/rclone.exe"),
        )

    def apply_private_permissions(self, path: Path) -> None:
        # Windows applies ACLs inherited from the per-user application directory.
        return

    def mount_process_options(self) -> dict[str, int]:
        return {"creationflags": 0x08000000 | 0x00000008 | 0x00000200}

    def is_mounted(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        try:
            result = subprocess.run(
                ("fsutil", "reparsepoint", "query", path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def prepare_mount_path(self, path: str) -> OperationResult:
        mountpoint = Path(path)
        try:
            mountpoint.parent.mkdir(parents=True, exist_ok=True)
            if mountpoint.exists() and not self.is_mounted(path):
                if not mountpoint.is_dir() or any(mountpoint.iterdir()):
                    return OperationResult(
                        False,
                        f"Mount folder {path} is not empty. Choose an empty folder or move its files first.",
                    )
                mountpoint.rmdir()
        except OSError as exc:
            return OperationResult(False, f"Cannot prepare Windows mount folder {path}: {exc}")
        return OperationResult(True)

    def unmount(self, path: str, pid: int | None = None) -> OperationResult:
        if pid:
            try:
                subprocess.run(
                    ("taskkill", "/PID", str(pid), "/T", "/F"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            Path(path).rmdir()
        except (FileNotFoundError, OSError):
            pass
        return OperationResult(not self.is_mounted(path), "The mount process could not be stopped.")

    def autostart_path(self, app_name: str) -> Path:
        # This synthetic path supports status checks without importing winreg on other systems.
        return self.user_directories(app_name).state / "autostart.enabled"

    def is_start_at_login_enabled(self, app_name: str) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "Mountlet")
            return True
        except (ImportError, FileNotFoundError, OSError):
            return False

    def set_start_at_login(
        self,
        app_name: str,
        enabled: bool,
        *,
        command: Sequence[str],
        destination: Path | None = None,
    ) -> None:
        if destination is not None:
            if enabled:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(subprocess.list2cmdline(command), encoding="utf-8")
            else:
                destination.unlink(missing_ok=True)
            return
        import winreg

        access = winreg.KEY_SET_VALUE
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            access,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, "Mountlet", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
            else:
                try:
                    winreg.DeleteValue(key, "Mountlet")
                except FileNotFoundError:
                    pass

    def prerequisite_guidance(self) -> tuple[str, ...]:
        return ("Install rclone.", "Install WinFsp to enable filesystem mounts.")

    def mount_driver_available(self) -> bool:
        tool_names = ("fsptool-x64.exe", "fsptool-x86.exe", "fsptool-a64.exe")
        if any(shutil.which(name) for name in tool_names):
            return True

        roots = {
            Path(os.environ.get("ProgramFiles", "C:/Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
            Path(os.environ.get("ProgramW6432", "C:/Program Files")),
        }
        return any((root / "WinFsp" / "bin" / name).exists() for root in roots for name in tool_names)
