"""The Offline view: the workspace's data from files, its videos and playlists, and the resolvers that add playlists,
with a button to add a video."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.workspace.ui.sections_view import SectionsView

OFFLINE_VIEW_ID = "offline"


class OfflineView(SectionsView):
    """The sections it is given, with an Add video button under them; ``shown`` reports it came on screen and
    ``add_video_requested`` that the user wants to add a video."""

    shown = Signal()
    add_video_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(OFFLINE_VIEW_ID, "Offline", Icon.VIDEO, search_placeholder="Search files", parent=parent)
        self.add_footer_button("Add video", Icon.ADD).clicked.connect(self.add_video_requested)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Report that the view came on screen, so its owner can bring it up to date."""
        super().showEvent(event)
        self.shown.emit()
