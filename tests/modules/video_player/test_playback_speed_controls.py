from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel, format_timecode


def _speed_choice(panel: SeekableVideoControlPanel, text: str) -> None:
    menu = panel.speed_button.menu()
    assert menu is not None
    next(action for action in menu.actions() if action.text() == text).trigger()


class TestSeekableVideoControlPanelPlaybackSpeed:
    def test_speed_menu_and_setter_update_clamped_value(self, qtbot: QtBot) -> None:
        panel = SeekableVideoControlPanel()
        qtbot.addWidget(panel)

        seen: list[float] = []
        panel.playbackSpeedChanged.connect(seen.append)

        _speed_choice(panel, "0.25×")

        assert panel.playback_speed() == 0.25
        assert seen[-1] == 0.25
        assert panel.speed_button.text() == "0.25×"

        panel.set_playback_speed(100.0)
        assert panel.playback_speed() == 10.0
        assert panel.speed_button.text() == "10×"

        panel.set_playback_speed(-5.0)
        assert panel.playback_speed() == 0.01


def test_time_follows_the_frame_at_the_video_frame_rate(qtbot: QtBot) -> None:
    panel = SeekableVideoControlPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_video(total_frames=9000, frame_rate=25.0)

    panel.set_current_frame(1530)

    assert panel.timecode_text() == "1:01.20"

    # Variable frame rates show each frame's own time.
    panel.show_video(total_frames=3, frame_rate=25.0, frame_times_us=(0, 40_000, 1_500_000))
    panel.set_current_frame(2)
    assert panel.timecode_text() == "0:01.50"
    assert format_timecode(3725.5) == "1:02:05.50"
    assert format_timecode(65.4, hundredths=False) == "1:05"


def test_timeline_shows_the_cached_frames_reported_by_the_video(qtbot: QtBot) -> None:
    panel = SeekableVideoControlPanel()
    qtbot.addWidget(panel)
    panel.show()
    cached: list[tuple[tuple[int, int], ...]] = [((0, 9),)]
    panel.show_video(total_frames=100, frame_rate=25.0, cached_ranges=lambda: cached[-1])
    assert panel.timeline_slider.cached_ranges() == ((0, 9),)

    cached.append(((0, 9), (40, 59)))
    qtbot.waitUntil(lambda: panel.timeline_slider.cached_ranges() == ((0, 9), (40, 59)), timeout=2000)

    panel.show_video(total_frames=100, frame_rate=25.0)
    assert panel.timeline_slider.cached_ranges() == ()
    QApplication.processEvents()


def test_controls_ignore_fade_requests_after_cleanup(qtbot: QtBot) -> None:
    """A hide requested after teardown, as a lane switch can send, must not touch the deleted opacity effect."""
    panel = SeekableVideoControlPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.cleanup()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)  # Deletes the opacity effect.

    panel.start_auto_hide_timer()
    panel.fade_in()
    qtbot.wait(panel.auto_hide_delay + 50)
