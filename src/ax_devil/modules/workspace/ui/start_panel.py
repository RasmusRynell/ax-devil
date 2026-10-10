"""Sidebar start panel shown while the workspace is empty: open actions and recent workspaces."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.workspace.core import workspace_name

if TYPE_CHECKING:
    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager

_DROP_HINT = "Or drop video files anywhere"
_RECENT_DAYS_AS_WEEKDAY = 6


def recent_date(modified: date, today: date) -> str:
    """Return how the start panel shows when a recent workspace was last saved: today, a weekday, or a date."""
    days = (today - modified).days
    if days <= 0:
        return "today"
    if days <= _RECENT_DAYS_AS_WEEKDAY:
        return modified.strftime("%a")
    if modified.year == today.year:
        return f"{modified.strftime('%b')} {modified.day}"
    return f"{modified.strftime('%b')} {modified.day} {modified.year}"


def _home_relative(folder: Path) -> str:
    """Return *folder* with the home folder shortened to ``~``."""
    try:
        return f"~/{folder.relative_to(Path.home())}".removesuffix("/.")
    except ValueError:
        return str(folder)


class _ElidedLabel(QLabel):
    """A one-line label that elides its text instead of widening its container."""

    def __init__(self, text: str, elide: Qt.TextElideMode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self._elide = elide
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)
        self._update_text()

    def full_text(self) -> str:
        """Return the text before eliding."""
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Elide again at the new width."""
        super().resizeEvent(event)
        self._update_text()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        """Elide again when the font changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._update_text()

    def _update_text(self) -> None:
        text = self.fontMetrics().elidedText(self._full_text, self._elide, max(0, self.width()))
        if text != self.text():
            self.setText(text)


class _RecentRow(QFrame):
    """One recent workspace: its name and folder, and when it was last saved; clicking it opens it."""

    clicked = Signal()

    def __init__(self, path: Path, today: date, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("AxDevilRecentRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(str(path))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.S, Space.M, Space.S)
        layout.setSpacing(Space.M)
        self._icon = QLabel(self)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self.name_label = _ElidedLabel(workspace_name(path), Qt.TextElideMode.ElideRight, self)
        self.folder_label = _ElidedLabel(_home_relative(path.parent), Qt.TextElideMode.ElideMiddle, self)
        TextRole.MONO_SMALL.apply(self.folder_label)
        text.addWidget(self.name_label)
        text.addWidget(self.folder_label)
        layout.addLayout(text, 1)

        self.date_label = QLabel(self)
        TextRole.SMALL.apply(self.date_label)
        try:
            self.date_label.setText(recent_date(datetime.fromtimestamp(path.stat().st_mtime).date(), today))
        except OSError:
            self.date_label.clear()
        layout.addWidget(self.date_label, 0, Qt.AlignmentFlag.AlignTop)
        follow_appearance(self, self._apply_appearance)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Open this workspace on a left click."""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def _apply_appearance(self) -> None:
        side = TextRole.BODY.px
        self._icon.setPixmap(Icon.BROWSE.icon().pixmap(side, side))
        palette = self.palette()
        muted = palette_color_css(palette, palette.ColorRole.Text, alpha=0.6)
        self.setStyleSheet(
            f"""
            #AxDevilRecentRow {{ border-radius: {Radius.CONTROL}px; background: transparent; }}
            #AxDevilRecentRow:hover {{ background: palette(midlight); }}
            #AxDevilRecentRow QLabel {{ background: transparent; }}
            """
        )
        self.folder_label.setStyleSheet(f"color: {muted};")
        self.date_label.setStyleSheet(f"color: {muted};")


