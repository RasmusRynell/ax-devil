"""The Offline view: the workspace's data from files, its videos and playlists, with a list under them of what to add:
a video, or a playlist from a resolver."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import cast

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.row_list import RowList
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Height
from ax_devil.modules.workspace.ui.footer_panel import FooterPanel
from ax_devil.modules.workspace.ui.sections_view import SectionsView

OFFLINE_VIEW_ID = "offline"


@dataclass(frozen=True, slots=True)
class PlaylistSource:
    """A playlist resolver the user can add a playlist from: its id, the *name* on its row, and a *tooltip*."""

    resolver_id: str
    name: str
    tooltip: str = ""


class _FittedRowList(RowList):
    """A row list that asks for the height of its rows, and can be squeezed down to a row; it scrolls only then."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        # The theme gives a scroll area with no rule of its own a border, which eats into a height measured to the row.
        self.setStyleSheet(f"QScrollArea {{ background: transparent; border: 0px; margin: 0px; }} {SEE_THROUGH}")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Ask the panel for room again when the rows change, in number or in height."""
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

    def _rows_widget(self) -> QWidget:
        return cast(QWidget, self.widget())

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the size of all the rows."""
        return self._rows_widget().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the width of the rows and the height of one."""
        return QSize(self._rows_widget().minimumSizeHint().width(), Height.ROW.px)


class AddPanel(FooterPanel):
    """What can be added, as a list: a single video, then a row per playlist resolver; it scrolls only when the panel
    is squeezed below the height of its rows.

    ``add_video_requested`` reports a click on the video row, and ``add_playlist_requested`` the id of the resolver
    whose row was clicked.
    """

    add_video_requested = Signal()
    add_playlist_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Add", parent)
        self._list = _FittedRowList()
        self._list.add(_add_row("Single video", Icon.VIDEO, "Add a video file", self.add_video_requested.emit))
        self._sources = self._list.section()
        # The controls' side margins are the rows' own.
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.addWidget(self._list)

    def set_sources(self, sources: Sequence[PlaylistSource]) -> None:
        """List a row for each of *sources* after the video row, in place of the ones listed."""
        self._sources.clear()
        for source in sources:
            add = partial(self.add_playlist_requested.emit, source.resolver_id)
            self._sources.add(_add_row(source.name, Icon.PLAYLIST, source.tooltip, add))


def _add_row(title: str, icon: Icon, tooltip: str, activate: Callable[[], None]) -> ListRow:
    """Return a row that calls *activate* when clicked."""
    row = ListRow(title, icon=icon.icon())
    row.setToolTip(tooltip)
    row.setCursor(Qt.CursorShape.PointingHandCursor)
    row.activated.connect(activate)
    return row


class OfflineView(SectionsView):
    """The sections it is given, with a list of what to add under them; ``shown`` reports it came on screen,
    ``add_video_requested`` that the user wants to add a video and ``add_playlist_requested`` carries the id of the
    resolver they want a playlist from."""

    shown = Signal()
    add_video_requested = Signal()
    add_playlist_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            OFFLINE_VIEW_ID,
            "Offline",
            Icon.VIDEO,
            search_placeholder="Search files",
            empty_text="No videos or playlists",
            parent=parent,
        )
        self._add = AddPanel(self)
        self._add.add_video_requested.connect(self.add_video_requested)
        self._add.add_playlist_requested.connect(self.add_playlist_requested)
        self.set_footer_panel(self._add)

    def set_playlist_sources(self, sources: Sequence[PlaylistSource]) -> None:
        """Offer *sources* under Add, after the video."""
        self._add.set_sources(sources)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Report that the view came on screen, so its owner can bring it up to date."""
        super().showEvent(event)
        self.shown.emit()
