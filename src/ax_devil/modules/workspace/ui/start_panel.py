"""Sidebar start panel shown while the workspace is empty: open actions and recent workspaces."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.shortcuts.shortcuts import DEFAULT_SHORTCUTS
from ax_devil.modules.workspace.core import RecentWorkspace, workspace_file_stem

if TYPE_CHECKING:
    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager

_RECENT_DAYS_AS_WEEKDAY = 6
_ACTION_NAMES = {definition.action_id: definition.display_name for definition in DEFAULT_SHORTCUTS}


def _today() -> date:
    return date.today()


def recent_date(used: date, today: date) -> str:
    """Return how the start panel shows when a recent workspace was last opened or saved: today, weekday, or date."""
    days = (today - used).days
    if days <= 0:
        return "today"
    if days <= _RECENT_DAYS_AS_WEEKDAY:
        return used.strftime("%a")
    if used.year == today.year:
        return f"{used.strftime('%b')} {used.day}"
    return f"{used.strftime('%b')} {used.day} {used.year}"


def _home_relative(folder: Path) -> str:
    """Return *folder* with the home folder shortened to ``~``."""
    try:
        return f"~/{folder.relative_to(Path.home())}".removesuffix("/.")
    except ValueError:
        return str(folder)


class StartPanel(QWidget):
    """Open a workspace, a video, a camera, or a playlist, or reopen a recent workspace.

    The open buttons trigger the shortcut manager's actions, so they do what the File menu does, carry the same
    names, and show the same keys in their tooltips.
    """

    recent_workspace_requested = Signal(object)  # Path

    _OPEN_ACTIONS: tuple[tuple[str, Icon], ...] = (
        ("app.open_workspace", Icon.BROWSE),
        ("app.add_video", Icon.VIDEO),
        ("app.add_live_stream", Icon.LIVE_VIDEO),
        ("app.add_playlist", Icon.PLAYLIST),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilStartPanel")
        self._shortcut_manager: ShortcutManager | None = None
        self._recents: tuple[RecentWorkspace, ...] = ()
        self._recent_rows: list[ListRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        body.setObjectName("AxDevilStartBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(Space.S)

        self._open_buttons: dict[str, QPushButton] = {}
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(Space.S)
        for action_id, icon in self._OPEN_ACTIONS:
            buttons.addWidget(self._open_button(action_id, icon))
        self._open_buttons[self._OPEN_ACTIONS[0][0]].setDefault(True)
        layout.addLayout(buttons)

        layout.addSpacing(Space.L)
        self._recent_heading = QLabel("Recent workspaces")
        TextRole.CAPTION.apply(self._recent_heading)
        self._recent_heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._recent_heading)
        self._recent_list = QVBoxLayout()
        self._recent_list.setContentsMargins(0, 0, 0, 0)
        self._recent_list.setSpacing(0)
        layout.addLayout(self._recent_list)
        layout.addStretch(1)

        scroll = ContentScrollArea(body)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)

        self.set_recent_workspaces(())
        follow_appearance(self, self._apply_appearance)

    def set_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Trigger *manager*'s actions from the open buttons and show their keys in the tooltips."""
        self._shortcut_manager = manager
        for action_id, button in self._open_buttons.items():
            manager.bind_tooltip(button, action_id)

    def set_recent_workspaces(self, recents: Sequence[RecentWorkspace]) -> None:
        """List the *recents*, newest first; clicking one emits ``recent_workspace_requested``."""
        for row in self._recent_rows:
            self._recent_list.removeWidget(row)
            row.deleteLater()
        self._recents = tuple(recents)
        today = _today()
        self._recent_rows = []
        for recent in self._recents:
            row = ListRow(
                workspace_file_stem(recent.path),
                icon=Icon.BROWSE.icon(),
                subtitle=_home_relative(recent.path.parent),
                detail=recent_date(recent.used.astimezone().date(), today),
                parent=self,
            )
            row.setToolTip(str(recent.path))
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.activated.connect(lambda path=recent.path: self.recent_workspace_requested.emit(path))
            self._recent_rows.append(row)
            self._recent_list.addWidget(row)
        self._recent_heading.setVisible(bool(self._recent_rows))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Recompute the relative dates, which go stale while the app stays open across midnight."""
        super().showEvent(event)
        self.set_recent_workspaces(self._recents)

    def open_button(self, action_id: str) -> QPushButton:
        """Return the button that triggers *action_id*."""
        return self._open_buttons[action_id]

    def _open_button(self, action_id: str, icon: Icon) -> QPushButton:
        button = QPushButton(_ACTION_NAMES[action_id], self)
        # Shrink with the sidebar instead of setting its minimum width; the labels fit at the default width.
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIcon(icon.icon())
        button.clicked.connect(lambda _checked=False: self._trigger(action_id))
        self._open_buttons[action_id] = button
        return button

    def _trigger(self, action_id: str) -> None:
        if self._shortcut_manager is not None:
            self._shortcut_manager.get_action(action_id).trigger()

    def _apply_appearance(self) -> None:
        side = TextRole.BODY.px
        # A wider icon box centers the icon in it, which spaces the icon from the label.
        icon_size = QSize(side + Space.S, side)
        for button in self._open_buttons.values():
            button.setIconSize(icon_size)
        palette = self.palette()
        muted = palette_color_css(palette, palette.ColorRole.PlaceholderText)
        self.setStyleSheet(
            f"""
            #AxDevilStartBody {{ background: transparent; }}
            #AxDevilStartBody QPushButton {{ text-align: left; padding-left: {Space.M}px; padding-right: {Space.M}px; }}
            """
        )
        self._recent_heading.setStyleSheet(f"color: {muted}; padding-top: {Space.S}px;")
