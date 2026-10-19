"""Small widgets of the offline viewer: overlay host, playlist navigation bar and lane context menu."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.video_viewer.offline_lane import OfflineLane


class ViewerOverlayHost(QWidget):
    """Fill available space with viewer content and anchor one overlay at the bottom."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content_layout = QGridLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

    def set_content(self, content: QWidget) -> None:
        """Show *content* over the whole host."""
        self.content_layout.addWidget(content, 0, 0)

    def set_bottom_overlay(self, overlay: QWidget) -> None:
        """Anchor *overlay* at the bottom, above the content."""
        self.content_layout.addWidget(overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)
        overlay.raise_()


class EntryNavigationBar(QWidget):
    """Previous and next buttons around an "Entry n / total" label for stepping through a playlist."""

    def __init__(self, parent: QWidget, *, on_previous: Callable[[], None], on_next: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.M, 0, Space.M, 0)
        layout.setSpacing(Space.S)
        layout.addStretch(1)

        self._previous = QPushButton(Icon.STEP_BACK.icon(), "")
        self._previous.setToolTip("Previous entry")
        self._previous.setAccessibleName("Previous entry")
        self._previous.clicked.connect(on_previous)
        layout.addWidget(self._previous)

        self._label = QLabel()
        layout.addWidget(self._label)

        self._next = QPushButton(Icon.STEP_FORWARD.icon(), "")
        self._next.setToolTip("Next entry")
        self._next.setAccessibleName("Next entry")
        self._next.clicked.connect(on_next)
        layout.addWidget(self._next)
        layout.addStretch(1)

    def show_position(self, index: int, count: int, *, can_previous: bool, can_next: bool) -> None:
        """Show entry *index* (zero-based) of *count* and enable the buttons that have somewhere to go."""
        self._label.setText(f"Entry {index + 1} / {count}")
        self._previous.setEnabled(can_previous)
        self._next.setEnabled(can_next)


def exec_lane_menu(
    window: QWidget,
    lane: OfflineLane,
    actions: Sequence[QAction],
    hide: Callable[[], None] | None,
    global_pos: QPoint,
) -> None:
    """Show what can be done with *lane* at *global_pos* and return when the menu closes.

    Export is the lane's own action, the same one its media tools button triggers; *actions* follow it. Hide, when
    *hide* is given, skips the lane in playback.
    """
    menu = QMenu(window)
    menu.setToolTipsVisible(True)
    menu.addAction(lane.export_action)
    for action in actions:
        menu.addAction(action)
    if hide is not None:
        menu.addSeparator()
        hide_action = QAction("Hide", menu)
        hide_action.setToolTip("Skip this lane in playback. Bring it back with Unhide in the sidebar.")
        hide_action.triggered.connect(lambda _checked=False: hide())
        menu.addAction(hide_action)
    try:
        menu.exec(global_pos)
    finally:
        menu.deleteLater()
