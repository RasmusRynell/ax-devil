"""Selectable charts for bounded paint observations."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.diagnostics.render_metrics import HISTORY_SECONDS, TIMING_BY_KEY, PaintObservation


class PaintHistoryChart(QWidget):
    """Plot measured operations with spatial sample selection and keyboard navigation."""

    sampleSelected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(150)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Paint history. Click a sample to inspect; use arrow keys to navigate.")
        self._history: tuple[PaintObservation, ...] = ()
        self._now = 0.0
        self._key = "interval"
        self._ceiling = 1.0
        self._selected_index: int | None = None

    def set_history(
        self, history: tuple[PaintObservation, ...], now: float, key: str, selected_index: int | None = None
    ) -> None:
        """Display a stable snapshot and mark the selected paint independently of frame identity."""
        self._history = history
        self._now = now
        self._key = key
        self._selected_index = selected_index
        self._ceiling = max(1.0, max((value for _, value in self._points()), default=0.0) * 1.1)
        self.update()

    def _points(self) -> list[tuple[int, float]]:
        return [
            (index, value) for index, item in enumerate(self._history) if (value := item.timings[self._key]) is not None
        ]

    def _plot_area(self) -> QRect:
        """Return the plot inside gutters sized for the axis labels at the current text size."""
        metrics = self.fontMetrics()
        left = Space.S + metrics.horizontalAdvance(f"{self._ceiling:.1f} ms") + Space.M
        return self.rect().adjusted(left, metrics.height(), -Space.XL, -(metrics.height() + Space.M))

    def _position(self, index: int, value: float) -> QPointF:
        area = self._plot_area()
        x = area.right() - (self._now - self._history[index].sample.completed_at) / HISTORY_SECONDS * area.width()
        return QPointF(x, area.bottom() - value / self._ceiling * area.height())

    def _hit_test(self, position: QPointF) -> int | None:
        candidates = [
            (math.hypot(point.x() - position.x(), point.y() - position.y()), index)
            for index, value in self._points()
            for point in (self._position(index, value),)
        ]
        if not candidates:
            return None
        distance, index = min(candidates)
        return index if distance <= 18 else None

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw measured points, gaps and a persistent selected-paint marker."""
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.brush(QPalette.ColorRole.Base))
        area = self._plot_area()
        baseline = self.height() - painter.fontMetrics().descent() - Space.XS
        points = self._points()
        maximum = self._ceiling
        for fraction in (0.0, 0.5, 1.0):
            y = round(area.bottom() - area.height() * fraction)
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            painter.drawLine(area.left(), y, area.right(), y)
            painter.setPen(palette.color(QPalette.ColorRole.Text))
            painter.drawText(Space.S, y + painter.fontMetrics().capHeight() // 2, f"{maximum * fraction:.1f} ms")
        painter.drawText(area.left(), baseline, f"−{HISTORY_SECONDS:g} s")
        painter.drawText(area.right() - painter.fontMetrics().horizontalAdvance("snapshot"), baseline, "snapshot")
        if not points:
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "No measurements in this window")
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            previous: dict[bool, QPointF] = {}
            for index, value in points:
                item = self._history[index]
                point = self._position(index, value)
                role = QPalette.ColorRole.Link if item.new_frame else QPalette.ColorRole.PlaceholderText
                painter.setPen(QPen(palette.color(role), 2.0))
                before = previous.get(item.new_frame)
                if before is not None and point.x() - before.x() < area.width() / HISTORY_SECONDS * 0.5:
                    painter.drawLine(before, point)
                painter.drawPoint(point)
                previous[item.new_frame] = point
        if self._selected_index is not None:
            item = self._history[self._selected_index]
            selected_value = item.timings[self._key]
            point = self._position(self._selected_index, selected_value or 0.0)
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(point.x(), area.top()), QPointF(point.x(), area.bottom()))
            if selected_value is not None:
                painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 2.0))
                painter.drawEllipse(point, 5, 5)
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Preview the point nearest the cursor in both time and value."""
        index = self._hit_test(event.position())
        if index is None:
            QToolTip.hideText()
            return
        item = self._history[index]
        value = item.timings[self._key]
        kind = "New frame" if item.new_frame else "Repaint"
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{kind} · {item.sample.frame.label}\n{TIMING_BY_KEY[self._key].label}: {value:.3f} ms\n"
            "Click to freeze and investigate this paint.",
            self,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Select a nearby measured point without changing playback."""
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._hit_test(event.position())
            if index is not None:
                self.sampleSelected.emit(index)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Navigate measured points with the arrow keys."""
        indices = [index for index, _ in self._points()]
        if indices and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self._selected_index is None:
                target = indices[0]
            elif event.key() == Qt.Key.Key_Left:
                target = next((index for index in reversed(indices) if index < self._selected_index), indices[0])
            else:
                target = next((index for index in indices if index > self._selected_index), indices[-1])
            self.sampleSelected.emit(target)
            event.accept()
            return
        super().keyPressEvent(event)
