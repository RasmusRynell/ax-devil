"""The look every sidebar view gives a browser row and a browser section, built from the chrome list kit."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QToolButton, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import ButtonKind, SmallButton
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.workspace.ui.browser_rows import (
    RowAction,
    WorkspaceBrowserIconKind,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)

# Each kind keeps one icon and color, so live streams, videos, and playlists read apart in a mixed workspace.
_KIND_MARKS: dict[WorkspaceBrowserIconKind, tuple[Icon, KindColor | StatusColor]] = {
    "live_video": (Icon.LIVE_VIDEO, KindColor.LIVE),
    "video": (Icon.VIDEO, KindColor.VIDEO),
    "playlist": (Icon.PLAYLIST, KindColor.PLAYLIST),
    "overlay": (Icon.OVERLAY, KindColor.OVERLAY),
    "unavailable": (Icon.WARNING, StatusColor.WARNING),
}


def section_caption(section: WorkspaceBrowserSection) -> SectionCaption:
    """Return the caption that heads *section*: its title and row count, marked in its kind's color or muted."""
    kind = section.kind
    mark = KindColor.MUTED if kind.addable else _KIND_MARKS[kind.icon_kind][1]
    return SectionCaption(kind.title, len(section.rows), mark.color)


class _ConsiderationToggle(QToolButton):
    """Small eye toggle used to include or exclude a row from playback."""

    def __init__(self, *, considered: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setCheckable(True)
        self.setChecked(considered)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            """
            QToolButton {
                border: none;
                padding: 0px;
                margin: 0px;
                background: transparent;
            }
            QToolButton:hover {
                background: transparent;
            }
            QToolButton:checked {
                background: transparent;
            }
            """
        )
        self.toggled.connect(self._update_icon)
        self._update_icon(considered)

    def _update_icon(self, considered: bool) -> None:
        self.setIcon((Icon.SHOWN if considered else Icon.HIDDEN).icon())
        self.setToolTip(
            "Included in playback layout/navigation. Click to exclude."
            if considered
            else "Excluded from playback layout/navigation. Click to include."
        )


def _action_button(action: RowAction) -> SmallButton:
    """Return the button that offers *action*."""
    button = SmallButton(action.label, action.icon, ButtonKind.PRIMARY if action.primary else ButtonKind.NORMAL)
    button.setToolTip(action.tooltip)
    return button


class SourceRow(ListRow):
    """*row* as a list row: its kind's mark, its name over its detail, its action, and its eye toggle.

    The mark is muted and the name dimmed as the row says; a row open in a viewer has the accent edge. Hovering shows
    the full name over the row's tooltip. ``action_requested`` carries the row whose action was pressed and
    ``consideration_changed`` the consideration item and the state the eye asks for; a click on the row itself is
    ``activated``. Kind colors are drawn for the palette at construction, so owners build rows again when the theme
    changes.
    """

    action_requested = Signal(object)  # WorkspaceBrowserRow
    consideration_changed = Signal(object, bool)  # ConsiderationItemRef, considered

    def __init__(self, row: WorkspaceBrowserRow, parent: QWidget | None = None) -> None:
        trailing: list[QWidget] = []
        if row.action is not None:
            button = _action_button(row.action)
            button.clicked.connect(lambda _checked=False: self.action_requested.emit(row))
            trailing.append(button)
        if (ref := row.consideration_ref) is not None:
            toggle = _ConsiderationToggle(considered=row.is_considered)
            toggle.toggled.connect(lambda considered: self.consideration_changed.emit(ref, considered))
            trailing.append(toggle)
        icon, color = _KIND_MARKS[row.icon_kind]
        mark = KindColor.MUTED if row.is_mark_muted else color
        super().__init__(
            row.label,
            icon=icon.icon(mark.color(QGuiApplication.palette())),
            subtitle=row.detail,
            trailing=trailing,
            active=row.is_open,
            dim=row.is_dimmed,
            parent=parent,
        )
        self.row = row
        lines = "".join(f"<br>{escape(line)}" for line in row.tooltip.splitlines())
        self.setToolTip(f"<b>{escape(row.label)}</b>{lines}")
