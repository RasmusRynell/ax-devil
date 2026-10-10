"""Sidebar start panel shown while the workspace is empty: open actions and recent workspaces."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css, qcolor_to_css
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.shortcuts.shortcuts import DEFAULT_SHORTCUTS
from ax_devil.modules.workspace.core import workspace_name

if TYPE_CHECKING:
    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager

_RECENT_DAYS_AS_WEEKDAY = 6
_ACTION_NAMES = {definition.action_id: definition.display_name for definition in DEFAULT_SHORTCUTS}


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
        self.name_label = ElidedLabel(workspace_name(path), Qt.TextElideMode.ElideRight, self)
        self.folder_label = ElidedLabel(_home_relative(path.parent), Qt.TextElideMode.ElideMiddle, self)
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
        muted = palette_color_css(palette, palette.ColorRole.PlaceholderText)
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
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(Space.S)
        for action_id, icon in self._OPEN_ACTIONS:
            buttons.addWidget(self._open_button(action_id, icon))
        self._open_buttons[self._OPEN_ACTIONS[0][0]].setDefault(True)  # The theme fills it in the accent color.
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
            button.setToolTip(manager.tooltip(action_id))

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

    def _open_button(self, action_id: str, icon: Icon) -> QPushButton:
        button = QPushButton(_ACTION_NAMES[action_id], self)
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
        # Icons take the color of their button's text. The application palette has it: this widget's stylesheet
        # resolves its own palette from the theme's partial one, whose highlighted text is wrong.
        app_palette = QGuiApplication.palette()
        side = TextRole.BODY.px
        # A wider icon box centers the icon in it, which spaces the icon from the label.
        icon_size = QSize(side + Space.S, side)
        for index, (action_id, icon) in enumerate(self._OPEN_ACTIONS):
            role = app_palette.ColorRole.HighlightedText if index == 0 else app_palette.ColorRole.WindowText
            button = self._open_buttons[action_id]
            button.setIcon(icon.icon(app_palette.color(role)))
            button.setIconSize(icon_size)
        palette = self.palette()
        text = palette.ColorRole.WindowText
        muted = palette_color_css(palette, palette.ColorRole.PlaceholderText)
        # The secondary buttons use the body text color on a light fill instead of the theme's accent-colored text
        # on a near-invisible border, so they read clearly and leave the accent to the primary button. The primary
        # button gets a border in its fill color, so its icon and label line up with theirs.
        self.setStyleSheet(
            f"""
            #AxDevilStartBody {{ background: transparent; }}
            #AxDevilStartBody QPushButton {{ text-align: left; padding-left: {Space.M}px; padding-right: {Space.M}px; }}
            #AxDevilStartBody QPushButton:default {{
                border: 1px solid {qcolor_to_css(app_palette.color(app_palette.ColorRole.Highlight))};
                padding-top: {Space.XS}px;
                padding-bottom: {Space.XS}px;
            }}
            #AxDevilStartBody QPushButton:!default {{
                color: palette(window-text);
                background: {palette_color_css(palette, text, alpha=0.06)};
                border: 1px solid {palette_color_css(palette, text, alpha=0.22)};
                border-radius: {Radius.CONTROL}px;
            }}
            #AxDevilStartBody QPushButton:!default:hover {{
                background: {palette_color_css(palette, text, alpha=0.12)};
                border-color: {palette_color_css(palette, text, alpha=0.32)};
            }}
            #AxDevilStartBody QPushButton:!default:pressed {{
                background: {palette_color_css(palette, text, alpha=0.18)};
            }}
            """
        )
        self._recent_heading.setStyleSheet(f"color: {muted}; padding-top: {Space.S}px;")
