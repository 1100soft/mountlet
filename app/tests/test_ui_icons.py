from __future__ import annotations

import unittest

from mountlet import ui_icons


class UiIconTests(unittest.TestCase):
    def test_neutral_icon_recolors_only_334155_case_insensitively(self):
        svg = '<path stroke="#334155"/><path fill="#334155"/><path fill="#000000"/><path stroke="#334155AA"/>'

        recolored = ui_icons.NEUTRAL_ICON_RE.sub("#f9fafb", svg)

        self.assertIn('stroke="#f9fafb"', recolored)
        self.assertIn('fill="#f9fafb"', recolored)
        self.assertIn('fill="#000000"', recolored)
        self.assertIn('stroke="#334155AA"', recolored)


if __name__ == "__main__":
    unittest.main()
