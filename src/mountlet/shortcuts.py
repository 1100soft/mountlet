from __future__ import annotations

from typing import Any

from .settings import DEFAULT_SHORTCUTS, load_app_settings


def shortcut_text(action: str) -> str:
    return load_app_settings().shortcuts.get(action, DEFAULT_SHORTCUTS.get(action, ""))


def matches_shortcut(qt: Any, event: Any, action: str) -> bool:
    expected = shortcut_text(action)
    if not expected:
        return False
    sequence = _event_sequence(qt, event)
    if not sequence:
        return False
    return _normalized(sequence) == _normalized(expected)


def _event_sequence(qt: Any, event: Any) -> str:
    key = event.key()
    modifier_keys = {
        qt.Qt.Key.Key_Control,
        qt.Qt.Key.Key_Shift,
        qt.Qt.Key.Key_Alt,
        qt.Qt.Key.Key_Meta,
    }
    if key in modifier_keys:
        return ""
    value = key
    try:
        modifiers = event.modifiers()
    except Exception:
        modifiers = qt.Qt.KeyboardModifier.NoModifier
    for modifier in (
        qt.Qt.KeyboardModifier.ControlModifier,
        qt.Qt.KeyboardModifier.AltModifier,
        qt.Qt.KeyboardModifier.ShiftModifier,
        qt.Qt.KeyboardModifier.MetaModifier,
    ):
        if modifiers & modifier:
            value = value | modifier
    return qt.QKeySequence(value).toString(qt.QKeySequence.SequenceFormat.PortableText)


def _normalized(sequence: str) -> str:
    aliases = {
        "enter": "return",
        "escape": "esc",
        "ctrl": "control",
        "cmd": "meta",
        "command": "meta",
        "option": "alt",
    }
    parts = []
    for part in sequence.replace(" ", "").split("+"):
        normalized = part.casefold()
        parts.append(aliases.get(normalized, normalized))
    return "+".join(parts)


__all__ = ["matches_shortcut", "shortcut_text"]
