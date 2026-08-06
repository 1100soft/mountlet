from __future__ import annotations

import unittest
from unittest import mock

from mountlet import ui_icons


class UiIconTests(unittest.TestCase):
    def test_refresh_widget_palette_reapplies_stylesheet_and_repolishes(self):
        style = mock.Mock()
        widget = mock.Mock()
        widget.styleSheet.return_value = "color: palette(text);"
        widget.style.return_value = style
        widget.findChildren.return_value = []
        qt = mock.Mock(QWidget=object)

        ui_icons.refresh_widget_palette(qt, widget)

        self.assertEqual(
            widget.setStyleSheet.call_args_list,
            [mock.call(""), mock.call("color: palette(text);")],
        )
        style.unpolish.assert_called_once_with(widget)
        style.polish.assert_called_once_with(widget)
        widget.update.assert_called_once_with()

    def test_refresh_widget_palette_does_not_repolish_every_child(self):
        root_style = mock.Mock()
        child_style = mock.Mock()
        child = mock.Mock()
        child.styleSheet.return_value = ""
        child.style.return_value = child_style
        root = mock.Mock()
        root.styleSheet.return_value = ""
        root.style.return_value = root_style
        root.findChildren.return_value = [child]
        qt = mock.Mock(QWidget=object)

        ui_icons.refresh_widget_palette(qt, root)

        root_style.unpolish.assert_called_once_with(root)
        root_style.polish.assert_called_once_with(root)
        child_style.unpolish.assert_not_called()
        child_style.polish.assert_not_called()

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

    def test_refresh_palette_derived_icon_keeps_color_dynamic(self):
        class Button:
            def __init__(self) -> None:
                self.properties = {
                    "mountletIconName": "ui-copy",
                    "mountletIconFallback": "Copy",
                    "mountletIconSize": 18,
                    "mountletIconColor": "",
                }
                self.children = lambda: []

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
            with mock.patch.object(ui_icons, "_button_text_color", return_value="#111827"):
                ui_icons.refresh_widget_icons(qt, button)

        self.assertEqual(button.property("mountletIconColor"), "")

    def test_palette_change_event_recolors_dynamic_icon(self):
        class QObject:
            def __init__(self, _parent=None) -> None:
                pass

        class QEvent:
            class Type:
                ApplicationPaletteChange = 1
                PaletteChange = 2
                StyleChange = 3

        class QTimer:
            @staticmethod
            def singleShot(_delay: int, callback) -> None:
                callback()

        class Event:
            def type(self) -> int:
                return QEvent.Type.PaletteChange

        class Button:
            def __init__(self) -> None:
                self.properties = {}
                self.event_filter = None

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

            def installEventFilter(self, event_filter) -> None:
                self.event_filter = event_filter

        button = Button()
        qt = mock.Mock(QObject=QObject, QEvent=QEvent, QTimer=QTimer)
        qt.QSize.side_effect = lambda width, height: (width, height)
        colors = iter(("#111827", "#f9fafb"))

        with mock.patch.object(ui_icons, "mountlet_icon", return_value=object()) as icon, mock.patch.object(
            ui_icons,
            "_button_text_color",
            side_effect=lambda _button: next(colors),
        ):
            ui_icons.apply_button_icon(qt, button, "ui-copy", size=18)
            button.event_filter.eventFilter(button, Event())

        self.assertEqual(icon.call_args_list[0].kwargs["color"], "#111827")
        self.assertEqual(icon.call_args_list[1].kwargs["color"], "#f9fafb")
        self.assertEqual(button.property("mountletIconColor"), "")


if __name__ == "__main__":
    unittest.main()
