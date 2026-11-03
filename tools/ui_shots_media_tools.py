"""Helpers for the media tools screenshots: saving a shot, adding events, picking objects and a four-lane playlist."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
)
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.scene_history import FrameEvent, SceneHistory
from ax_devil.modules.plugin_system import (
    PLUGIN_API_VERSION,
    PlaylistResolverPlugin,
    PlaylistResolverWidget,
)
from ax_devil.modules.video_player.ui.viewport import FrameViewport
from ax_devil.modules.video_viewer.media_tools import MediaToolsPanel
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    FileVideoSourceSpec,
    OverlayContent,
    PlaylistContent,
    PlaylistEntry,
    PlaylistSettings,
    SeekableVideoContent,
    create_entry_lane,
)
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML
from tools.ui_shots_config import OUT


def save_shot(
    window: QWidget, theme: str, name: str, popup: QWidget | None = None, anchor: QWidget | None = None
) -> None:
    """Save *window* as ``<theme>-<name>.png``.

    An open *popup* (a menu or popover) is painted in below *anchor*, right-aligned, because popups are windows of
    their own and the private display keeps them inside its 800x800 area.
    """
    shot = window.grab()
    if popup is not None and anchor is not None:
        painter = QPainter(shot)
        corner = anchor.mapTo(window, QPoint(anchor.width() - popup.width(), anchor.height()))
        painter.drawPixmap(corner, popup.grab())
        painter.end()
    shot.save(str(OUT / f"{theme}-{name}.png"))


def with_merge_and_split(panel: MediaToolsPanel) -> FrameEvent:
    """Add to *panel*'s history the events no decoder yields: a Merge on the Rename's frame and a later Split.

    Return the Rename event.
    """
    history = panel._history
    assert history is not None
    times = history.objects[0].frame_times_us

    def at(frame: int, kind: str, label: str, *entity_ids: str) -> FrameEvent:
        return FrameEvent(FrameIdentifier(sequence_id=frame, timestamp_us=times[frame]), kind, label, entity_ids)

    rename = next(event for event in history.events if event.kind == "Rename")
    merge = at(rename.frame_id.sequence_id, "Merge", "Merge 1, 7 → 1", "1", "7")
    split = at(45, "Split", "Split 2 → 2, 4", "2", "4")
    panel.set_scene_history(
        SceneHistory(events=(*history.events, merge, split), objects=history.objects, frame_count=history.frame_count)
    )
    return rename


def object_point(viewport: FrameViewport, entity_id: str) -> QPoint:
    """Return the widget position of the centre of object *entity_id*'s box, where a hit test finds that object."""
    provider = viewport._interaction_provider()
    target = viewport._hover_target_rect()
    assert provider is not None and target is not None
    hit = provider.get_hit_by_id(entity_id)
    assert hit is not None
    x, y, width, height = hit.bounds
    centre = QPointF(target.x() + (x + width / 2) * target.width(), target.y() + (y + height / 2) * target.height())
    found = viewport._hit_test((centre.x() - target.x()) / target.width(), (centre.y() - target.y()) / target.height())
    assert found is not None and found.target_id == entity_id
    return centre.toPoint()


def move_pointer(viewport: FrameViewport, pos: QPoint) -> None:
    """Send *viewport* a mouse move to *pos*, as the pointer would, without touching any real input device."""
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)


def click_object(viewport: FrameViewport, entity_id: str) -> None:
    """Click object *entity_id* on *viewport*, then move the pointer onto empty video so the hover chip is gone."""
    pos = object_point(viewport, entity_id)
    move_pointer(viewport, pos)
    cast(Any, QTest).mouseClick(viewport, Qt.MouseButton.LeftButton, pos=pos)
    move_pointer(viewport, QPoint(viewport.width() - 4, viewport.height() - 4))


def grid_panels(qtbot: QtBot, viewer: OfflineVideoViewerWidget) -> list[MediaToolsPanel]:
    """Open the side panel of every lane of *viewer* and return the panels."""
    runtime = viewer._runtime
    assert runtime is not None
    for display in runtime.displays:
        display.set_side_panel_open(True)
    qtbot.wait(800)
    panels: list[MediaToolsPanel] = viewer.findChildren(MediaToolsPanel)
    assert len(panels) == len(runtime.displays)
    return panels


class FourCameras(PlaylistResolverPlugin):
    """A playlist of one entry whose four lanes show copies of one clip, each with the same tracks."""

    @classmethod
    def plugin_id(cls) -> str:
        return "four_cameras"

    @classmethod
    def required_api_version(cls) -> int:
        return PLUGIN_API_VERSION

    @classmethod
    def display_name(cls) -> str:
        return "Four cameras"

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        """Return the playlist of the clips in ``settings["videos"]``, all with the overlay ``settings["overlay"]``."""
        overlay = OverlayContent("tracks", FileOverlaySourceSpec(Path(str(settings["overlay"])), ONVIF_XML))
        lanes = tuple(
            create_entry_lane(
                SeekableVideoContent(display_name=Path(video).name, source_spec=FileVideoSourceSpec(Path(video))),
                default_considered=True,
                overlay=overlay,
            )
            for video in cast(list[str], settings["videos"])
        )
        return [PlaylistContent(display_name="Four cameras", entries=(PlaylistEntry(lanes, default_considered=True),))]

    def create_settings_widget(self) -> PlaylistResolverWidget:
        raise NotImplementedError("The screenshots only open a saved playlist.")
