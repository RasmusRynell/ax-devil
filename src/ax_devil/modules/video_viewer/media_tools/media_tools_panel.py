"""Combined tools panel hosting entity inspection, the event log, object history, filter and overlay controls."""

from __future__ import annotations

from enum import Enum
from functools import partial
from html import escape
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
    QWidgetAction,
)

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.synchronization.timestamp_matching import DEFAULT_TIMESTAMP_MATCH_TOLERANCE_US
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter

from .entity_filter_widget import EntityFilterWidget
from .entity_list_widget import EntityListWidget, EntityScope
from .event_log_widget import EventLogWidget
from .object_history_widget import ObjectHistoryPane
from .overlay_persistence_controls import OverlayPersistenceControls
from .overlay_visibility_widget import OverlayVisibilityWidget
from .scene_render_catalog_selector import SceneRenderCatalogSelector

_TAB_PAGE_MARGINS = (0, Space.M, Space.M, 0)
_POPUP_MARGINS = (Space.M, Space.M, Space.M, Space.M)


class MediaToolsSection(Enum):
    """Sections of the media tools panel."""

    OVERLAY_SYNC = "overlay_sync"
    FILTERS = "filters"
    OVERLAY_PERSISTENCE = "overlay_persistence"
    RENDER_CATALOG = "render_catalog"
    OVERLAY_DETAILS = "overlay_details"
    ENTITIES = "entities"
    EVENTS = "events"

    @property
    def title(self) -> str:
        """Return the section heading."""
        return {
            MediaToolsSection.OVERLAY_SYNC: "Overlay sync",
            MediaToolsSection.FILTERS: "Filters",
            MediaToolsSection.OVERLAY_PERSISTENCE: "Sticky overlays",
            MediaToolsSection.RENDER_CATALOG: "Render catalog",
            MediaToolsSection.OVERLAY_DETAILS: "Overlay details",
            MediaToolsSection.ENTITIES: "Entities",
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
                "Show only entities of the checked types.\n\n"
                "The search box next to it keeps only entities whose id contains the typed text. "
                "Both apply to the overlay and the entity list."
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
            MediaToolsSection.ENTITIES: (
                "Inspect the entities in the currently displayed scene. Expand an entity row to see its details.\n\n"
                "For files, File lists every object in the overlay and highlights those on the displayed frame. "
                "Click an object to see where it is shown, its events and its last observation."
            ),
            MediaToolsSection.EVENTS: (
                "Scene events such as deletes and renames, listed on the video frame where each takes effect.\n\n"
                "For files, the list holds every event in the overlay, each on the first frame at or after its "
                "sample. Events on the displayed frame are highlighted. Click an event to see the objects it "
                "involves; double-click it to jump to its frame. For live streams, events are added when their "
                "overlay is first shown. Times are the video position for files and UTC clock time for live streams."
                "\n\nThe search box keeps events whose text, including their ids, contains the typed text; Filter "
                "offers each kind of event the log holds."
            ),
        }[self]

    @property
    def help_html(self) -> str:
        """Return the section help as rich text."""
        paragraphs = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in self.description.split("\n\n"))
        return f'<table width="300"><tr><td><b>{self.title}</b>{paragraphs}</td></tr></table>'


