from __future__ import annotations

import os
import sys
from typing import TextIO

from .base import PlatformServices


class ConsoleServices:
    def __init__(self, platform: PlatformServices, stream: TextIO | None = None) -> None:
        self.platform = platform
        self.stream = stream or sys.stdout

    def clear(self) -> None:
        if self.platform.system_name == "Windows":
            os.system("cls")
            return
        self.stream.write("\033[2J\033[H")
        self.stream.flush()

    def print_status(
        self,
        line_prefix: str,
        mounted_line: str,
        normal_line: str,
        mounted: bool,
        *,
        color: bool,
    ) -> None:
        text = mounted_line if mounted else normal_line
        if color and mounted and self.stream.isatty():
            print(f"{line_prefix}\033[92m{text}\033[0m", file=self.stream)
            return
        print(line_prefix + text, file=self.stream)