class StartPanel(QWidget):
    """Open a workspace, a video, a camera, or a playlist, or reopen a recent workspace.

    The open buttons trigger the shortcut manager's actions, so they do what the File menu does and show the same
    keys in their tooltips.
    """

    recent_workspace_requested = Signal(object)  # Path

    _OPEN_ACTIONS: tuple[tuple[str, str, Icon], ...] = (
        ("app.open_workspace", "Open workspace…", Icon.BROWSE),
        ("app.add_video", "Open video", Icon.VIDEO),
        ("app.add_live_stream", "Connect camera", Icon.LIVE_VIDEO),
        ("app.add_playlist", "Open playlist…", Icon.PLAYLIST),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilStartPanel")
        self._shortcut_manager: ShortcutManager | None = None
        self._recent_rows: list[_RecentRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        body.setObjectName("AxDevilStartBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(Space.S)

        self._open_buttons: dict[str, QPushButton] = {}
        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(Space.S)
        for index, (action_id, label, icon) in enumerate(self._OPEN_ACTIONS):
            button = self._open_button(action_id, label, icon)
            if index == 0:
                button.setDefault(True)  # The theme fills the default button in the accent color.
                buttons.addWidget(button, 0, 0, 1, 2)
            elif index == len(self._OPEN_ACTIONS) - 1:
                buttons.addWidget(button, 2, 0, 1, 2)
            else:
                buttons.addWidget(button, 1, index - 1)
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
        self._drop_hint = QLabel(_DROP_HINT)
        self._drop_hint.setObjectName("AxDevilStartFooter")
        self._drop_hint.setWordWrap(True)
        TextRole.SMALL.apply(self._drop_hint)
        outer.addWidget(self._drop_hint)

        self.set_recent_workspaces(())
        follow_appearance(self, self._apply_appearance)

    def set_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Trigger *manager*'s actions from the open buttons and show their keys in the tooltips."""
        self._shortcut_manager = manager
        for action_id, button in self._open_buttons.items():
            keys = manager.current_key_sequence(action_id)
            shortcut = keys.toString(keys.SequenceFormat.NativeText) if keys is not None else ""
            name = manager.get_definition(action_id).display_name
            button.setToolTip(f"{name} ({shortcut})" if shortcut else name)

    def set_recent_workspaces(self, paths: Sequence[Path]) -> None:
        """List workspace files *paths*, newest first; clicking one emits ``recent_workspace_requested``."""
        for row in self._recent_rows:
            self._recent_list.removeWidget(row)
            row.deleteLater()
        today = date.today()
        self._recent_rows = [_RecentRow(path, today, self) for path in paths]
        for row in self._recent_rows:
            row.clicked.connect(lambda path=row.path: self.recent_workspace_requested.emit(path))
            self._recent_list.addWidget(row)
        self._recent_heading.setVisible(bool(self._recent_rows))

    def recent_rows(self) -> list[_RecentRow]:
        """Return the recent workspace rows, newest first."""
        return list(self._recent_rows)

    def open_button(self, action_id: str) -> QPushButton:
        """Return the button that triggers *action_id*."""
        return self._open_buttons[action_id]

    def _open_button(self, action_id: str, label: str, icon: Icon) -> QPushButton:
        button = QPushButton(icon.icon(), label, self)
        # Shrink with the sidebar instead of setting its minimum width; the labels fit at the default width.
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False: self._trigger(action_id))
        self._open_buttons[action_id] = button
        return button

    def _trigger(self, action_id: str) -> None:
        if self._shortcut_manager is not None:
            self._shortcut_manager.get_action(action_id).trigger()

    def _apply_appearance(self) -> None:
        palette = self.palette()
        border = palette_color_css(palette, palette.ColorRole.Mid, alpha=0.5)
        muted = palette_color_css(palette, palette.ColorRole.Text, alpha=0.6)
        side = TextRole.BODY.px
        for button in self._open_buttons.values():
            button.setIconSize(QSize(side, side))
        primary = self._open_buttons[self._OPEN_ACTIONS[0][0]]
        primary.setIcon(self._OPEN_ACTIONS[0][2].icon(palette.color(palette.ColorRole.HighlightedText)))
        self.setStyleSheet(
            f"""
            #AxDevilStartBody {{ background: transparent; }}
            #AxDevilStartFooter {{
                color: {muted};
                border-top: 1px solid {border};
                padding: {Space.M}px {Space.L}px;
            }}
            """
        )
        self._recent_heading.setStyleSheet(f"color: {muted}; padding-top: {Space.S}px;")
