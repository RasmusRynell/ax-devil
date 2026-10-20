"""Populated editors fit their opening size with usable fields, including at large text sizes."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.theme import apply_text_size, apply_theme
from ax_devil.modules.workspace.core import LiveStreamItem, OverlayFile, PlaylistItem, VideoItem
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from ax_devil.modules.workspace.ui.add_content.add_video_dialog import AddVideoDialog
from tests.helpers.clipping import clipped_text_on_every_page
from tests.helpers.workspace import inline_resolver


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("pixels", [13, 21])
def test_saved_forms_open_without_scrolling_or_squeezed_fields(
    qtbot: QtBot, tmp_path: Path, theme: str, pixels: int
) -> None:
    apply_theme(theme)
    apply_text_size(pixels)
    video = VideoItem(video=tmp_path / "clip.mp4", overlays=(OverlayFile(tmp_path / "tracks.xml", "ONVIF_XML"),))
    playlist = PlaylistItem(
        resolver="folder_pair",
        settings={"videos_dir": str(tmp_path), "overlays_dir": str(tmp_path), "handler_type": "ONVIF_XML"},
    )
    for dialog in (
        AddVideoDialog(existing=video),
        AddLiveStreamDialog(existing=LiveStreamItem(host="camera.example")),
        AddPlaylistDialog(inline_resolver(), existing=playlist),
    ):
        qtbot.addWidget(dialog)
        dialog.show()
        QApplication.processEvents()
        scroll = dialog.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == scroll.verticalScrollBar().maximum() == 0
        content = scroll.widget()
        assert content is not None
        assert content.sizeHint().width() <= scroll.viewport().width()
        assert content.sizeHint().height() <= scroll.viewport().height()
        assert clipped_text_on_every_page(dialog) == []
        for field in dialog.findChildren(QLineEdit):
            if field.isVisibleTo(dialog):
                assert field.width() >= field.sizeHint().width()
        dialog.close()
