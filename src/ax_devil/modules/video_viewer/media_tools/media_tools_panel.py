"""Side panel of a video: its objects and events as pages, with appearance and overlay controls above them."""

from __future__ import annotations

import re
from collections.abc import Callable
from time import monotonic
from typing import Any

from PySide6.QtCore import QSize, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QHideEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.core.time_units import seconds_to_ms
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.filtering.session_filter import SessionFilter
from ax_devil.modules.object_inspection.pinned_fields import PinnedFields
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.scene.rendering import SceneRenderCatalogSelection
from ax_devil.modules.video_player.ui.frame_display import FrameDisplay
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings
from ax_devil.modules.video_viewer.scene_inspection import SceneRefilter

from .appearance_popover import AppearancePopover
from .event_page import EventPage
from .events_page import EventsPage
from .media_tools_section import MediaToolsSection
from .object_page import ObjectPage
from .objects_page import ObjectsPage
from .overlay_persistence_controls import OverlayPersistenceControls
from .page_stack import EventPageKey, ObjectPageKey, PageKey, PageStack
from .panel_tab_row import PanelTabRow

_INSPECTION_INTERVAL_S = 0.1

_HISTORY_NOTICE_MARGINS = (0, Space.M, Space.M, 0)
_MIN_CHARS = 37
"""Average characters of body text the panel always has room for: the tabs with their counts, and at least the pages."""


