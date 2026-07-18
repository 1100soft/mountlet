from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.badged_button import create_badged_button


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def emit(self) -> None:
        if self.callback is not None:
            self.callback(False)


class _Action:
    def __init__(self, label: str) -> None:
        self.label = label
        self.tooltip = ""
        self.enabled = True
        self.triggered = _Signal()

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setIcon(self, icon) -> None:
        self.icon = icon


class _Menu:
    last = None

    def __init__(self, _parent) -> None:
        self.actions = []
        self.popup_at = None
        type(self).last = self

    def clear(self) -> None:
        self.actions.clear()

    def addAction(self, label: str) -> _Action:
        action = _Action(label)
        self.actions.append(action)
        return action

    def addSeparator(self) -> None:
        self.actions.append(None)

    def popup(self, position) -> None:
        self.popup_at = position


class _PushButton:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self._tooltip = ""
        self._enabled = True
        self.clicked_count = 0

    def toolTip(self) -> str:
        return self._tooltip

    def setToolTip(self, tooltip: str) -> None:
        self._tooltip = tooltip

    def click(self) -> None:
        self.clicked_count += 1

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def isEnabled(self) -> bool:
        return self._enabled

    def update(self) -> None:
        pass

    def contextMenuEvent(self, _event) -> None:
        pass


class _ContextEvent:
    def __init__(self) -> None:
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True

    def globalPos(self):
        return (42, 24)


class BadgedButtonTests(unittest.TestCase):
    def test_context_menu_uses_tooltip_as_default_action_then_extra_options(self):
        qt = SimpleNamespace(QPushButton=_PushButton, QMenu=_Menu)
        button = create_badged_button(qt)
        button.setToolTip("Open settings\nShortcut: S")
        extra_calls = []
        button.addContextMenuOption("View online", lambda: extra_calls.append("online"))

        event = _ContextEvent()
        button.contextMenuEvent(event)
        menu = _Menu.last

        self.assertTrue(event.accepted)
        self.assertEqual([action.label if action else None for action in menu.actions], ["Open settings", None, "View online"])
        self.assertEqual(menu.popup_at, (42, 24))
        self.assertEqual(button.clicked_count, 0)

        menu.actions[0].triggered.emit()
        menu.actions[2].triggered.emit()
        self.assertEqual(button.clicked_count, 1)
        self.assertEqual(extra_calls, ["online"])

    def test_context_option_can_be_disabled_dynamically(self):
        qt = SimpleNamespace(QPushButton=_PushButton, QMenu=_Menu)
        button = create_badged_button(qt)
        button.addContextMenuOption("Unavailable", lambda: None, enabled=lambda: False)

        button.contextMenuEvent(_ContextEvent())

        self.assertFalse(_Menu.last.actions[0].enabled)

    def test_context_option_accepts_an_icon(self):
        qt = SimpleNamespace(QPushButton=_PushButton, QMenu=_Menu)
        button = create_badged_button(qt)
        icon = object()
        button.addContextMenuOption("View all", lambda: None, icon=lambda: icon)

        button.contextMenuEvent(_ContextEvent())

        self.assertIs(_Menu.last.actions[0].icon, icon)


if __name__ == "__main__":
    unittest.main()
