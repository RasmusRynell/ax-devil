"""Text-size changes reach open windows without a restart."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QWidget
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
    """The frame number and speed keep their text inside their fields when the text size changes."""
    apply_text_size(21)
    controls = SeekableVideoControlPanel()
    qtbot.addWidget(controls)
    controls.show_video(total_frames=100000, frame_rate=30.0)
    controls.set_current_frame(12345)
    controls.set_playback_speed(0.25)
    controls.resize(900, controls.height())
    controls.show()

    for size in (13, 21, 13):
        apply_text_size(size)
        QApplication.processEvents()
        field = controls.frame_spinbox.lineEdit()
        assert field is not None
        assert field.fontMetrics().horizontalAdvance(field.text()) <= field.contentsRect().width()
        assert field.fontMetrics().height() <= field.contentsRect().height()
        assert controls.frame_spinbox.rect().contains(field.geometry())
        assert field.font().pixelSize() == controls.frame_spinbox.font().pixelSize()
        speed = controls.speed_button
        assert speed.fontMetrics().horizontalAdvance(speed.text()) <= speed.contentsRect().width()


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("width", [180, 280, 420, 900])
def test_playback_controls_fit_narrow_panes_at_large_text(qtbot: QtBot, width: int) -> None:
    """In a narrow pane the row drops its least important controls instead of cutting any off or widening the pane."""
    apply_text_size(21)
    pane = QWidget()
    qtbot.addWidget(pane)
    layout = QGridLayout(pane)
    layout.setContentsMargins(0, 0, 0, 0)
    controls = SeekableVideoControlPanel()
    controls.show_video(total_frames=100000, frame_rate=30.0)
    layout.addWidget(controls, 0, 0, Qt.AlignmentFlag.AlignBottom)
    pane.resize(width, 200)
    pane.show()
    QApplication.processEvents()

    assert controls.width() == width
    row = controls.play_pause_btn.parentWidget()
    assert row is not None
    shown = [child for child in row.findChildren(QWidget) if child.isVisibleTo(controls) and child.parent() is row]
    assert controls.play_pause_btn in shown
    for child in shown:
        assert row.rect().contains(child.geometry()), child
        assert child.width() >= child.minimumSizeHint().width(), child
    if width >= 900:
        assert controls.timecode_text() and controls.speed_button.isVisibleTo(controls)


def test_header_details_hide_in_narrow_panes_without_widening_them(qtbot: QtBot) -> None:
    """The muted details next to a pane title never set the pane's minimum width; they hide when they do not fit."""
    pane = _Pane()
    qtbot.addWidget(pane)
    narrowest = pane.minimumSizeHint().width()
    pane.set_header_details("1920×1080 · 29.97 fps · 1:02:03")
    assert pane.minimumSizeHint().width() == narrowest

    pane.resize(900, 300)
    pane.show()
    QApplication.processEvents()
    assert pane.header_details() == "1920×1080 · 29.97 fps · 1:02:03"

    pane.resize(narrowest + 40, 300)
    QApplication.processEvents()
    assert pane.header_details() == ""
