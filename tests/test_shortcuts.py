from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet import shortcuts
from mountlet.settings import DEFAULT_SHORTCUTS, AppSettings


class FakeQKeySequence:
    class SequenceFormat:
        PortableText = object()

    _text_by_value: dict[int, str] = {}

    def __init__(self, value: int) -> None:
        self.value = value

    def toString(self, _format: object) -> str:
        return self._text_by_value[self.value]


def _fake_qt() -> SimpleNamespace:
    key = SimpleNamespace(
        Key_Control=0x01000021,
        Key_Shift=0x01000020,
        Key_Alt=0x01000023,
        Key_Meta=0x01000022,
        Key_Home=0x01000010,
        Key_Up=0x01000013,
        Key_Return=0x01000004,
        Key_Right=0x01000014,
    )
    modifier = SimpleNamespace(
        NoModifier=0,
        ControlModifier=0x04000000,
        AltModifier=0x08000000,
        ShiftModifier=0x02000000,
        MetaModifier=0x10000000,
    )
    FakeQKeySequence._text_by_value = {
        key.Key_Home | modifier.AltModifier: "Alt+Home",
        key.Key_Up | modifier.ShiftModifier: "Shift+Up",
        key.Key_Return: "Return",
        key.Key_Right: "Right",
    }
    return SimpleNamespace(
        Qt=SimpleNamespace(Key=key, KeyboardModifier=modifier),
        QKeySequence=FakeQKeySequence,
    )


class ShortcutTests(unittest.TestCase):
    def test_matches_configured_shortcut(self):
        qt = _fake_qt()
        event = SimpleNamespace(
            key=lambda: qt.Qt.Key.Key_Home,
            modifiers=lambda: qt.Qt.KeyboardModifier.AltModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "browser_root": ("Alt+Home",)})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "browser_root"))

    def test_enter_alias_matches_return_key(self):
        qt = _fake_qt()
        event = SimpleNamespace(
            key=lambda: qt.Qt.Key.Key_Return,
            modifiers=lambda: qt.Qt.KeyboardModifier.NoModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "browser_open": ("Enter",)})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "browser_open"))

    def test_matches_alternative_shortcut(self):
        qt = _fake_qt()
        event = SimpleNamespace(
            key=lambda: qt.Qt.Key.Key_Right,
            modifiers=lambda: qt.Qt.KeyboardModifier.NoModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "remote_enter_browser": ("Return", "Space", "Right")})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "remote_enter_browser"))

    def test_matches_shift_arrow_from_integer_key_event(self):
        qt = _fake_qt()
        event = SimpleNamespace(
            key=lambda: qt.Qt.Key.Key_Up,
            modifiers=lambda: qt.Qt.KeyboardModifier.ShiftModifier,
        )
        settings = AppSettings(shortcuts={**DEFAULT_SHORTCUTS, "remote_move_up": ("Shift+Up",)})

        with mock.patch.object(shortcuts, "load_app_settings", return_value=settings):
            self.assertTrue(shortcuts.matches_shortcut(qt, event, "remote_move_up"))


if __name__ == "__main__":
    unittest.main()
