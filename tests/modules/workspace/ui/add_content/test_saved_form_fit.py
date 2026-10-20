"""Populated editors fit the screen with usable fields, including at large text sizes."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QScrollArea
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
def test_saved_forms_fit_available_screen_with_usable_fields(
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
        assert scroll.horizontalScrollBar().maximum() == 0
        content = scroll.widget()
        assert content is not None
        screen = dialog.screen()
        assert screen is not None
        available = screen.availableGeometry()
        decorations = dialog.frameGeometry().height() - dialog.height()
        available_height = available.height() - decorations - 2 * dialog.fontMetrics().height()
        # Native CI desktops can be shorter than the form at large text sizes.
        if dialog.opening_size_hint().height() <= available_height:
            assert scroll.verticalScrollBar().maximum() == 0
            assert content.sizeHint().width() <= scroll.viewport().width()
            assert content.sizeHint().height() <= scroll.viewport().height()
        else:
            assert dialog.frameGeometry().height() <= available.height()
            assert scroll.verticalScrollBar().maximum() > 0
        assert clipped_text_on_every_page(dialog) == []
        for field in dialog.findChildren(QLineEdit):
            if field.isVisibleTo(dialog):
                assert field.width() >= field.sizeHint().width()
                scroll.ensureWidgetVisible(field)
                QApplication.processEvents()
                assert scroll.viewport().rect().contains(field.mapTo(scroll.viewport(), field.rect().center()))
        for button in dialog.findChildren(QPushButton):
            if button.isVisibleTo(dialog) and not scroll.isAncestorOf(button):
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        dialog.close()
