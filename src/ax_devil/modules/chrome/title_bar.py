"""Custom title bar for frameless windows with system-native drag-to-move.

Uses ``QWindow.startSystemMove()`` for native window dragging instead of
manual offset tracking.  Falls back to manual drag on platforms where the
system call is unavailable.  Supports drag-from-maximized restore with
proportional cursor positioning.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenuBar,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole

DRAG_THRESHOLD = 4


def _global_pos(event: QMouseEvent) -> QPoint:
    """Extract global cursor position from a mouse event (PySide6-compatible)."""
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


class _DragAwareMenuBar(QMenuBar):
    """Menu bar that delegates non-menu mouse events to the title bar for drag gestures."""

    def __init__(self, title_bar: TitleBar) -> None:
        super().__init__(title_bar)
        self._title_bar = title_bar

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: D401 - Qt override
        if self._on_menu_item(event) or not self._title_bar._begin_drag(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: D401 - Qt override
        if not self._title_bar._continue_drag(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: D401 - Qt override
        if not self._title_bar._end_drag(event):
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: D401 - Qt override
        if self._on_menu_item(event) or not self._title_bar._handle_double_click(event):
            super().mouseDoubleClickEvent(event)

    def _on_menu_item(self, event: QMouseEvent) -> bool:
        """Return True when the click lands on an actual menu action."""
        if event.button() != Qt.MouseButton.LeftButton:
            return True
        return self.actionAt(event.pos()) is not None


class TitleBar(QWidget):
    """Compact frameless title bar with system-native drag, menus, and window controls."""

    def __init__(
        self,
        window: QWidget,
        *,
        show_menu_bar: bool = True,
        show_minimize: bool = True,
        show_maximize: bool = True,
        show_close: bool = True,
    ) -> None:
        super().__init__(window)

        self._window = window
        self._show_menu_bar = show_menu_bar
        self._show_minimize = show_minimize
        self._show_maximize = show_maximize
        self._show_close = show_close
        self._drag_offset: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._pending_restore = False

        self.setObjectName("AxDevilTitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._build_ui()
        window.windowTitleChanged.connect(self._title_label.setText)
        self.sync_buttons()
        follow_appearance(self, self._apply_appearance)

    # -- Construction -------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._title_label = QLabel(self._window.windowTitle(), self)
        self._title_label.setObjectName("AxDevilTitleLabel")
        self._title_label.setContentsMargins(Space.M, 0, Space.S, 0)
        self._title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._title_label)

        self.menu_bar = _DragAwareMenuBar(self)
        self.menu_bar.setObjectName("AxDevilMenuBar")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if self._show_menu_bar:
            layout.addWidget(self.menu_bar, 1)
        else:
            self.menu_bar.hide()
            layout.addStretch(1)

        controls = QWidget(self)
        controls.setObjectName("AxDevilWindowControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)

        self._btn_min = self._make_button(Icon.MINIMIZE, "Minimize")
        self._btn_max = self._make_button(Icon.MAXIMIZE, "Maximize")
        self._btn_close = self._make_button(Icon.CLOSE, "Close")
        self._btn_close.setObjectName("AxDevilCloseButton")

        if self._show_minimize:
            controls_layout.addWidget(self._btn_min)
            self._btn_min.clicked.connect(self._window.showMinimized)
        else:
            self._btn_min.hide()

        if self._show_maximize:
            controls_layout.addWidget(self._btn_max)
            self._btn_max.clicked.connect(self._toggle_maximize)
        else:
            self._btn_max.hide()

        if self._show_close:
            controls_layout.addWidget(self._btn_close)
            self._btn_close.clicked.connect(self._window.close)
        else:
            self._btn_close.hide()

        layout.addWidget(controls, 0, Qt.AlignmentFlag.AlignRight)

    def _make_button(self, icon: Icon, tooltip: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setAutoRaise(True)
        btn.setIcon(icon.icon())
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    # -- Public API ---------------------------------------------------------

    def sync_buttons(self) -> None:
        """Refresh button icons to match the current window state."""
        if not self._show_maximize:
            return
        maximized = self._window.isMaximized()
        self._btn_max.setIcon((Icon.RESTORE if maximized else Icon.MAXIMIZE).icon())
        self._btn_max.setToolTip("Restore" if maximized else "Maximize")

    def _apply_appearance(self) -> None:
        """Size the bar and its buttons to the text, and color them from the current palette."""
        height = Height.TITLE_BAR.px
        self.setFixedHeight(height)
        for button in (self._btn_min, self._btn_max, self._btn_close):
            button.setFixedSize(height, height)
        self._title_label.setFont(TextRole.STRONG.font())
        palette = self.palette()
        text = QPalette.ColorRole.Text
        hover_bg = palette_color_css(palette, text, alpha=0.07)
        hover_border = palette_color_css(palette, text, alpha=0.1)
        pressed_bg = palette_color_css(palette, text, alpha=0.12)
        self.setStyleSheet(
            f"""
            #AxDevilTitleBar, #AxDevilWindowControls {{
                background: palette(alternate-base);
                border-bottom: 1px solid {hover_border};
            }}
            #AxDevilTitleLabel {{
                color: palette(text);
            }}
            #AxDevilMenuBar {{
                background: transparent;
                border: none;
            }}
            #AxDevilMenuBar::item {{
                padding: {Space.XS}px {Space.M}px;
                margin: 0px 0px;
                border-radius: {Radius.CONTROL}px;
            }}
            #AxDevilTitleBar QToolButton {{
                border: 1px solid transparent;
                background: transparent;
                color: palette(text);
            }}
            #AxDevilTitleBar QToolButton:hover {{
                background: {hover_bg};
                border-color: {hover_border};
            }}
            #AxDevilTitleBar QToolButton:pressed {{
                background: {pressed_bg};
            }}
            #AxDevilCloseButton:hover {{
                background: rgba(232, 17, 35, 217);
                border-color: rgba(232, 17, 35, 255);
                color: white;
            }}
            """
        )

    # -- Mouse handling (title bar itself) ----------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._begin_drag(event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._continue_drag(event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._end_drag(event):
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._handle_double_click(event):
            return
        super().mouseDoubleClickEvent(event)

    # -- Drag internals -----------------------------------------------------

    def _begin_drag(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        if self._window.isMaximized() or self._window.isFullScreen():
            self._press_pos = _global_pos(event)
            self._pending_restore = True
            event.accept()
            return True

        # Normal state: try system move first.
        handle = self._window.windowHandle()
        if handle is not None and handle.startSystemMove():
            event.accept()
            return True

        # Fallback: manual offset-based drag.
        self._drag_offset = _global_pos(event) - self._window.frameGeometry().topLeft()
        event.accept()
        return True

    def _continue_drag(self, event: QMouseEvent) -> bool:
        # Manual drag fallback.
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(_global_pos(event) - self._drag_offset)
            event.accept()
            return True

        # Drag-from-maximized: wait for threshold, restore, then start system move.
        if self._pending_restore and event.buttons() & Qt.MouseButton.LeftButton and self._press_pos:
            if self._drag_distance(event) < DRAG_THRESHOLD:
                return False

            self._restore_from_maximized(event)
            self._pending_restore = False

            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._press_pos = None
                event.accept()
                return True

            # Fallback: manual drag.
            self._press_pos = None
            self._drag_offset = _global_pos(event) - self._window.frameGeometry().topLeft()
            event.accept()
            return True

        return False

    def _end_drag(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False

        had_interaction = self._drag_offset is not None or self._pending_restore
        self._drag_offset = None
        self._pending_restore = False
        self._press_pos = None

        if had_interaction:
            event.accept()
        return had_interaction

    def _handle_double_click(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or not self._show_maximize:
            return False
        self._toggle_maximize()
        event.accept()
        return True

    def _toggle_maximize(self) -> None:
        if not self._show_maximize:
            return
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _restore_from_maximized(self, event: QMouseEvent) -> None:
        """Restore window from maximized and position it under the cursor."""
        if not (self._window.isMaximized() or self._window.isFullScreen()):
            return

        ratio = 0.5
        if self.width() > 0:
            ratio = max(0.0, min(1.0, event.position().x() / self.width()))

        if hasattr(self._window, "restore_from_maximized"):
            self._window.restore_from_maximized(ratio, _global_pos(event), self.height())

    def _drag_distance(self, event: QMouseEvent) -> int:
        if not self._press_pos:
            return 0
        delta: QPoint = _global_pos(event) - self._press_pos
        return abs(int(delta.x())) + abs(int(delta.y()))
