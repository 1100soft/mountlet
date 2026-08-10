from __future__ import annotations

from contextlib import suppress
import re
from typing import Any, Callable

from .ui_icons import scale_widget_icon


MIN_ZOOM_STEPS = -4
MAX_ZOOM_STEPS = 6
ZOOM_STEP_SCALE = 0.1


def clamp_zoom_steps(value: int | float | str) -> int:
    try:
        steps = int(value)
    except (TypeError, ValueError):
        steps = 0
    return min(max(steps, MIN_ZOOM_STEPS), MAX_ZOOM_STEPS)


def zoom_factor(steps: int) -> float:
    return max(0.6, 1.0 + clamp_zoom_steps(steps) * ZOOM_STEP_SCALE)


def scaled_metric(value: int, steps: int) -> int:
    if value <= 0:
        return value
    return max(1, round(value * zoom_factor(steps)))


def metric_levels(value: int) -> tuple[int, ...]:
    """Return every discrete rendering of a base pixel metric."""
    if value <= 0:
        return tuple(value for _step in range(MIN_ZOOM_STEPS, MAX_ZOOM_STEPS + 1))
    return tuple(max(1, round(value * zoom_factor(step))) for step in range(MIN_ZOOM_STEPS, MAX_ZOOM_STEPS + 1))


def metric_at_level(levels: tuple[int, ...], steps: int) -> int:
    return levels[clamp_zoom_steps(steps) - MIN_ZOOM_STEPS]


