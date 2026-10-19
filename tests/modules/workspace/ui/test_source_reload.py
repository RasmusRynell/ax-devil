"""An open viewer offers to reload when its overlay file is rewritten on disk, and reloading keeps the playhead."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.core.time_units import seconds_to_us
from ax_devil.modules.cache.cache_manager import CacheManager
from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.video_viewer.export.encoder import VideoEncoder
from ax_devil.modules.video_viewer.offline_entry_media import EntryMedia
from ax_devil.modules.video_viewer.offline_video_viewer import OfflineVideoViewerWidget
from ax_devil.modules.workspace.core import OverlayFile, RecentWorkspaces, VideoItem
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.plugins.decoders.mot.provider import MOTChallengeSceneDataProvider
from tests.helpers.workspace import FakePrompts, PlainViewerWidget

_WAIT_FOR_WATCHER_MS = 15_000  # The watcher reports a change after the file has been quiet for a second.
_BROKEN = "broken"


def _mot_decoder(path: Path, **options: Any) -> MOTChallengeSceneDataProvider:
    """Stand in for the MOT decoder plugin, which skips bad rows instead of failing, with one that fails."""
    if path.read_text(encoding="utf-8").startswith(_BROKEN):
        raise ValueError("Unreadable overlay file")
    return MOTChallengeSceneDataProvider(path, **options)


def _write_boxes(path: Path, count: int) -> None:
    """Write *count* boxes on each of the clip's three frames."""
    rows = [f"{frame},{box},10,10,8,8,1,1,1" for frame in (1, 2, 3) for box in range(1, count + 1)]
    path.write_text("\n".join(rows), encoding="utf-8")


def _shown_boxes(viewer: OfflineVideoViewerWidget) -> int:
    """Return how many boxes the overlay holds at the frame on screen."""
    assert viewer._runtime is not None and viewer._runtime.presented_frame is not None
    source = viewer._runtime.lanes[0].overlay_source
    assert source is not None
    shown = viewer._runtime.presented_frame
    overlay = source.get_overlay_at_frame(FrameIdentifier(shown.frame, seconds_to_us(shown.seconds)))
    assert overlay is not None
    return len(overlay.content.entities)


def _banner(viewer: ViewerWidget) -> Banner | None:
    """Return the banner the viewer shows, or None."""
    return next((banner for banner in viewer.findChildren(Banner) if not banner.isHidden()), None)


def _banner_text(viewer: OfflineVideoViewerWidget) -> str:
    banner = _banner(viewer)
    assert banner is not None
    return " ".join(label.text() for label in banner.findChildren(QLabel))


def test_a_rewritten_overlay_file_offers_a_reload_that_keeps_the_frame(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    render_catalog_manager: SceneRenderCatalogManager,
) -> None:
    """The user sees the new overlay at the same frame after Reload, and the old view after a broken rewrite."""
    monkeypatch.setattr(CacheManager, "_get_base_cache_dir", lambda self: tmp_path / "cache")
    monkeypatch.setattr(
        "ax_devil.modules.video_viewer.offline_entry_media.get_file_decoder_factory", lambda _: _mot_decoder
    )
    video = tmp_path / "clip.mp4"
    encoder = VideoEncoder(video, width=32, height=24, fps=1)
    for index in range(3):
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(QColor("gray"))
        encoder.write_frame(image, timestamp_us=seconds_to_us(index))
    encoder.finish()
    overlay = tmp_path / "boxes.txt"
    _write_boxes(overlay, 1)

    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(default_resolution_context(), in_background=False),
        unsaved_folder=tmp_path / "state" / "unsaved-workspaces",
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "state" / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    try:
        session.add_items([VideoItem(video=video, overlays=(OverlayFile(overlay, "MOT_FILE"),))])
        qtbot.waitUntil(lambda: isinstance(session.focused_widget(), OfflineVideoViewerWidget))
        viewer = session.focused_widget()
        assert isinstance(viewer, OfflineVideoViewerWidget)
        qtbot.waitUntil(lambda: viewer._runtime is not None)
        viewer.pause_playback()
        viewer.set_playback_speed(2.0)
        viewer._jump_to_frame(2)
        qtbot.waitUntil(
            lambda: (
                viewer._runtime is not None
                and viewer._runtime.presented_frame is not None
                and viewer._runtime.presented_frame.frame == 2
            )
        )
        assert _shown_boxes(viewer) == 1
        assert _banner(viewer) is None

        _write_boxes(overlay, 2)
        qtbot.waitUntil(lambda: _banner(viewer) is not None, timeout=_WAIT_FOR_WATCHER_MS)
        assert "Source changed, reload current viewer?" in _banner_text(viewer)
        assert _shown_boxes(viewer) == 1  # Nothing changes until the user agrees.

        old_runtime = viewer._runtime
        banner = _banner(viewer)
        assert isinstance(banner, Banner)
        banner.action.click()
        qtbot.waitUntil(lambda: viewer._runtime is not old_runtime)
        qtbot.waitUntil(lambda: viewer._runtime is not None and viewer._runtime.presented_frame is not None)
        assert _banner(viewer) is None
        assert viewer._runtime is not None
        assert viewer._runtime.presented_frame is not None and viewer._runtime.presented_frame.frame == 2
        assert viewer._runtime.is_playing is False
        assert viewer._playback_speed == 2.0
        assert _shown_boxes(viewer) == 2

        overlay.write_text(_BROKEN, encoding="utf-8")
        qtbot.waitUntil(lambda: _banner(viewer) is not None, timeout=_WAIT_FOR_WATCHER_MS)
        shown = viewer._runtime
        banner = _banner(viewer)
        assert isinstance(banner, Banner)
        banner.action.click()
        qtbot.waitUntil(lambda: _banner(viewer) is not None)
        assert "Could not reload" in _banner_text(viewer)
        assert "Unreadable overlay file" in _banner_text(viewer)
        assert viewer._runtime is shown
        assert _shown_boxes(viewer) == 2

        # A reload failing after the source changed again leaves the newer offer in place.
        viewer.offer_reload()
        viewer._on_entry_opened(0, None, True, EntryMedia(error="late failure"))
        assert "Source changed" in _banner_text(viewer)
    finally:
        session.cleanup()


def test_changes_arriving_while_an_offer_waits_keep_the_one_banner(qtbot: QtBot) -> None:
    """Several changes show one offer; Dismiss removes it until the next change, and Reload reloads."""

    class Reloadable(PlainViewerWidget):
        reloads = 0

        def reload(self) -> None:
            self.reloads += 1

    viewer = Reloadable()
    qtbot.addWidget(viewer)

    for _ in range(3):
        viewer.offer_reload()
    first = _banner(viewer)
    assert first is not None and first.dismiss is not None
    first.dismiss.click()
    assert _banner(viewer) is None

    viewer.offer_reload()
    second = _banner(viewer)
    assert second is not None
    second.action.click()
    assert viewer.reloads == 1
    assert _banner(viewer) is None