class MediaToolsPanel(QWidget):
    """Side panel: the displayed frame's objects and the Scene events as pages, with appearance and overlay controls.

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

        *export_action* belongs to the viewer and is listed in the ⋯ menu; *timing_controls* (offline videos) become
        its Overlay sync section.
        """
        super().__init__(parent)

        self._playing = False
        self._last_refresh_at = float("-inf")
        self._idle_refresh = QTimer(self)
        self._idle_refresh.setSingleShot(True)
        self._idle_refresh.setInterval(round(seconds_to_ms(_INSPECTION_INTERVAL_S)))
        self._idle_refresh.timeout.connect(self._refresh_visible)
        self._render_catalog_selection = render_catalog_selection
        self._last_scene: Scene | None = None
        self._last_frame_id: FrameIdentifier | None = None
        self._refilter: SceneRefilter | None = None
        self._source_count: int | None = None
        self._history = scene_history
        self._pending_object: ObjectPageKey | None = None
        self._followed: ObjectPage | EventPage | None = None  # The drilled page on top, which follows the frame
        self._page_builders: dict[type, Callable[[Any], ObjectPage | EventPage]] = {
            ObjectPageKey: self._build_object_page,
            EventPageKey: self._build_event_page,
        }
        # Where a page's key is in a replaced history; an object page does not depend on it.
        self._key_resolvers: dict[type, Callable[[Any], PageKey | None]] = {
            ObjectPageKey: lambda key: key,
            EventPageKey: self._resolve_event_key,
        }
        self._fold_state: dict[str, bool] = {}
        self._pinned_fields = PinnedFields()
        self.filter_model = filter_model
        self._overlay_controls = OverlayPersistenceControls(initial_settings=overlay_settings, show_title=False)
        self._overlay_controls.settingsChanged.connect(self.overlayPersistenceChanged.emit)
        self._appearance = AppearancePopover(self._render_catalog_selection, self)
        self._appearance.catalogViewerRequested.connect(self.catalogViewerRequested.emit)

        self._objects = ObjectsPage(filter_model, self)
        self._objects.objectActivated.connect(self._open_object)
        self._objects.setObjectName("mediaToolsObjects")
        self._events = EventsPage(scene_history, self)
        self._events.setObjectName("mediaToolsEvents")
        self._events.eventActivated.connect(self._open_event_page)
        roots: list[tuple[MediaToolsSection, QWidget]] = [
            (MediaToolsSection.OBJECTS, self._objects),
            (MediaToolsSection.EVENTS, self._events),
        ]
        self._stack = PageStack({section.title: root for section, root in roots}, self._build_page, self)
        self._stack.pageChanged.connect(self._on_page_changed)

        self._tab_row = PanelTabRow(
            [section for section, _root in roots],
            appearance=self._appearance,
            overlay_controls=self._overlay_controls,
            timing_controls=timing_controls,
            export_action=export_action,
            parent=self,
        )
        self._tab_roots = [root for _section, root in roots]
        self._tab_row.tabs.currentChanged.connect(lambda index: self._stack.show_root(self._tab_roots[index]))
        self._objects.countChanged.connect(lambda count: self._tab_row.set_count(0, count))
        self._events.countChanged.connect(lambda count: self._tab_row.set_count(1, count))
        self._events.rowsChanged.connect(self._refresh_event_steps)
        self._tab_row.set_count(0, self._objects.count)
        self._tab_row.set_count(1, self._events.count)

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
            self._objects.set_history(scene_history)
        self.filter_model.changed.connect(self._on_filter_changed)
        self._on_filter_changed()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Return the same narrowest width whichever page shows, so the panel never changes width with its contents.

        It is the room for the tabs with counts of several digits, or the page header and root pages if wider: those
        depend on the text size and the columns alone, and a drilled page is built from the same lists. The tab row
        itself grows with its counts, so it cannot set this.
        """
        tabs = _MIN_CHARS * QFontMetrics(QApplication.font()).averageCharWidth()
        width = max(tabs, self._stack.narrowest_width())
        return QSize(width, super().minimumSizeHint().height())

    def cleanup(self) -> None:
        """Disconnect persistent selection observers when the owning view closes."""
        self._idle_refresh.stop()
        self._appearance.cleanup()
        self._overlay_controls.cleanup()

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

    def clear(self) -> None:
        """Clear the frame-following tools."""
        self._idle_refresh.stop()
        self._last_refresh_at = float("-inf")
        frame_id = self._last_frame_id
        self._last_scene, self._last_frame_id, self._refilter, self._source_count = None, None, None, None
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
        source_count: int | None = None,
    ) -> None:
        """Forward the Scene shown for a displayed frame, with its *source_count* before filtering, to every page.

        Pages retain only the latest snapshot; live events are recorded before visual updates are coalesced.
        During playback the visible page refreshes at most ten times a second, on an arriving frame. A trailing
        single-shot catches a stalled source; each arrival restarts it, so it never competes with steady playback.
        """
        if scene is self._last_scene and frame_id == self._last_frame_id:
            return
        self._last_scene, self._last_frame_id, self._refilter = scene, frame_id, refilter
        self._source_count = source_count
        self._objects.update_scene(
            scene, frame_id.sequence_id if frame_id is not None else None, source_count=source_count, defer=True
        )
        self._events.update_scene(scene, frame_id, metadata, defer=True)
        self._follow_frame(defer=True)
        if not self.isVisible():
            return
        if not self._playing or monotonic() - self._last_refresh_at >= _INSPECTION_INTERVAL_S:
            self._refresh_visible()
        else:
            self._idle_refresh.start()

    def set_playing(self, playing: bool) -> None:
        """Allow skipped inspection states during playback; pausing immediately shows the displayed frame."""
        self._playing = playing
        if not playing:
            self.refresh_scene()

    def refresh_scene(self) -> None:
        """Catch up after an inspection action and let the next presented frame through immediately (e.g. a seek)."""
        self._refresh_visible()
        self._last_refresh_at = float("-inf")

    def _refresh_visible(self) -> None:
        self._idle_refresh.stop()
        if not self.isVisible():
            return
        self._last_refresh_at = monotonic()
        self._objects.refresh()
        self._events.refresh()
        if self._followed is not None:
            self._followed.refresh()
        self._refresh_event_steps()

    def set_scene_history(self, history: SceneHistory) -> None:
        """Replace the history after a change in how overlay samples are selected."""
        self._history = history
        self._history_notice.setVisible(False)
        self._objects.set_history(history)
        self._events.set_history(history)
        # An open event may now sit on another frame, or be gone; its page then follows it, or closes.
        self._stack.remap(lambda key: self._key_resolvers[type(key)](key))
        if self._followed is not None:
            self._followed.set_history(history)
        self._stack.refresh_steps()

    def mark_history_out_of_date(self, reason: str) -> None:
        """Say why the history shown is out of date; the next `set_scene_history` replaces the notice."""
        # An error names paths and lanes, which are one word to a label; it may break after their separators.
        self._history_notice.setText(re.sub(r"([/\\._:-])", "\\1\u200b", reason))
        self._history_notice.setVisible(True)

    def show_object(self, entity_id: str) -> None:
        """Open the page of *entity_id*, replacing the object page already open, else on top of what is showing.

        While the panel is hidden the choice is only remembered and applies when it is next shown.
        """
        self._pending_object = ObjectPageKey(entity_id)
        if self.isVisible():
            self._show_pending_object()

    def link_selection(self, display: FrameDisplay) -> None:
        """Keep the object selected on *display* and the object page open here the same.

        Clicking an object on the video opens its page; moving between objects here moves the selection on the video.
        Clearing the selection on the video leaves a page that is open, but drops an object chosen while the panel was
        hidden so that opening the panel does not select it again.
        """
        display.objectSelected.connect(self._show_selected)
        self.objectSelected.connect(display.select_object)

    def _show_selected(self, entity_id: str | None) -> None:
        if entity_id is None:
            self._pending_object = None
        else:
            self.show_object(entity_id)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        """Open the object chosen while the panel was hidden."""
        super().showEvent(event)
        self._show_pending_object()
        self._stack.refresh_steps()
        self.refresh_scene()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """Stop the idle refresh while hidden; pages keep the latest snapshot for their next showing."""
        self._idle_refresh.stop()
        super().hideEvent(event)

    def _refresh_event_steps(self) -> None:
        """Enable ▲▼ of an open event page again when live events rotate under it, even at a constant count.

        Only for pages opened from the events list, and only while the panel shows: asking would refilter the list per
        event, and `showEvent` catches up when the panel opens again.
        """
        if self.isVisible() and self._stack.root() is self._events:
            self._stack.refresh_steps()

    def _show_pending_object(self) -> None:
        key, self._pending_object = self._pending_object, None
        if key is not None:
            self._stack.jump(key, self._objects if self._stack.root() is self._objects else None)

    def _open_object(self, entity_id: str) -> None:
        self._stack.push(ObjectPageKey(entity_id), self._objects)

    def _open_event(self, event: FrameEvent) -> None:
        self._open_event_page(EventPageKey(event))

    def _open_event_page(self, key: EventPageKey) -> None:
        self._stack.push(key, self._events)

    def _resolve_event_key(self, key: EventPageKey) -> EventPageKey | None:
        """Find the key in a replaced history: the nearest equal event, keeping its occurrence while it remains."""
        history = self._history
        if history is None:
            return key
        event = history.nearest_event(key.event)
        if event is None:
            return None
        return EventPageKey(event, min(key.occurrence, history.events.count(event) - 1))

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
        page = ObjectPage(key.entity_id, self._fold_state, self._pinned_fields)
        page.eventActivated.connect(self._open_event)
        return page

    def _build_event_page(self, key: EventPageKey) -> EventPage:
        page = EventPage(key)
        page.objectActivated.connect(self._open_object)
        page.eventActivated.connect(self._open_event_page)
        return page

    def _on_page_changed(self, key: PageKey | None) -> None:
        self._tab_row.setVisible(key is None)
        if key is None:
            self._followed = None
        self.objectSelected.emit(key.entity_id if isinstance(key, ObjectPageKey) else None)

    def _follow_frame(self, *, defer: bool = False) -> None:
        if self._followed is not None and self._last_frame_id is not None:
            self._followed.update_scene(self._last_scene, self._last_frame_id.sequence_id, defer=defer)

    def _on_filter_changed(self) -> None:
        self._objects.refilter()
        if self._refilter is not None and self._last_frame_id is not None:
            self._last_scene = self._refilter()
            self._objects.update_scene(
                self._last_scene, self._last_frame_id.sequence_id, source_count=self._source_count
            )
            self._follow_frame()
