"""Side panel of a video: its objects and events as pages, with the overlay controls in a menu."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from html import escape
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.synchronization.timestamp_matching import DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter

from .entity_filter_widget import EntityFilterWidget
from .event_page import EventPage
from .events_page import EventsPage
from .object_page import ObjectPage
from .objects_page import ObjectsPage
from .overlay_persistence_controls import OverlayPersistenceControls
from .overlay_visibility_widget import OverlayVisibilityWidget
from .page_stack import EventPageKey, ObjectPageKey, PageKey, PageStack
from .scene_render_catalog_selector import SceneRenderCatalogSelector

_HISTORY_NOTICE_MARGINS = (0, Space.M, Space.M, 0)
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
                f"within the tolerance ({DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US / 1000:g} ms by default). "
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
                "Hidden details remain available in inspection and hover cards. Reset enables every detail."
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
                "sample. Events on the displayed frame are highlighted. Click an event to open its page; "
                "double-click it to jump to its frame. For live streams, events are added when their overlay is "
                "first shown. Times are the video position for files and UTC clock time for live streams.\n\n"
                "Search by kind:merge or object:17, or by text in the label; ? shows the syntax."
            ),
        }[self]

    @property
    def help_html(self) -> str:
        """Return the section help as rich text."""
        paragraphs = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in self.description.split("\n\n"))
        return f'<table width="300"><tr><td><b>{self.title}</b>{paragraphs}</td></tr></table>'


class MediaToolsPanel(QWidget):
    """Side panel: the displayed frame's objects and the Scene events as pages, and the overlay controls in a menu.

    The roots are an Objects list and an Events list under a tab bar. Opening an object or an event drills into its
    page, which replaces the tabs until the user goes back; ``show_object`` opens an object the same way, for a
    selection made elsewhere. The panel is the Scene inspector sink of its viewer and forwards each update to the pages
    that follow the displayed frame. With a `SceneHistory` the Objects list can also list every object of the file, and
    an object page shows where it appears.
    """

    overlayPersistenceChanged = Signal(object)  # Emits OverlayPersistenceSettings
    catalogViewerRequested = Signal()
    frameRequested = Signal(int)  # Video frame index requested from an event or object page
    objectSelected = Signal(object)  # Entity id of the object whose page opened, or None when none is open

    def __init__(
        self,
        render_catalog_selection: SceneRenderCatalogSelection,
        parent: QWidget | None = None,
        *,
        filter_model: SessionFilter,
        overlay_settings: OverlayPersistenceSettings | None = None,
        export_action: QAction | None = None,
        scene_history: SceneHistory | None = None,
        timing_controls: QWidget | None = None,
    ) -> None:
        """Create the panel for one video's *render_catalog_selection* and session *filter_model*.

        *export_action* belongs to the viewer and is listed in the menu; *timing_controls* (offline videos) become
        its Overlay sync section.
        """
        super().__init__(parent)

        self._render_catalog_selection = render_catalog_selection
        self._last_scene: Scene | None = None
        self._last_frame_id: FrameIdentifier | None = None
        self._refilter: SceneRefilter | None = None
        self._history = scene_history
        self._pending_object: ObjectPageKey | None = None
        self._followed: ObjectPage | EventPage | None = None  # The drilled page on top, which follows the frame
        self._page_builders: dict[type, Callable[[Any], ObjectPage | EventPage]] = {
            ObjectPageKey: self._build_object_page,
            EventPageKey: self._build_event_page,
        }
        self._fold_state: dict[str, bool] = {}
        self.filter_model = filter_model
        self._filter_widget = EntityFilterWidget(filter_model=self.filter_model, show_title=False)
        self._overlay_controls = OverlayPersistenceControls(initial_settings=overlay_settings, show_title=False)
        self._overlay_controls.settingsChanged.connect(self.overlayPersistenceChanged.emit)
        self._catalog_selector = SceneRenderCatalogSelector(
            self._render_catalog_selection,
            show_title=False,
        )
        self._catalog_selector.catalogViewerRequested.connect(self.catalogViewerRequested.emit)
        self._visibility_widget = OverlayVisibilityWidget(self._render_catalog_selection)

        self._objects = ObjectsPage(self)
        self._objects.objectActivated.connect(self._open_object)
        self._objects.setObjectName("mediaToolsObjects")
        self._events = EventsPage(scene_history, self)
        self._events.setObjectName("mediaToolsEvents")
        self._events.eventActivated.connect(self._open_event)
        self._events.frameRequested.connect(self.frameRequested.emit)
        roots: list[tuple[MediaToolsSection, QWidget]] = [
            (MediaToolsSection.OBJECTS, self._objects),
            (MediaToolsSection.EVENTS, self._events),
        ]
        self._stack = PageStack({section.title: root for section, root in roots}, self._build_page, self)
        self._stack.pageChanged.connect(self._on_page_changed)

        self._tabs = QTabBar(self)
        self._tabs.setObjectName("mediaToolsTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setExpanding(False)
        # Show every tab: the panel widens to fit them instead of hiding some behind scroll arrows.
        self._tabs.setUsesScrollButtons(False)
        for section, _root in roots:
            self._tabs.setTabToolTip(self._tabs.addTab(section.title), section.help_html)
        self._tab_roots = [root for _section, root in roots]
        self._tabs.currentChanged.connect(lambda index: self._stack.show_root(self._tab_roots[index]))
        self._objects.countChanged.connect(lambda count: self._set_tab_count(0, MediaToolsSection.OBJECTS, count))
        self._events.countChanged.connect(lambda count: self._set_tab_count(1, MediaToolsSection.EVENTS, count))
        self._events.countChanged.connect(self._stack.refresh_steps)  # Live events arrive under an open event page.
        self._set_tab_count(0, MediaToolsSection.OBJECTS, self._objects.count)
        self._set_tab_count(1, MediaToolsSection.EVENTS, self._events.count)

        self._more_menu = QMenu(self)
        self._more_menu.setToolTipsVisible(True)
        self._more_button = QToolButton(self)
        self._more_button.setObjectName("mediaToolsMoreButton")
        self._more_button.setIcon(Icon.MORE.icon())
        self._more_button.setAutoRaise(True)
        self._more_button.setToolTip("Catalog, overlay settings and export")
        self._more_button.setAccessibleName("More")
        self._more_button.setMenu(self._more_menu)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self._build_menu(export_action, timing_controls)

        self._tab_row = QWidget(self)
        tab_row_layout = QHBoxLayout(self._tab_row)
        tab_row_layout.setContentsMargins(0, 0, Space.S, 0)
        tab_row_layout.setSpacing(Space.S)
        tab_row_layout.addWidget(self._tabs)
        tab_row_layout.addStretch(1)
        tab_row_layout.addWidget(self._more_button)

        self._history_notice = QLabel(self)
        self._history_notice.setObjectName("historyOutOfDateNotice")
        self._history_notice.setWordWrap(True)
        self._history_notice.setContentsMargins(*_HISTORY_NOTICE_MARGINS)
        self._history_notice.setVisible(False)
        follow_appearance(
            self._history_notice,
            lambda: self._history_notice.setStyleSheet(StatusColor.ERROR.css(self._history_notice.palette())),
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._history_notice)
        layout.addWidget(self._tab_row)
        layout.addWidget(self._stack, 1)

        if scene_history is not None:
            self._objects.set_history(scene_history, filter_model)
        self.filter_model.changed.connect(self._on_filter_changed)
        self._on_filter_changed()

    def _build_menu(self, export_action: QAction | None, timing_controls: QWidget | None) -> None:
        """Fill the overflow menu: one submenu per control group, then the viewer's export command."""
        catalog_menu = self._section_menu(MediaToolsSection.RENDER_CATALOG, self._catalog_selector)
        self._catalog_selector.catalogViewerRequested.connect(catalog_menu.close)
        self._catalog_selector.catalogViewerRequested.connect(self._more_menu.close)
        self._section_menu(MediaToolsSection.OVERLAY_DETAILS, self._visibility_widget)
        self._filter_menu = self._section_menu(MediaToolsSection.FILTERS, self._filter_widget)
        self._section_menu(MediaToolsSection.OVERLAY_PERSISTENCE, self._overlay_controls)
        if timing_controls is not None:
            self._section_menu(MediaToolsSection.OVERLAY_SYNC, timing_controls)
        if export_action is not None:
            # Export is a one-shot command, so it sits beside the submenus. The lane owns the action, so this entry
            # and the lane's context menu are one command.
            self._more_menu.addSeparator()
            self._more_menu.addAction(export_action)

    def _section_menu(self, section: MediaToolsSection, content: QWidget) -> QMenu:
        """Add a submenu titled by *section* to the overflow menu that hosts *content*; its help is the tooltip."""
        submenu = self._more_menu.addMenu(section.title)
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
        return submenu

    def cleanup(self) -> None:
        """Disconnect persistent selection observers when the owning view closes."""
        self._visibility_widget.cleanup()

    @property
    def filter_widget(self) -> EntityFilterWidget:
        """Return the embedded entity filter widget."""
        return self._filter_widget

    @property
    def overlay_controls(self) -> OverlayPersistenceControls:
        """Return the overlay persistence controls widget."""
        return self._overlay_controls

    @property
    def objects_page(self) -> ObjectsPage:
        """Return the Objects list, the first root page."""
        return self._objects

    @property
    def events_page(self) -> EventsPage:
        """Return the Scene events list, the Events root page."""
        return self._events

    @property
    def catalog_selector(self) -> SceneRenderCatalogSelector:
        """Return the render catalog selector widget."""
        return self._catalog_selector

    def clear(self) -> None:
        """Clear the frame-following tools."""
        frame_id = self._last_frame_id
        self._last_scene, self._last_frame_id, self._refilter = None, None, None
        self._objects.clear()
        self._events.clear()
        if self._followed is not None and frame_id is not None:
            self._followed.update_scene(None, frame_id.sequence_id)

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Forward the Scene shown for a displayed frame to every page that follows it.

        Repeating the previous frame and Scene changes nothing, so it is dropped. Each page defers its own work while
        hidden.
        """
        if scene is self._last_scene and frame_id == self._last_frame_id:
            return
        self._last_scene, self._last_frame_id, self._refilter = scene, frame_id, refilter
        self._objects.update_scene(scene, frame_id.sequence_id if frame_id is not None else None)
        self._events.update_scene(scene, frame_id, metadata)
        self._follow_frame()

    def set_scene_history(self, history: SceneHistory) -> None:
        """Replace the history after a change in how overlay samples are selected."""
        self._history = history
        self._history_notice.setVisible(False)
        self._objects.set_history(history, self.filter_model)
        self._events.set_history(history)
        if self._followed is not None:
            self._followed.set_history(history)

    def mark_history_out_of_date(self, reason: str) -> None:
        """Say why the history shown is out of date; the next `set_scene_history` replaces the notice."""
        self._history_notice.setText(reason)
        self._history_notice.setVisible(True)

    def show_object(self, entity_id: str) -> None:
        """Open the page of *entity_id*, replacing the object page already open, else on top of what is showing.

        While the panel is hidden the choice is only remembered and applies when it is next shown.
        """
        self._pending_object = ObjectPageKey(entity_id)
        if self.isVisible():
            self._show_pending_object()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Open the object chosen while the panel was hidden."""
        super().showEvent(event)
        self._show_pending_object()

    def _show_pending_object(self) -> None:
        key, self._pending_object = self._pending_object, None
        if key is not None:
            self._stack.jump(key, self._objects if self._stack.root() is self._objects else None)

    def _open_object(self, entity_id: str) -> None:
        self._stack.push(ObjectPageKey(entity_id), self._objects)

    def _open_event(self, event: FrameEvent) -> None:
        self._stack.push(EventPageKey(event), self._events)

    def _build_page(self, key: PageKey) -> QWidget:
        """Build the page of *key*, which follows the displayed frame from now on."""
        page = self._page_builders[type(key)](key)
        page.frameRequested.connect(self.frameRequested.emit)
        page.set_history(self._history)
        self._followed = page
        if self._last_frame_id is not None:
            page.update_scene(self._last_scene, self._last_frame_id.sequence_id)
        return page

    def _build_object_page(self, key: ObjectPageKey) -> ObjectPage:
        page = ObjectPage(key.entity_id, self._fold_state)
        page.eventActivated.connect(self._open_event)
        return page

    def _build_event_page(self, key: EventPageKey) -> EventPage:
        page = EventPage(key.event)
        page.objectActivated.connect(self._open_object)
        page.eventActivated.connect(self._open_event)
        return page

    def _on_page_changed(self, key: PageKey | None) -> None:
        self._tab_row.setVisible(key is None)
        if key is None:
            self._followed = None
        self.objectSelected.emit(key.entity_id if isinstance(key, ObjectPageKey) else None)

    def _follow_frame(self) -> None:
        if self._followed is not None and self._last_frame_id is not None:
            self._followed.update_scene(self._last_scene, self._last_frame_id.sequence_id)

    def _set_tab_count(self, index: int, section: MediaToolsSection, count: int) -> None:
        self._tabs.setTabText(index, f"{section.title} {count}")

    def _on_filter_changed(self) -> None:
        enabled, total = self.filter_model.enabled_count, len(self.filter_model.options)
        title = MediaToolsSection.FILTERS.title
        self._filter_menu.setTitle(title if enabled == total else f"{title} {enabled}/{total}")
        self._objects.refilter()
        if self._refilter is not None and self._last_frame_id is not None:
            self._last_scene = self._refilter()
            self._objects.update_scene(self._last_scene, self._last_frame_id.sequence_id)
            self._follow_frame()
