from __future__ import annotations

from pytestqt.qtbot import QtBot

from ax_devil.modules.video_player.ui.controls import SeekableVideoControlPanel


class TestSeekableVideoControlPanelPlaybackSpeed:
    def test_speed_spinbox_and_setter_update_clamped_value(self, qtbot: QtBot) -> None:
        panel = SeekableVideoControlPanel()
        qtbot.addWidget(panel)

        seen: list[float] = []
        panel.playbackSpeedChanged.connect(seen.append)

        panel.speed_spinbox.setValue(0.01)

        assert panel.playback_speed() == 0.01
        assert seen[-1] == 0.01

        panel.set_playback_speed(100.0)
        assert panel.playback_speed() == 10.0

        panel.set_playback_speed(-5.0)
        assert panel.playback_speed() == 0.01

    def test_speed_steps_clamp_to_hotkey_floor_and_max(self, qtbot: QtBot) -> None:
        panel = SeekableVideoControlPanel()
        qtbot.addWidget(panel)

        panel.set_playback_speed(0.01)
        panel.step_playback_speed(1)
        assert panel.playback_speed() == 0.1

        panel.step_playback_speed(-1)
        assert panel.playback_speed() == 0.1

        for _ in range(200):
            panel.step_playback_speed(1)

        assert panel.playback_speed() == 10.0

        panel.set_playback_speed(0.2)
        for _ in range(10):
            panel.step_playback_speed(-1)

        assert panel.playback_speed() == 0.1