class ApplicationZoom:
    """Apply one live zoom level to fonts, layouts, icons, and fixed controls."""

    def __init__(
        self,
        qt: Any,
        app: Any,
        steps: int = 0,
        *,
        changed: Callable[[int], None] | None = None,
    ) -> None:
        self.qt = qt
        self.app = app
        self.steps = clamp_zoom_steps(steps)
        self._changed = changed or (lambda _steps: None)
        self._base_app_point_size = self._point_size(app.font())
        self._pending_roots: set[int] = set()
        self._requested_steps: int | None = None
        self._change_timer = qt.QTimer()
        self._change_timer.setSingleShot(True)
        self._change_timer.timeout.connect(self._commit_requested_steps)
        self._filter = self._make_event_filter()
        app.installEventFilter(self._filter)
        self._apply_app_font()

    def set_changed_callback(self, callback: Callable[[int], None]) -> None:
        self._changed = callback

    def zoom_in(self) -> bool:
        current = self._requested_steps if self._requested_steps is not None else self.steps
        return self.set_steps(current + 1)

    def zoom_out(self) -> bool:
        current = self._requested_steps if self._requested_steps is not None else self.steps
        return self.set_steps(current - 1)

    def reset(self) -> bool:
        return self.set_steps(0)

    def set_steps(self, steps: int) -> bool:
        steps = clamp_zoom_steps(steps)
        current = self._requested_steps if self._requested_steps is not None else self.steps
        if steps == current:
            return False
        self._requested_steps = steps
        self._change_timer.start(300)
        return True

    def _commit_requested_steps(self) -> None:
        steps = self._requested_steps
        self._requested_steps = None
        if steps is None or steps == self.steps:
            return
        old_factor = zoom_factor(self.steps)
        self.steps = steps
        self._apply_app_font()
        self.apply_all_windows(resize_ratio=zoom_factor(steps) / old_factor)
        self._changed(steps)

    def apply_all_windows(self, *, resize_ratio: float | None = None) -> None:
        for window in list(self.app.topLevelWidgets()):
            self.apply_window(window)
            if resize_ratio is not None:
                self._resize_window(window, resize_ratio)

    def apply_window(self, root: Any) -> None:
        if root is None:
            return
        widgets = [root]
        with suppress(Exception):
            widgets.extend(root.findChildren(self.qt.QWidget))
        for widget in widgets:
            self._scale_widget(widget)
            self._scale_layout(getattr(widget, "layout", lambda: None)())
        with suppress(Exception):
            root.updateGeometry()

    def _make_event_filter(self) -> Any:
        outer = self

        class ZoomEventFilter(self.qt.QObject):
            def eventFilter(self, watched: Any, event: Any) -> bool:
                event_type = event.type()
                if event_type == outer.qt.QEvent.Type.KeyPress and outer._handle_key(event):
                    return True
                if event_type == outer.qt.QEvent.Type.Show:
                    outer._queue_shown_widget(watched)
                return False

        return ZoomEventFilter()

    def _handle_key(self, event: Any) -> bool:
        modifiers = event.modifiers()
        if not bool(modifiers & self.qt.Qt.KeyboardModifier.ControlModifier):
            return False
        key = event.key()
        keys = self.qt.Qt.Key
        handled = False
        if key in {getattr(keys, "Key_Plus", None), getattr(keys, "Key_Equal", None)}:
            handled = self.zoom_in()
        elif key == getattr(keys, "Key_Minus", None):
            handled = self.zoom_out()
        elif key == getattr(keys, "Key_0", None):
            handled = self.reset()
        if handled:
            with suppress(Exception):
                event.accept()
        return handled

    def _queue_shown_widget(self, widget: Any) -> None:
        with suppress(Exception):
            root = widget.window()
            if self.steps == 0 and root is not widget:
                return
            identity = id(root)
            if identity in self._pending_roots:
                return
            self._pending_roots.add(identity)
            self.qt.QTimer.singleShot(0, lambda target=root, key=identity: self._apply_shown_widget(target, key))

    def _apply_shown_widget(self, root: Any, identity: int) -> None:
        self._pending_roots.discard(identity)
        self.apply_window(root)

    def _apply_app_font(self) -> None:
        font = self.app.font()
        font.setPointSizeF(max(6.0, self._base_app_point_size * zoom_factor(self.steps)))
        self.app.setFont(font)

    def _scale_widget(self, widget: Any) -> None:
        factor = zoom_factor(self.steps)
        with suppress(Exception):
            font = widget.font()
            base = getattr(widget, "_mountlet_zoom_base_point_size", None)
            if base is None:
                base = self._point_size(font) / factor
                setattr(widget, "_mountlet_zoom_base_point_size", base)
            font.setPointSizeF(max(6.0, float(base) * factor))
            widget.setFont(font)

        self._scale_style_sheet(widget)

        class_name = widget.metaObject().className() if hasattr(widget, "metaObject") else ""
        scalable_fixed = not getattr(widget, "_mountlet_zoom_geometry_managed", False) and not any(
            name in class_name
            for name in ("Tree", "List", "Table", "Text", "Scroll", "MainWindow", "Dialog")
        )
        if scalable_fixed:
            self._scale_fixed_dimensions(widget)

        icon_scaled = scale_widget_icon(self.qt, widget, factor)
        if not icon_scaled:
            with suppress(Exception):
                icon_size = widget.iconSize()
                if icon_size.isValid() and not icon_size.isNull():
                    base = getattr(widget, "_mountlet_zoom_base_icon_size", None)
                    if base is None:
                        base = (icon_size.width(), icon_size.height())
                        setattr(widget, "_mountlet_zoom_base_icon_size", base)
                    widget.setIconSize(
                        self.qt.QSize(max(1, round(base[0] * factor)), max(1, round(base[1] * factor)))
                    )

    def _scale_fixed_dimensions(self, widget: Any) -> None:
        with suppress(Exception):
            minimum, maximum = widget.minimumSize(), widget.maximumSize()
            base = getattr(widget, "_mountlet_zoom_base_fixed_size", None)
            if base is None:
                fixed_width = minimum.width() if 0 < minimum.width() == maximum.width() < 16_777_215 else 0
                fixed_height = minimum.height() if 0 < minimum.height() == maximum.height() < 16_777_215 else 0
                base = (fixed_width, fixed_height)
                setattr(widget, "_mountlet_zoom_base_fixed_size", base)
            levels = getattr(widget, "_mountlet_zoom_fixed_size_levels", None)
            if levels is None or getattr(widget, "_mountlet_zoom_fixed_size_levels_base", None) != base:
                levels = (metric_levels(base[0]), metric_levels(base[1]))
                setattr(widget, "_mountlet_zoom_fixed_size_levels", levels)
                setattr(widget, "_mountlet_zoom_fixed_size_levels_base", base)
            if base[0] > 0:
                widget.setFixedWidth(metric_at_level(levels[0], self.steps))
            if base[1] > 0:
                widget.setFixedHeight(metric_at_level(levels[1], self.steps))

    def _scale_style_sheet(self, widget: Any) -> None:
        with suppress(Exception):
            current = widget.styleSheet()
            if not current:
                return
            last = getattr(widget, "_mountlet_zoom_last_style", None)
            base = getattr(widget, "_mountlet_zoom_base_style", None)
            if base is None or (last is not None and current != last):
                base = current
                setattr(widget, "_mountlet_zoom_base_style", base)
            factor = zoom_factor(self.steps)

            def replace(match: re.Match[str]) -> str:
                value = float(match.group(1))
                scaled = value * factor
                rendered = str(round(scaled, 2)).rstrip("0").rstrip(".")
                return f"{rendered}px"

            scaled_style = re.sub(r"(?<![\w.-])(\d+(?:\.\d+)?)px\b", replace, base)
            setattr(widget, "_mountlet_zoom_last_style", scaled_style)
            if scaled_style != current:
                widget.setStyleSheet(scaled_style)

    def _scale_layout(self, layout: Any) -> None:
        if layout is None:
            return
        with suppress(Exception):
            base = getattr(layout, "_mountlet_zoom_base_margins", None)
            if base is None:
                margins = layout.contentsMargins()
                base = (margins.left(), margins.top(), margins.right(), margins.bottom())
                setattr(layout, "_mountlet_zoom_base_margins", base)
            levels = getattr(layout, "_mountlet_zoom_margin_levels", None)
            if levels is None:
                levels = tuple(metric_levels(value) for value in base)
                setattr(layout, "_mountlet_zoom_margin_levels", levels)
            layout.setContentsMargins(*(metric_at_level(values, self.steps) for values in levels))
        with suppress(Exception):
            base_spacing = getattr(layout, "_mountlet_zoom_base_spacing", None)
            if base_spacing is None:
                spacing = layout.spacing()
                base_spacing = spacing
                setattr(layout, "_mountlet_zoom_base_spacing", base_spacing)
            if base_spacing >= 0:
                levels = getattr(layout, "_mountlet_zoom_spacing_levels", None)
                if levels is None:
                    levels = metric_levels(base_spacing)
                    setattr(layout, "_mountlet_zoom_spacing_levels", levels)
                layout.setSpacing(metric_at_level(levels, self.steps))
        with suppress(Exception):
            for index in range(layout.count()):
                self._scale_layout(layout.itemAt(index).layout())
        self._scale_grid_constraints(layout, self.steps)

    @staticmethod
    def _scale_grid_constraints(layout: Any, steps: int) -> None:
        with suppress(Exception):
            base_columns = getattr(layout, "_mountlet_zoom_base_column_minimums", None)
            if base_columns is None:
                base_columns = tuple(layout.columnMinimumWidth(index) for index in range(layout.columnCount()))
                setattr(layout, "_mountlet_zoom_base_column_minimums", base_columns)
            for index, value in enumerate(base_columns):
                levels = metric_levels(value)
                layout.setColumnMinimumWidth(index, metric_at_level(levels, steps))
        with suppress(Exception):
            base_rows = getattr(layout, "_mountlet_zoom_base_row_minimums", None)
            if base_rows is None:
                base_rows = tuple(layout.rowMinimumHeight(index) for index in range(layout.rowCount()))
                setattr(layout, "_mountlet_zoom_base_row_minimums", base_rows)
            for index, value in enumerate(base_rows):
                levels = metric_levels(value)
                layout.setRowMinimumHeight(index, metric_at_level(levels, steps))

    def _resize_window(self, window: Any, ratio: float) -> None:
        with suppress(Exception):
            if (
                getattr(window, "_mountlet_zoom_geometry_managed", False)
                or not window.isVisible()
                or window.isMaximized()
                or window.isFullScreen()
            ):
                return
            frame = window.frameGeometry()
            anchor_x = frame.x() + frame.width() / 2
            anchor_y = frame.y() + frame.height() / 2
            size = window.size()
            frame_width_overhead = max(frame.width() - size.width(), 0)
            frame_height_overhead = max(frame.height() - size.height(), 0)
            width = max(window.minimumWidth(), round(size.width() * ratio))
            height = max(window.minimumHeight(), round(size.height() * ratio))
            screen = window.screen() or self.app.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                width = min(width, max(1, available.width() - 16))
                height = min(height, max(1, available.height() - 16))
            window.resize(width, height)
            theoretical_frame_width = width + frame_width_overhead
            theoretical_frame_height = height + frame_height_overhead
            window.move(
                round(anchor_x - theoretical_frame_width / 2),
                round(anchor_y - theoretical_frame_height / 2),
            )

    @staticmethod
    def _point_size(font: Any) -> float:
        size = float(font.pointSizeF())
        if size <= 0:
            size = float(font.pointSize())
        return size if size > 0 else 10.0
