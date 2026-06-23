from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from mountlet import shortcuts
from mountlet.settings import DEFAULT_SHORTCUTS, AppSettings


class ShortcutTests(unittest.TestCase):
    def test_matches_configured_shortcut(self):
        qt = SimpleNamespace(Qt=Qt, QKeySequence=QKeySequence)
        event = SimpleNamespace(
            key=lambda: Qt.Key.Key_Home,
            modifiers=lambda: Qt.KeyboardModifier.AltModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "browser_root": ("Alt+Home",)})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "browser_root"))

    def test_enter_alias_matches_return_key(self):
        qt = SimpleNamespace(Qt=Qt, QKeySequence=QKeySequence)
        event = SimpleNamespace(
            key=lambda: Qt.Key.Key_Return,
            modifiers=lambda: Qt.KeyboardModifier.NoModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "browser_open": ("Enter",)})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "browser_open"))

    def test_matches_alternative_shortcut(self):
        qt = SimpleNamespace(Qt=Qt, QKeySequence=QKeySequence)
        event = SimpleNamespace(
            key=lambda: Qt.Key.Key_Right,
            modifiers=lambda: Qt.KeyboardModifier.NoModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "remote_enter_browser": ("Return", "Space", "Right")})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "remote_enter_browser"))


if __name__ == "__main__":
    unittest.main()
