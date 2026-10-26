"""The parts of the media tools panel that carry help text, and how one becomes a submenu of its overflow menu."""

from __future__ import annotations

from enum import Enum
from functools import partial
from html import escape

from PySide6.QtWidgets import (
    QLayout,
    QMenu,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ax_devil.core.time_units import us_to_ms
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.synchronization.timestamp_matching import DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US

_MENU_CONTENT_MARGINS = (Space.M, Space.M, Space.M, Space.M)


class MediaToolsSection(Enum):
    """Parts of the media tools panel that carry help text: the root pages and the overlay controls in its menu."""

    OVERLAY_SYNC = "overlay_sync"
    FILTERS = "filters"
    OVERLAY_PERSISTENCE = "overlay_persistence"
    RENDER_CATALOG = "render_catalog"
    OVERLAY_DETAILS = "overlay_details"
    OBJECTS = "objects"
    EVENTS = "events"

    @property
    def title(self) -> str:
        """Return the section heading."""
        return {
            MediaToolsSection.OVERLAY_SYNC: "Overlay sync",
            MediaToolsSection.FILTERS: "Overlay filter",
            MediaToolsSection.OVERLAY_PERSISTENCE: "Sticky overlays",
            MediaToolsSection.RENDER_CATALOG: "Catalog",
            MediaToolsSection.OVERLAY_DETAILS: "Overlay details",
            MediaToolsSection.OBJECTS: "Objects",
            MediaToolsSection.EVENTS: "Events",
        }[self]

    @property
    def description(self) -> str:
        """Return help text explaining the section controls."""
        return {
            MediaToolsSection.OVERLAY_SYNC: (
                "Match overlay data to video frames when timestamps differ slightly.\n\n"
                "After exact and source-provided matching, Previous allows the latest earlier sample "
                f"within the tolerance ({us_to_ms(DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US):g} ms by default). "
                "Exact disables this fallback. Previous can match one sample to several frames."
            ),
            MediaToolsSection.OVERLAY_PERSISTENCE: (
                "Show the latest earlier overlay when no new overlay matches a frame, including after seeks.\n\n"
                "Expiry is measured from the original sample timestamp; pausing does not use it up. "
                "Reused overlay opacity controls how strongly retained overlays are shown."
            ),
            MediaToolsSection.FILTERS: (
                "Show only objects of the checked types. The choice applies to the overlay, the object list and "
                "the exported video."
            ),
            MediaToolsSection.OVERLAY_DETAILS: (
                "Choose which details this view draws. Choices apply immediately, including while paused. "
                "Hidden details remain available in inspection and the hover chip. Reset enables every detail."
            ),
            MediaToolsSection.RENDER_CATALOG: (
                "Choose how overlay entities are drawn. The choice applies to this view only.\n\n"
                "Changes to the catalog file appear here as soon as it is saved. View shows the catalog on example "
                "objects, Reload reads the file again, and ⋯ applies the catalog to every open view or makes it the "
                "default for new ones."
            ),
            MediaToolsSection.OBJECTS: (
                "The objects in the displayed frame. Search by id, class or state, or by any attribute with "
                "field:value or field>0.5; ? shows the syntax.\n\n"
                "For files, File lists every object in the overlay and dims those not in the displayed frame. "
                "Click an object to open its page."
            ),
            MediaToolsSection.EVENTS: (
                "Scene events such as deletes and renames, listed on the video frame where each takes effect.\n\n"
                "For files, the list holds every event in the overlay, each on the first frame at or after its "
                "sample. Events on the displayed frame are highlighted. Click an event to open its page, "
                "which can jump to its frame. For live streams, events are added when their overlay is "
                "first shown. Times are the video position for files and UTC clock time for live streams.\n\n"
                "Search by kind:merge or object:17, or by text in the label; ? shows the syntax."
            ),
        }[self]

    @property
    def help_html(self) -> str:
        """Return the section help as rich text."""
        paragraphs = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in self.description.split("\n\n"))
        return f'<table width="300"><tr><td><b>{self.title}</b>{paragraphs}</td></tr></table>'


def add_section_menu(menu: QMenu, section: MediaToolsSection, content: QWidget) -> QMenu:
    """Add a submenu titled by *section* to *menu* that hosts *content*; the section's help is the tooltip."""
    submenu = menu.addMenu(section.title)
    assert submenu is not None
    submenu.setToolTipsVisible(True)
    submenu.menuAction().setToolTip(section.help_html)
    container = QWidget(submenu)
    container.setToolTip(section.help_html)
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(*_MENU_CONTENT_MARGINS)
    container_layout.addWidget(content)
    action = QWidgetAction(submenu)
    action.setDefaultWidget(container)
    submenu.addAction(action)
    # A widget that is hidden does not pass a size change up to its layout, so the content of a menu closed during
    # a text-size change would be measured at the old size and clip.
    submenu.aboutToShow.connect(partial(_remeasure, container))
    return submenu


def _remeasure(container: QWidget) -> None:
    for layout in container.findChildren(QLayout):
        layout.invalidate()