class MediaToolsPanel(QWidget):
    """Side panel: searchable entity list, Scene event log and persistent overlay options on separate tabs.

    The panel is the Scene inspector sink of its viewer and forwards each update to the tools that follow the
    displayed frame. With a `SceneHistory`, selected objects and events show their history below the tabs.
    """

    overlayPersistenceChanged = Signal(object)  # Emits OverlayPersistenceSettings
    catalogViewerRequested = Signal()
    exportRequested = Signal()  # Emitted when user clicks "Export Video"
    frameRequested = Signal(int)  # Video frame index requested from an event or object history

    def __init__(
        self,
        render_catalog_selection: SceneRenderCatalogSelection,
        parent: Optional[QWidget] = None,
        *,
        filter_model: SessionFilter,
        overlay_settings: OverlayPersistenceSettings | None = None,
        show_export: bool = False,
        scene_history: SceneHistory | None = None,
        timing_controls: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._render_catalog_selection = render_catalog_selection
        self._export_button: QToolButton | None = None
        self._last_scene: Scene | None = None
        self._last_frame_id: FrameIdentifier | None = None
        self._refilter: SceneRefilter | None = None
        self.filter_model = filter_model
        self._filter_widget = EntityFilterWidget(filter_model=self.filter_model, show_title=False)
        self._overlay_controls = OverlayPersistenceControls(initial_settings=overlay_settings, show_title=False)
        self._overlay_controls.settingsChanged.connect(self.overlayPersistenceChanged.emit)
        self._catalog_selector = SceneRenderCatalogSelector(
            self._render_catalog_selection,
            show_title=False,
        )
        self._catalog_selector.catalogViewerRequested.connect(self.catalogViewerRequested.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("mediaToolsTabs")
        self._tabs.setDocumentMode(True)
        # Show every tab: the panel widens to fit them instead of hiding some behind scroll arrows.
        self._tabs.setUsesScrollButtons(False)
        self._object_pane: ObjectHistoryPane | None = None
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._tabs)
        if scene_history is not None:
            self._object_pane = ObjectHistoryPane(scene_history)
            self._object_pane.frameRequested.connect(self.frameRequested.emit)
            self._splitter.addWidget(self._object_pane)
        layout.addWidget(self._splitter)

        corner = QWidget(self)
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(Space.XS)
        catalog_button = self._popup_button("Catalog", MediaToolsSection.RENDER_CATALOG, self._catalog_selector)
        catalog_button.setObjectName("renderCatalogButton")
        catalog_menu = catalog_button.menu()
        assert catalog_menu is not None
        self._catalog_selector.catalogViewerRequested.connect(catalog_menu.close)
        corner_layout.addWidget(catalog_button)
        self._visibility_widget = OverlayVisibilityWidget(self._render_catalog_selection)
        details_button = self._popup_button("Details", MediaToolsSection.OVERLAY_DETAILS, self._visibility_widget)
        details_button.setObjectName("overlayDetailsButton")
        corner_layout.addWidget(details_button)
        if show_export:
            # Export is a one-shot action, so it lives beside the tabs instead of taking a tab.
            self._export_button = QToolButton(self)
            self._export_button.setObjectName("exportVideoButton")
            self._export_button.setIcon(Icon.EXPORT.icon())
            self._export_button.setToolTip("Export video with overlays")
            self._export_button.setAccessibleName("Export video with overlays")
            self._export_button.setAutoRaise(True)
            self._export_button.clicked.connect(self.exportRequested.emit)
            corner_layout.addWidget(self._export_button)
        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self._id_search = QLineEdit(self)
        self._id_search.setText(self.filter_model.id_query)
        self._id_search.setObjectName("entityIdSearch")
        self._id_search.setPlaceholderText("Search id…")
        self._id_search.setClearButtonEnabled(True)
        self._id_search.textChanged.connect(self.filter_model.set_id_query)
        self._filter_button = self._popup_button("Filter", MediaToolsSection.FILTERS, self._filter_widget)
        self._filter_button.setObjectName("entityFilterButton")
        self.filter_model.changed.connect(self._on_filter_changed)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        # The entity list scrolls itself so only visible rows are built each frame.
        self._entity_list = EntityListWidget(show_title=False)
        if scene_history is not None:
            self._entity_list.set_history(scene_history, self.filter_model)
            scope_buttons = QButtonGroup(self)
            for scope in EntityScope:
                scope_button = QToolButton(self)
                scope_button.setObjectName(f"entityScope_{scope.value}")
                scope_button.setText(scope.title)
                scope_button.setToolTip(scope.tooltip)
                scope_button.setCheckable(True)
                scope_button.setAutoRaise(True)
                scope_button.setChecked(scope is EntityScope.FRAME)
                if scope is EntityScope.FILE and not self.filter_model.supports_history_filtering:
                    scope_button.setEnabled(False)
                    scope_button.setToolTip("Whole-file scope requires classification filters from the decoder.")
                scope_button.clicked.connect(partial(self._entity_list.set_scope, scope))
                scope_buttons.addButton(scope_button)
                toolbar.addWidget(scope_button)
        toolbar.addWidget(self._id_search, 1)
        toolbar.addWidget(self._filter_button)
        entities_page = QWidget()
        entities_page.setObjectName("mediaToolsEntities")
        entities_layout = QVBoxLayout(entities_page)
        entities_layout.setContentsMargins(*_TAB_PAGE_MARGINS)
        entities_layout.addLayout(toolbar)
        entities_layout.addWidget(self._entity_list, 1)
        entities_index = self._tabs.addTab(entities_page, MediaToolsSection.ENTITIES.title)
        self._tabs.setTabToolTip(entities_index, MediaToolsSection.ENTITIES.help_html)

        self._event_log = EventLogWidget(scene_history)
        self._event_log.frameRequested.connect(self.frameRequested.emit)
        if self._object_pane is not None:
            self._event_log.eventSelected.connect(self._show_event_history)
            self._entity_list.entitySelected.connect(self._show_object_history)
        events_page = QWidget()
        events_page.setObjectName("mediaToolsEvents")
        events_layout = QVBoxLayout(events_page)
        events_layout.setContentsMargins(*_TAB_PAGE_MARGINS)
        events_layout.addWidget(self._event_log, 1)
        events_index = self._tabs.addTab(events_page, MediaToolsSection.EVENTS.title)
        self._tabs.setTabToolTip(events_index, MediaToolsSection.EVENTS.help_html)

        sections: list[tuple[MediaToolsSection, QWidget]] = []
        if timing_controls is not None:
            sections.append((MediaToolsSection.OVERLAY_SYNC, timing_controls))
        sections.append((MediaToolsSection.OVERLAY_PERSISTENCE, self._overlay_controls))
        options = QWidget()
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(Space.XL)
        for section, content in sections:
            options_layout.addWidget(self._section_widget(section, content))
        options_page = self._scrollable_page(options)
        options_page.setObjectName("mediaToolsOptions")
        self._tabs.addTab(options_page, "Options")
        self._on_filter_changed()

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
    def entity_list(self) -> EntityListWidget:
        """Return the entity list."""
        return self._entity_list

    @property
    def event_log(self) -> EventLogWidget:
        """Return the Scene event log."""
        return self._event_log

    @property
    def catalog_selector(self) -> SceneRenderCatalogSelector:
        """Return the render catalog selector widget."""
        return self._catalog_selector

    def clear(self) -> None:
        """Clear the frame-following tools."""
        self._last_scene, self._last_frame_id, self._refilter = None, None, None
        self._entity_list.clear()
        self._event_log.clear()

    def update_scene(
        self,
        scene: Scene | None,
        frame_id: FrameIdentifier | None,
        metadata: dict[str, Any] | None,
        refilter: SceneRefilter | None = None,
    ) -> None:
        """Forward the Scene shown for a displayed frame to every tool that follows it.

        Repeating the previous frame and Scene changes nothing, so it is dropped. Each tool defers its own work while
        hidden.
        """
        if scene is self._last_scene and frame_id == self._last_frame_id:
            return
        self._last_scene, self._last_frame_id, self._refilter = scene, frame_id, refilter
        self._entity_list.update_scene(scene, frame_id, metadata, refilter)
        self._event_log.update_scene(scene, frame_id, metadata)
        if self._object_pane is not None and frame_id is not None:
            self._object_pane.show_frame(scene, frame_id.sequence_id)

    def set_scene_history(self, history: SceneHistory) -> None:
        """Replace the history after a change in how overlay samples are selected."""
        assert self._object_pane is not None, "History tools exist only for panels created with a history"
        self._entity_list.set_history(history, self.filter_model)
        self._event_log.set_history(history)
        self._object_pane.set_history(history)

    def _show_event_history(self, event: FrameEvent) -> None:
        assert self._object_pane is not None
        opening = self._object_pane.isHidden()
        self._object_pane.show_event(event)
        self._share_height_when(opening)

    def _show_object_history(self, entity_id: str) -> None:
        assert self._object_pane is not None
        opening = self._object_pane.isHidden()
        self._object_pane.show_object(entity_id)
        self._share_height_when(opening)

    def _share_height_when(self, opening: bool) -> None:
        """Give a newly opened object pane half of the panel."""
        if opening:
            half = self._splitter.height() // 2
            self._splitter.setSizes([self._splitter.height() - half, half])

    def set_export_enabled(self, enabled: bool) -> None:
        """Enable or disable the export button."""
        if self._export_button is not None:
            self._export_button.setEnabled(enabled)

    def _on_filter_changed(self) -> None:
        if self._id_search.text().strip() != self.filter_model.id_query:
            previous_block = self._id_search.blockSignals(True)
            self._id_search.setText(self.filter_model.id_query)
            self._id_search.blockSignals(previous_block)
        self._entity_list.refilter()
        if self._object_pane is not None and self._refilter is not None and self._last_frame_id is not None:
            self._last_scene = self._refilter()
            self._object_pane.show_frame(self._last_scene, self._last_frame_id.sequence_id)
        enabled, total = self.filter_model.enabled_count, len(self.filter_model.options)
        self._filter_button.setText("Filter" if enabled == total else f"Filter {enabled}/{total}")

    def _popup_button(self, text: str, section: MediaToolsSection, content: QWidget) -> QToolButton:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(*_POPUP_MARGINS)
        container_layout.addWidget(content)

        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        button = MenuButton(text, menu, self)
        button.setToolTip(section.help_html)
        return button

    def _section_widget(self, section: MediaToolsSection, content: QWidget) -> QWidget:
        title = QLabel(section.title)
        TextRole.STRONG.apply(title)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._create_help_button(section))

        widget = QWidget()
        widget.setObjectName(f"mediaToolsSection_{section.value}")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)
        layout.addLayout(header)
        layout.addWidget(content)
        return widget

    @staticmethod
    def _scrollable_page(content: QWidget) -> QScrollArea:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(*_TAB_PAGE_MARGINS)
        page_layout.addWidget(content)
        page_layout.addStretch(1)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(page)
        return scroll_area

    def _create_help_button(self, section: MediaToolsSection) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName(f"mediaToolsSectionInfo_{section.value}")
        button.setToolTip(section.help_html)
        button.setAccessibleName(f"About {section.title}")
        button.setText("i")
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)

        def apply_size() -> None:
            # A circle one row high around the "i".
            size = Height.ROW.px
            button.setFont(TextRole.SMALL_STRONG.font())
            button.setFixedSize(size, size)
            button.setStyleSheet(
                f"""
                QToolButton {{
                    border: none;
                    border-radius: {size // 2}px;
                    color: palette(placeholder-text);
                    padding: 0;
                }}
                QToolButton:hover, QToolButton:focus {{
                    color: palette(text);
                    background: palette(midlight);
                }}
                """
            )

        follow_appearance(button, apply_size)
        button.clicked.connect(lambda: self._show_section_help(button))
        return button

    def _show_section_help(self, button: QToolButton) -> None:
        QToolTip.hideText()
        palette = self.palette()
        foreground = palette.color(QPalette.ColorRole.Text)
        help_text = f'<div style="color: {foreground.name()}">{button.toolTip()}</div>'
        QWhatsThis.showText(button.mapToGlobal(button.rect().bottomLeft()), help_text, button)
        popup = QApplication.activePopupWidget()
        if popup is not None:
            palette.setColor(QPalette.ColorRole.ToolTipBase, palette.color(QPalette.ColorRole.Window))
            palette.setColor(QPalette.ColorRole.ToolTipText, foreground)
            popup.setPalette(palette)
