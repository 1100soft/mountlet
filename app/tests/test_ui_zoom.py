from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mountlet.ui_zoom import (
    ApplicationZoom,
    MAX_ZOOM_STEPS,
    MIN_ZOOM_STEPS,
    clamp_zoom_steps,
    metric_at_level,
    metric_levels,
    scaled_metric,
    zoom_factor,
)
from mountlet.cloud_browser_ui import CompactCloudBrowser
from mountlet.cloud_browser import BrowserEntry
from mountlet.tray import _load_qt_bindings
from mountlet.ui_geometry import (
    FILE_LIST_FRAME_PADDING,
    FILE_LIST_HEADER_HEIGHT,
    FILE_LIST_ROW_HEIGHT,
    exclude_tray_panel,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # The core-only install intentionally has no Qt runtime.
    QtCore = None
    QtGui = None
    QtWidgets = None


class UiZoomTests(unittest.TestCase):
    @unittest.skipUnless(QtWidgets is not None, "PySide6 is not installed")
    def test_file_icons_keep_source_color_in_selected_state(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = _load_qt_bindings()
        pixmap = QtGui.QPixmap(24, 24)
        source = QtGui.QColor("#00ff00")
        pixmap.fill(source)

        icon = browser._untinted_pixmap_icon(pixmap)
        selected = icon.pixmap(
            QtCore.QSize(24, 24),
            QtGui.QIcon.Mode.Selected,
            QtGui.QIcon.State.Off,
        )

        self.assertEqual(selected.toImage().pixelColor(12, 12), source)

    @unittest.skipUnless(QtWidgets is not None, "PySide6 is not installed")
    def test_inline_rename_selects_basename_after_editor_focus(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        browser = object.__new__(CompactCloudBrowser)
        browser.qt = _load_qt_bindings()
        browser._zoom_steps = 0
        tree = QtWidgets.QTreeWidget()
        tree.setColumnCount(3)
        browser.tree = tree
        delegate = browser._make_file_row_delegate()
        tree.setItemDelegate(delegate)
        item = QtWidgets.QTreeWidgetItem(["report.final.pdf", "", ""])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, BrowserEntry("report.final.pdf", "report.final.pdf", False))
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)
        tree.addTopLevelItem(item)
        tree.show()
        tree.editItem(item, 0)
        app.processEvents()
        try:
            editor = tree.findChild(QtWidgets.QLineEdit)
            self.assertIsNotNone(editor)
            self.assertEqual(editor.selectedText(), "report.final")
        finally:
            tree.close()

    @unittest.skipUnless(QtWidgets is not None, "PySide6 is not installed")
    @mock.patch("mountlet.cloud_browser_ui.CloudBrowserBackend")
    def test_production_qt_namespace_constructs_file_browser(self, _backend_type):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        qt = _load_qt_bindings()
        zoom_steps = 3
        application_zoom = ApplicationZoom(qt, app, zoom_steps)
        parent = qt.QMainWindow()
        browser = CompactCloudBrowser(
            qt,
            parent,
            remotes=lambda: [],
            notify=lambda *_args: None,
            open_mount=lambda *_args: None,
            file_manager_label=lambda: "Files",
            embedded=False,
            zoom_steps=zoom_steps,
        )
        try:
            browser.window.show()
            app.processEvents()
            expected_header = metric_at_level(metric_levels(FILE_LIST_HEADER_HEIGHT), zoom_steps)
            expected_icon = metric_at_level(metric_levels(30), zoom_steps)
            application_zoom.apply_all_windows()
            app.processEvents()
            self.assertIs(browser.tree.itemDelegate(), browser._file_row_delegate)
            self.assertEqual(browser.tree.header().height(), expected_header)
            self.assertEqual(browser.tree.iconSize().height(), expected_icon)
            self.assertEqual(
                browser.tree.verticalScrollBarPolicy(),
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn,
            )
        finally:
            browser.dispose()
            browser.window.close()
            parent.close()

    @unittest.skipUnless(QtWidgets is not None, "PySide6 is not installed")
    def test_file_list_integer_height_has_no_scrollbar_at_every_zoom(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        browser = object.__new__(CompactCloudBrowser)
        # Use the production compatibility namespace so a missing Qt export
        # fails this release-critical test exactly as it would at startup.
        browser.qt = _load_qt_bindings()
        browser._zoom_steps = 0
        tree = QtWidgets.QTreeWidget()
        browser.tree = tree
        tree._mountlet_zoom_icon_managed = True
        tree.setColumnCount(3)
        tree.header()._mountlet_zoom_geometry_managed = True
        tree.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.setFixedWidth(540)
        delegate = browser._make_file_row_delegate()
        tree.setItemDelegate(delegate)
        row_levels = metric_levels(FILE_LIST_ROW_HEIGHT)
        header_levels = metric_levels(FILE_LIST_HEADER_HEIGHT)

        try:
            for steps in range(MIN_ZOOM_STEPS, MAX_ZOOM_STEPS + 1):
                browser._zoom_steps = steps
                for item_count, icon_source_size in ((5, 16), (9, 96)):
                    tree.clear()
                    row_height = metric_at_level(row_levels, steps)
                    header_height = metric_at_level(header_levels, steps)
                    tree.header().setFixedHeight(header_height)
                    tree.setIconSize(QtCore.QSize(max(1, row_height - 6), max(1, row_height - 6)))
                    for index in range(item_count):
                        item = QtWidgets.QTreeWidgetItem(
                            [str(index), "1.0 MB", "2026-08-10 12:34"]
                        )
                        pixmap = QtGui.QPixmap(icon_source_size, icon_source_size)
                        pixmap.fill(QtGui.QColor("#38bdf8"))
                        item.setIcon(0, QtGui.QIcon(pixmap))
                        tree.addTopLevelItem(item)
                    expected_height = (
                        header_height
                        + item_count * row_height
                        + FILE_LIST_FRAME_PADDING
                    )
                    tree.setFixedHeight(expected_height)
                    tree.show()
                    tree.doItemsLayout()
                    app.processEvents()

                    rendered_heights = [
                        tree.visualItemRect(tree.topLevelItem(index)).height()
                        for index in range(item_count)
                    ]
                    self.assertEqual(rendered_heights, [row_height] * item_count)
                    self.assertEqual(tree.viewport().height(), item_count * row_height)
                    self.assertEqual(
                        tree.verticalScrollBar().maximum(),
                        0,
                        f"unexpected scrollbar at zoom step {steps}",
                    )
        finally:
            tree.close()

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
