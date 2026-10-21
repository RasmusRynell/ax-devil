"""Tests for offline session, timeline, and lane behavior."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from pytestqt.qtbot import QtBot

from ax_devil.core.data_types import FrameData, FrameIdentifier
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_viewer import offline_entry_media
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings
from ax_devil.modules.workspace.core import (
    FileOverlaySourceSpec,
    LiveMQTTOverlaySourceSpec,
    OverlayContent,
)
from tests.helpers.offline_session import (
    AsyncFrameSource,
    FakeDisplay,
    FakeOverlaySource,
    PlaybackSource,
    make_frame,
    make_lane,
    make_overlay,
    make_playback,
    make_primary_playback,
)


def test_offline_session_bounds_commands_clamps_speed_and_tracks_playback_state(qtbot: QtBot) -> None:
    """Play at the last frame restarts; seeks and steps stay inside the video; speed is clamped."""
    session, _display, source = make_primary_playback(qtbot, current_frame=9, total_frames=10)

    assert session.total_frames == 10
    assert session.current_frame == 9
    assert not session.is_playing

    session.play()

    assert source.play_calls == 1
    assert source.current_frame == 0
    assert session.current_frame == 0
    assert session.is_playing

    session.pause()

    assert source.pause_calls == 1
    assert not session.is_playing

    session.seek(500)

    assert source.jump_calls[-1] == 9
    assert session.current_frame == 9

    session.step(-20)

    assert source.jump_calls[-1] == 0
    assert session.current_frame == 0

    session.set_speed(99.0)

    assert source.speed_updates[-1] == 10.0


def test_lane_shows_each_frame_with_the_overlay_for_that_frame() -> None:
    overlay_source = FakeOverlaySource(make_overlay(7))
    display = FakeDisplay()
    lane = make_lane(display, overlay_source=overlay_source)

    lane.present_frame(make_frame(7))

    assert overlay_source.requested_frame_ids == [FrameIdentifier(sequence_id=7, timestamp_us=7000)]
    assert display.frame_ids() == [7]
    assert display.frames[0].overlays is not None
    assert display.frames[0].overlays.overlay_id == 7


def test_secondary_video_shows_only_the_current_primary_frame(qtbot: QtBot) -> None:
    """Lanes on a second video follow the primary frame; superseded or stale completions never reach the screen."""
    primary = PlaybackSource()
    secondary = AsyncFrameSource()
    session = make_playback(qtbot, [primary, secondary])
    frames: dict[int, list[int]] = {0: [], 1: []}
    session.frameReady.connect(lambda index, frame: frames[index].append(frame.frame_id.sequence_id))

    # Frame 9 arrives while frame 7 is still decoding on the second video: 7 is skipped once it completes.
    primary.deliver(7)
    primary.deliver(9)
    assert secondary.requests == [7]
    secondary.complete_from_worker(7)
    assert frames[1] == []
    qtbot.waitUntil(lambda: secondary.requests == [7, 9])
    secondary.complete_from_worker(9)
    assert frames[1] == []
    qtbot.waitUntil(lambda: bool(frames[1]))
    assert frames[0] == [7, 9]
    assert frames[1] == [9]

    # Seeking to the frame already being decoded still rejects the completion requested before the seek.
    primary.deliver(10)
    before_seek = secondary.callbacks[10]
    session.seek(10)
    primary.deliver(10)
    before_seek(make_frame(10))
    assert frames[1] == [9]
    secondary.complete(10)
    assert frames[1] == [9, 10]

    # Pausing drops the pending request; a missing secondary frame keeps the previous one on screen.
    primary.deliver(11)
    session.pause()
    secondary.complete(11)
    session.step(2)
    primary.deliver(13)
    secondary.callbacks[13](None)
    assert frames[1] == [9, 10]
    primary.deliver(14)
    secondary.complete(14)
    assert frames[1] == [9, 10, 14]

    # A direct seek beyond a shorter video must show its final frame, even if it was never shown before.
    secondary.total_frames = 12
    session.seek(100)
    primary.deliver(100)
    assert secondary.requests[-1] == 11
    secondary.complete(11)
    assert frames[1][-1] == 11
    secondary.total_frames = 1000

    # A decoder finishing after the session is gone must not touch it.
    primary.deliver(15)
    late_completion = secondary.callbacks[15]
    session.close()
    QCoreApplication.sendPostedEvents(session, QEvent.Type.DeferredDelete)
    failures: list[Exception] = []

    def complete_after_destruction() -> None:
        try:
            late_completion(make_frame(15))
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=complete_after_destruction)
    worker.start()
    worker.join()
    QCoreApplication.processEvents()
    assert failures == []
    assert frames[1] == [9, 10, 14, 11]


def test_presented_frame_stays_the_frame_on_screen_while_a_seek_is_pending(qtbot: QtBot) -> None:
    """A bookmark taken after a seek request but before its frame arrives marks the frame the user sees."""
    session, _display, source = make_primary_playback(qtbot)
    assert session.presented_frame is None
    source.deliver(7)

    session.seek(40)

    assert session.presented_frame == (7, pytest.approx(0.007))

    source.deliver(40)

    assert session.presented_frame == (40, pytest.approx(0.04))


def test_seek_drops_frames_decoded_before_it(qtbot: QtBot) -> None:
    """Frames queued from the old position are skipped until the seek target arrives; frames without a position
    generation are always shown."""
    session, display, source = make_primary_playback(qtbot)
    session.play()
    old_generation = source.position_generation
    source.deliver(500)

    session.seek(498)
    source.deliver(501, position_generation=old_generation)
    source.deliver(498)
    source.deliver(501, position_generation=old_generation)
    source.deliver(499)

    assert source.jump_calls[-1] == 498
    assert display == [500, 498, 499]

    session.seek(4)
    source.frameReady.emit(make_frame(5))
    QCoreApplication.processEvents()

    assert display == [500, 498, 499, 5]


def test_frame_relay_presents_only_newest_of_queued_deliveries(qtbot: QtBot) -> None:
    """A GUI thread that falls behind presents the newest queued frame and skips superseded ones."""
    source = PlaybackSource()
    playback = make_playback(qtbot, [source])
    presented: list[int] = []
    playback.frameReady.connect(lambda _index, frame: presented.append(frame.frame_id.sequence_id))

    def deliver_from_worker(*frame_numbers: int) -> None:
        def emit_frames() -> None:
            for frame_number in frame_numbers:
                source.frameReady.emit(make_frame(frame_number))

        worker = threading.Thread(target=emit_frames)
        worker.start()
        worker.join()

    deliver_from_worker(1, 2, 3)
    assert presented == []
    qtbot.waitUntil(lambda: bool(presented))
    QCoreApplication.processEvents()
    assert presented == [3]

    deliver_from_worker(4)
    qtbot.waitUntil(lambda: len(presented) == 2)
    assert presented == [3, 4]


def test_step_during_display_is_relative_to_the_frame_being_shown(qtbot: QtBot) -> None:
    """A command triggered by displaying frame 800 should seek relative to frame 800, not 799."""
    source = PlaybackSource(total_frames=1000)
    secondary = AsyncFrameSource()
    playback = make_playback(qtbot, [source, secondary])
    observed: list[int] = []

    def step_on_frame(index: int, frame: FrameData) -> None:
        if index == 0 and frame.frame_id.sequence_id == 800:
            observed.append(playback.current_frame)
            playback.step(-30)

    playback.frameReady.connect(step_on_frame)
    source.deliver(799)
    source.deliver(800)
    assert observed == [800]
    assert source.jump_calls[-1] == 770
    assert secondary.requests[-1] == 770


class _RecordingProvider:
    """Overlay data provider test double recording how the decoder was configured."""

    def __init__(self, file_path: Path, **kwargs: object) -> None:
        self.file_path = file_path
        self.kwargs = kwargs
        self.policy = TimestampFallbackPolicy()

    def set_timestamp_fallback_policy(self, policy: TimestampFallbackPolicy) -> None:
        self.policy = policy

    def get_timestamp_fallback_policy(self) -> TimestampFallbackPolicy:
        return self.policy

    def get_total_frames(self) -> int:
        return 0


def test_overlay_source_is_opened_with_the_decoder_options_and_fallback_policy_of_its_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_file_decoder_factory(handler_type: str) -> type[_RecordingProvider]:
        assert handler_type == "MOT_FILE"
        return _RecordingProvider

    monkeypatch.setattr(offline_entry_media, "get_file_decoder_factory", get_file_decoder_factory)
    policy = TimestampFallbackPolicy(mode=TimestampFallbackMode.EXACT_ONLY, tolerance_us=12_000)
    overlay = OverlayContent(
        display_name="overlay.txt",
        source_spec=FileOverlaySourceSpec(
            path=Path("/tmp/overlay.txt"),
            handler_type="MOT_FILE",
            timestamp_fallback_policy=policy,
            decoder_kwargs={"width": 1920, "height": 1080},
        ),
    )

    opened = offline_entry_media.create_overlay_source(
        overlay, frame_timeline=FrameTimeline.lazy(count=0, timestamp_loader=tuple)
    )

    provider = opened.source.data_provider
    assert isinstance(provider, _RecordingProvider)
    assert provider.file_path == Path("/tmp/overlay.txt")
    assert provider.kwargs == {"width": 1920, "height": 1080}
    assert opened.source.handler_type == "MOT_FILE"
    assert opened.source.get_timestamp_fallback_policy() == policy
    assert opened.policy.settings.enabled


def test_entry_media_rejects_non_file_overlay_source_spec() -> None:
    overlay = OverlayContent(
        display_name="MQTT",
        source_spec=LiveMQTTOverlaySourceSpec(handler_type="LIVE", broker_host="broker.local"),
    )

    with pytest.raises(RuntimeError, match="requires a file overlay source spec"):
        offline_entry_media.create_overlay_source(
            overlay, frame_timeline=FrameTimeline.lazy(count=0, timestamp_loader=tuple)
        )


def test_lane_retains_a_recent_overlay_at_its_original_sample_time() -> None:
    """Direct display and backward revisits use lookup selection and retained styling."""
    overlay = replace(make_overlay(7), metadata={"timestamp_match_type": "retained"})
    display = FakeDisplay()
    lane = make_lane(
        display,
        overlay_source=FakeOverlaySource(overlay),
        overlay_policy=OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=2, opacity=0.4)),
    )
    for frame in [8, 10, 8, 6]:
        lane.present_frame(make_frame(frame))
    for index in [0, 2]:
        shown = display.frames[index].overlays
        assert shown is not None
        assert shown.timestamp_us == 7000
        assert shown.metadata is not None
        assert shown.metadata["overlay_reused"] is True
        assert shown.metadata["overlay_opacity"] == 0.4
    assert display.frames[1].overlays is None
    assert display.frames[3].overlays is None
