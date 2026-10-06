"""Control visibility orchestration for player widgets."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer


class ControlVisibilityController:
    """Owns hover tracking and mouse-idle visibility policy."""

    def __init__(
        self,
        *,
        timer_parent: QObject | None,
        idle_hide_delay_ms: int,
        request_show: Callable[[], None],
        request_hide: Callable[[], None],
    ) -> None:
        self._request_show = request_show
        self._request_hide = request_hide
        self._hovered_elements: set[str] = set()

        self._idle_timer = QTimer(timer_parent)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle)
        self._idle_hide_delay_ms = idle_hide_delay_ms

    def on_hover_enter(self, element_name: str) -> None:
        self._hovered_elements.add(element_name)
        self._request_show()

    def on_hover_leave(self, element_name: str) -> None:
        self._hovered_elements.discard(element_name)
        if not self._hovered_elements:
            self._idle_timer.start(self._idle_hide_delay_ms)

    def on_mouse_move(self) -> None:
        self._request_show()
        self._idle_timer.start(self._idle_hide_delay_ms)

    def on_mouse_enter(self) -> None:
        self._request_show()

    def on_mouse_leave(self) -> None:
        if not self._hovered_elements:
            self._request_hide()

    def _on_idle(self) -> None:
        if not self._hovered_elements:
            self._request_hide()

    def cleanup(self) -> None:
        self._idle_timer.stop()
