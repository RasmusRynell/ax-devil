"""Frameless window controller: native resize grips and palette-based styling.

Uses ``QWindow.startSystemResize()`` for native edge/corner resizing instead
of manual mouse-delta geometry math, matching how modern toolkits handle
frameless windows.  Chrome colours are derived from the active ``QPalette``
so the window adapts to light, dark, or custom themes automatically.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette, QResizeEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.title_bar import TitleBar

RESIZE_MARGIN = 5
WINDOW_BORDER_WIDTH = 1


_GRIP_SPECS: tuple[tuple[str, Qt.Edge, Qt.CursorShape], ...] = (
    ("top", Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
    ("bottom", Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
    ("left", Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
    ("right", Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
    ("top_left", Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeFDiagCursor),
    ("top_right", Qt.Edge.TopEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeBDiagCursor),
    ("bottom_left", Qt.Edge.BottomEdge | Qt.Edge.LeftEdge, Qt.CursorShape.SizeBDiagCursor),
    ("bottom_right", Qt.Edge.BottomEdge | Qt.Edge.RightEdge, Qt.CursorShape.SizeFDiagCursor),
)


class WindowFrameController:
    """Manages frameless window chrome: native resize grips, palette styling, and state sync."""

    def __init__(self, window: QWidget, *, show_border: bool = False) -> None:
        self._window = window
        self._show_border = show_border
        self._border: _WindowBorder | None = None
        self._grips: dict[str, _ResizeGrip] = {}

    def initialize(self) -> None:
        """Install grips and apply initial palette styles."""
        margin = WINDOW_BORDER_WIDTH if self._show_border else 0
        self._window.setContentsMargins(margin, margin, margin, margin)
        self._install_border()
        self._install_grips()
        self.apply_palette_style()

    def handle_window_state_change(self) -> None:
        """Toggle grip visibility when the window maximizes or restores."""
        self._set_grips_visible(self._is_resizable())

    def handle_resize_event(self, event: QResizeEvent) -> None:
        """Reposition grips to match the new window size."""
        _ = event
        self._update_border_geometry()
        self._update_grip_geometry()

    def handle_palette_change(self) -> None:
        """Re-derive chrome colours from the current palette."""
        self.apply_palette_style()

    def restore_from_maximized(self, x_ratio: float, cursor_pos: QPoint, title_bar_height: int) -> None:
        """Restore a maximized window and position it under the cursor.

        Args:
            x_ratio: Horizontal fraction (0-1) of the cursor within the title bar.
            cursor_pos: Global cursor position at the moment of restore.
            title_bar_height: Height of the title bar widget in pixels.
        """
        if not (self._window.isMaximized() or self._window.isFullScreen()):
            return

        target = self._window.normalGeometry()
        if not target.isValid():
            target = self._window.geometry()

        ratio = max(0.0, min(1.0, x_ratio))
        self._window.showNormal()
        self._window.setGeometry(target)

        new_x = int(cursor_pos.x() - target.width() * ratio)
        new_y = int(cursor_pos.y() - title_bar_height / 2)
        self._window.move(new_x, max(new_y, 0))

    def apply_palette_style(self) -> None:
        """Generate and apply a stylesheet derived from the active ``QPalette``."""
        pal = self._window.palette()
        text = pal.color(QPalette.ColorRole.Text)

        hover_bg = QColor(text)
        hover_bg.setAlpha(18)
        hover_border = QColor(text)
        hover_border.setAlpha(26)
        pressed_bg = QColor(text)
        pressed_bg.setAlpha(31)

        title_bar = self._window.findChild(TitleBar)
        if title_bar is None:
            return
        title_bar.apply_palette_style(
            text=text,
            hover_bg=hover_bg,
            hover_border=hover_border,
            pressed_bg=pressed_bg,
        )

    def _install_grips(self) -> None:
        for key, edges, cursor in _GRIP_SPECS:
            self._grips[key] = _ResizeGrip(self._window, edges, cursor)
        self._update_grip_geometry()
        self._set_grips_visible(self._is_resizable())

    def _install_border(self) -> None:
        if not self._show_border:
            return
        self._border = _WindowBorder(self._window)
        self._update_border_geometry()
        self._border.show()

    def _update_grip_geometry(self) -> None:
        if not self._grips:
            return

        m = RESIZE_MARGIN
        w, h = self._window.width(), self._window.height()
        inner_w = max(0, w - 2 * m)
        inner_h = max(0, h - 2 * m)

        self._grips["top_left"].setGeometry(0, 0, m, m)
        self._grips["top_right"].setGeometry(max(0, w - m), 0, m, m)
        self._grips["bottom_left"].setGeometry(0, max(0, h - m), m, m)
        self._grips["bottom_right"].setGeometry(max(0, w - m), max(0, h - m), m, m)

        self._grips["top"].setGeometry(m, 0, inner_w, m)
        self._grips["bottom"].setGeometry(m, max(0, h - m), inner_w, m)
        self._grips["left"].setGeometry(0, m, m, inner_h)
        self._grips["right"].setGeometry(max(0, w - m), m, m, inner_h)

        for grip in self._grips.values():
            grip.raise_()

    def _update_border_geometry(self) -> None:
        if self._border is None:
            return
        self._border.setGeometry(0, 0, self._window.width(), self._window.height())
        self._border.setVisible(self._show_border and self._is_resizable())
        self._border.raise_()

    def _set_grips_visible(self, visible: bool) -> None:
        self._update_border_geometry()
        for grip in self._grips.values():
            grip.setVisible(visible)

    def _is_resizable(self) -> bool:
        return not (self._window.isMaximized() or self._window.isFullScreen())


class _ResizeGrip(QWidget):
    """Invisible edge/corner grip that initiates a native system resize."""

    def __init__(self, host: QWidget, edges: Qt.Edge, cursor: Qt.CursorShape) -> None:
        super().__init__(host)
        self._host = host
        self._edges = edges
        self.setCursor(cursor)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start a system resize on left-click when the window is resizable."""
        if event.button() == Qt.MouseButton.LeftButton and not (self._host.isMaximized() or self._host.isFullScreen()):
            handle = self._host.windowHandle()
            if handle is not None and handle.startSystemResize(self._edges):
                event.accept()
                return
        super().mousePressEvent(event)


class _WindowBorder(QWidget):
    """Visible inside edge for frameless secondary windows."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("AxDevilWindowBorder")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            f"""
            #AxDevilWindowBorder {{
                border: {WINDOW_BORDER_WIDTH}px solid palette(mid);
                background: transparent;
            }}
            """
        )
