"""Text-size changes reach open windows without a restart."""

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.chrome.title_bar import TitleBar
from ax_devil.modules.chrome.tokens import TextRole
from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class _Pane(WorkspaceWidget):
    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        return "clip.mp4"

    def cleanup(self) -> None:
        pass


@pytest.mark.usefixtures("restore_app_appearance")
def test_open_chrome_follows_text_size_changes(qtbot: QtBot) -> None:
    """Fixed-height bars resize with their text when the text size changes, in both directions."""
    window = ChromeWindow(use_custom_frame=True)
    qtbot.addWidget(window)
    pane = _Pane()
    qtbot.addWidget(pane)
    title_bar = window.findChild(TitleBar)
    assert title_bar is not None
    labels = (title_bar._title_label, pane._title_label)

    bar_heights = []
    for size in (13, 21, 13):
        apply_text_size(size)
        QApplication.processEvents()  # Restyling runs once per change, on the next event-loop turn.
        bars = [label.parentWidget() for label in labels]
        assert all(bar is not None and label.fontMetrics().height() <= bar.height() for bar, label in zip(bars, labels))
        bar_heights.append([bar.height() for bar in bars if bar is not None])
    assert all(small < large for small, large in zip(bar_heights[0], bar_heights[1]))
    assert bar_heights[0] == bar_heights[2]


@pytest.mark.usefixtures("restore_app_appearance")
def test_open_role_text_follows_text_size_and_theme_changes(qtbot: QtBot, qapp: QApplication) -> None:
    """Restyling, for a new text size or a new theme, must not put back a role label's old font."""
    apply_text_size(13)
    caption = QLabel("Theme")
    qtbot.addWidget(caption)
    TextRole.CAPTION.apply(caption)
    caption.show()

    for size in (21, 13):
        apply_text_size(size)
        assert caption.font().pixelSize() == TextRole.CAPTION.px
    apply_text_size(21)
    # A theme switch installs a new palette and applies the application stylesheet again.
    palette = qapp.palette()
    palette.setColor(QPalette.ColorRole.Window, palette.color(QPalette.ColorRole.Base).darker())
    qapp.setPalette(palette)
    qapp.setStyleSheet(qapp.styleSheet())
    QApplication.processEvents()
    assert caption.font().pixelSize() == TextRole.CAPTION.px


@pytest.mark.usefixtures("restore_app_appearance")
def test_playback_readouts_are_not_cut_off_after_text_size_changes(qtbot: QtBot) -> None:
    """The frame number and speed boxes keep their text inside their fields when the text size changes."""
    apply_text_size(21)
    controls = SeekableVideoControlPanel()
    qtbot.addWidget(controls)
    controls.frame_spinbox.setMaximum(99999)
    controls.frame_spinbox.setValue(12345)
    controls.show()

    for size in (13, 21, 13):
        apply_text_size(size)
        QApplication.processEvents()
        for box in (controls.frame_spinbox, controls.speed_spinbox):
            field = box.lineEdit()
            assert field is not None
            assert field.fontMetrics().horizontalAdvance(field.text()) <= field.contentsRect().width()
            assert field.fontMetrics().height() <= field.contentsRect().height()
            assert box.rect().contains(field.geometry())
            assert field.font().pixelSize() == box.font().pixelSize()
