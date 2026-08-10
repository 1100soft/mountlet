from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.ui_zoom import ApplicationZoom, clamp_zoom_steps, scaled_metric, zoom_factor
from mountlet.ui_geometry import exclude_tray_panel


class UiZoomTests(unittest.TestCase):
    def test_zoom_steps_are_bounded(self):
        self.assertEqual(clamp_zoom_steps(-20), -4)
        self.assertEqual(clamp_zoom_steps("3"), 3)
        self.assertEqual(clamp_zoom_steps(20), 6)
        self.assertEqual(clamp_zoom_steps("invalid"), 0)

    def test_zoom_factor_uses_ten_percent_steps(self):
        self.assertEqual(zoom_factor(0), 1.0)
        self.assertEqual(zoom_factor(3), 1.3)
        self.assertEqual(zoom_factor(-4), 0.6)

    def test_metrics_scale_from_the_same_baseline(self):
        self.assertEqual(scaled_metric(40, 2), 48)
        self.assertEqual(scaled_metric(40, -2), 32)
        self.assertEqual(scaled_metric(0, 6), 0)

    def test_right_panel_boundary_uses_near_tray_icon_edge(self):
        self.assertEqual(
            exclude_tray_panel((0, 0, 1920, 1080), (1880, 200, 24, 24), "right"),
            (0, 0, 1872, 1080),
        )

    def test_existing_panel_exclusion_is_not_applied_twice(self):
        self.assertEqual(
            exclude_tray_panel((0, 0, 1872, 1080), (1880, 200, 24, 24), "right"),
            (0, 0, 1872, 1080),
        )

    def test_grid_constraints_scale_from_immutable_baselines(self):
        class Grid:
            def __init__(self):
                self.columns = [24, 126, 0]
                self.rows = [40]

            def columnCount(self):
                return len(self.columns)

            def columnMinimumWidth(self, index):
                return self.columns[index]

            def setColumnMinimumWidth(self, index, value):
                self.columns[index] = value

            def rowCount(self):
                return len(self.rows)

            def rowMinimumHeight(self, index):
                return self.rows[index]

            def setRowMinimumHeight(self, index, value):
                self.rows[index] = value

        grid = Grid()

        ApplicationZoom._scale_grid_constraints(grid, 5)
        self.assertEqual(grid.columns, [36, 189, 0])
        self.assertEqual(grid.rows, [60])

        ApplicationZoom._scale_grid_constraints(grid, -2)
        self.assertEqual(grid.columns, [19, 101, 0])
        self.assertEqual(grid.rows, [32])

    def test_repeated_zoom_requests_render_only_the_final_level(self):
        zoom = object.__new__(ApplicationZoom)
        zoom.steps = 0
        zoom._requested_steps = None
        zoom._change_timer = mock.Mock()
        zoom._apply_app_font = mock.Mock()
        zoom.apply_all_windows = mock.Mock()
        zoom._changed = mock.Mock()

        self.assertTrue(zoom.zoom_in())
        self.assertTrue(zoom.zoom_in())
        self.assertTrue(zoom.zoom_out())
        self.assertEqual(zoom._requested_steps, 1)
        zoom.apply_all_windows.assert_not_called()

        zoom._commit_requested_steps()

        self.assertEqual(zoom.steps, 1)
        zoom.apply_all_windows.assert_called_once_with(resize_ratio=1.1)
        zoom._changed.assert_called_once_with(1)

    @mock.patch("mountlet.ui_zoom.scale_widget_icon", return_value=True)
    def test_geometry_managed_top_level_is_not_fixed_size_scaled(self, _scale_icon):
        zoom = object.__new__(ApplicationZoom)
        zoom.steps = 6
        zoom.qt = mock.Mock()
        zoom._scale_style_sheet = mock.Mock()
        zoom._scale_fixed_dimensions = mock.Mock()
        widget = mock.Mock()
        widget._mountlet_zoom_geometry_managed = True
        widget.metaObject.return_value.className.return_value = "BrowserWindow"
        widget.font.side_effect = RuntimeError

        zoom._scale_widget(widget)

        zoom._scale_fixed_dimensions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
