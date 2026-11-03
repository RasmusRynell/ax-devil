"""The Appearance popover of the media tools: which catalog draws this view, and which details it draws."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.popover import Popover
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection

from .media_tools_section import MediaToolsSection
from .overlay_visibility_widget import OverlayVisibilityWidget
from .scene_render_catalog_selector import SceneRenderCatalogSelector


class AppearancePopover(Popover):
    """The catalog selector and the detail chips of one view, in a popover that stays open while they are used.

    Opening the catalog viewer closes it first; making a catalog the default or applying it to all open views leaves it
    open, so the result shows in the selector.
    """

    catalogViewerRequested = Signal()

    def __init__(self, selection: SceneRenderCatalogSelection, parent: QWidget) -> None:
        """Create the closed popover for *selection*, owned by *parent*."""
        super().__init__(parent)
        selector = SceneRenderCatalogSelector(selection, self)
        selector.catalogViewerRequested.connect(self.close)
        selector.catalogViewerRequested.connect(self.catalogViewerRequested.emit)
        self._details = OverlayVisibilityWidget(selection, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.M, Space.M, Space.M, Space.M)
        layout.setSpacing(Space.S)
        layout.addWidget(self._caption(MediaToolsSection.RENDER_CATALOG))
        layout.addWidget(selector)
        layout.addSpacing(Space.S)
        layout.addWidget(self._caption(MediaToolsSection.OVERLAY_DETAILS))
        layout.addWidget(self._details)

    def cleanup(self) -> None:
        """Disconnect the detail chips from the selection when the owning view closes."""
        self._details.cleanup()

    def _caption(self, section: MediaToolsSection) -> QLabel:
        caption = QLabel(section.title, self)
        caption.setStyleSheet("color: palette(placeholder-text);")
        caption.setToolTip(section.help_html)
        TextRole.CAPTION.apply(caption)
        return caption
