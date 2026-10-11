"""The Playlists view: the playlists in the workspace, and the resolvers that can add more."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.ui.sections_view import SectionsView

PLAYLISTS_VIEW_ID = "playlists"


class PlaylistsView(SectionsView):
    """The sections it is given, with a note on where more resolvers come from; ``shown`` reports it came on screen."""

    shown = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(PLAYLISTS_VIEW_ID, "Playlists", Icon.PLAYLIST, parent)
        self.add_padded(muted_label("More resolvers come from plugins."), top=Space.S, bottom=Space.M)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Report that the view came on screen, so its owner can bring it up to date."""
        super().showEvent(event)
        self.shown.emit()
