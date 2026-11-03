"""Saved-item editor screenshot scenarios, rendered only inside the isolated pytest display."""

from dataclasses import replace
from pathlib import Path

from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.workspace.core import LiveOverlayMode, LiveStreamItem, OverlayFile, PlaylistItem, VideoItem
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from tests.helpers.workspace import inline_resolver
from tools.ui_shots_config import OUT, TEXT_SIZES
from tools.ui_shots_navigation import grab_next_dialog


def edit_screenshots(qtbot: QtBot, root: Path, theme: str) -> None:
    """Capture populated editors at medium and larger text sizes."""
    OUT.mkdir(parents=True, exist_ok=True)
    clip, tracks = root / "entrance.mp4", root / "tracks.xml"
    clip.touch()
    tracks.write_text("<MetadataStream />")
    video = VideoItem(video=clip, overlays=(OverlayFile(tracks, "ONVIF_XML"),), label="Entrance")
    live = LiveStreamItem(
        host="$AX_DEVIL_TARGET_ADDR",
        username="operator",
        overlay_mode=LiveOverlayMode.RTSP,
        handler_type="ONVIF_XML",
        camera_head=2,
        resolution="640x480",
    )
    playlist = PlaylistItem(
        resolver="folder_pair",
        label="Night shift",
        settings={
            "videos_dir": str(root),
            "overlays_dir": str(root),
            "handler_type": "ONVIF_XML",
        },
    )
    parent = ChromeWindow(use_custom_frame=True)
    qtbot.addWidget(parent)
    parent.resize(1400, 1000)
    parent.show()
    apply_theme(theme)
    for text_size in TEXT_SIZES:
        apply_text_size(text_size.body_px)
        dialogs: dict[str, BaseDialog] = {
            "edit-video": AddVideoDialog(parent, existing=video),
            "edit-live-stream": AddLiveStreamDialog(parent, existing=live),
            "edit-live-stream-none": AddLiveStreamDialog(
                parent, existing=replace(live, overlay_mode=LiveOverlayMode.NONE, handler_type=None)
            ),
            "edit-playlist": AddPlaylistDialog(inline_resolver(), parent, existing=playlist),
            "edit-unavailable-playlist": AddPlaylistDialog(
                inline_resolver(), parent, existing=PlaylistItem(resolver="missing")
            ),
        }
        for name, dialog in dialogs.items():
            grab_next_dialog(f"{theme}-{text_size.value}-{name}")
            dialog.exec()
            dialog.close()
    parent.close()
