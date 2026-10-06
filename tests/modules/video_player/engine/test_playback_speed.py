from __future__ import annotations

from ax_devil.modules.video_player.engine.playback_speed import (
    DEFAULT_PLAYBACK_SPEED,
    MAX_PLAYBACK_SPEED,
    MIN_PLAYBACK_SPEED,
    clamp_playback_speed,
    format_playback_speed,
    scale_frame_period,
    step_playback_speed,
)


def test_playback_speed_numeric_helpers() -> None:
    assert clamp_playback_speed(-1.0) == MIN_PLAYBACK_SPEED
    assert clamp_playback_speed(100.0) == MAX_PLAYBACK_SPEED
    assert clamp_playback_speed(1.7) == 1.7
    assert step_playback_speed(1.0, 1) == 1.1
    assert step_playback_speed(1.0, -1) == 0.9
    assert step_playback_speed(0.2, -1, floor=0.1) == 0.1
    assert step_playback_speed(0.1, -1, floor=0.1) == 0.1
    assert step_playback_speed(0.01, 1, floor=0.1) == 0.1
    assert scale_frame_period(base_period_s=0.1, speed=2.0) == 0.05
    assert scale_frame_period(base_period_s=0.1, speed=0.5) == 0.2


def test_format_playback_speed_for_ui() -> None:
    assert format_playback_speed(DEFAULT_PLAYBACK_SPEED) == "1.0x"
    assert format_playback_speed(0.01) == "0.01x"
    assert format_playback_speed(1.7) == "1.7x"
    assert format_playback_speed(MAX_PLAYBACK_SPEED) == "10.0x"
