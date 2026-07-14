from __future__ import annotations

import unittest
from unittest import mock

from mountlet import ui_icons


class UiIconTests(unittest.TestCase):
    def test_neutral_icon_recolors_only_334155_case_insensitively(self):
        svg = '<path stroke="#334155"/><path fill="#334155"/><path fill="#000000"/><path stroke="#334155AA"/>'

        recolored = ui_icons.NEUTRAL_ICON_RE.sub("#f9fafb", svg)

        self.assertIn('stroke="#f9fafb"', recolored)
        self.assertIn('fill="#f9fafb"', recolored)
        self.assertIn('fill="#000000"', recolored)
        self.assertIn('stroke="#334155AA"', recolored)

    def test_palette_derived_button_icon_color_stays_dynamic(self):
        class Button:
            def __init__(self) -> None:
                self.properties = {}

            def setIcon(self, _icon) -> None:
                pass

            def setIconSize(self, _size) -> None:
                pass

            def setText(self, _text: str) -> None:
                pass

            def setProperty(self, key: str, value) -> None:
                self.properties[key] = value

            def property(self, key: str):
                return self.properties.get(key)

        button = Button()
        qt = mock.Mock()
        qt.QSize.side_effect = lambda width, height: (width, height)

        with mock.patch.object(ui_icons, "mountlet_icon", return_value=object()):
            with mock.patch.object(ui_icons, "_button_text_color", return_value="#ffffff"):
                self.assertTrue(ui_icons.apply_button_icon(qt, button, "ui-copy", fallback_text="Copy", size=18))

        self.assertEqual(button.property("mountletIconColor"), "")


if __name__ == "__main__":
    unittest.main()
