"""Tests for the MOT Challenge scene data provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.scene.model import EntityId
from ax_devil.plugins.decoders.mot.plugin import MOT_FILE
from ax_devil.plugins.decoders.mot.provider import MOTChallengeSceneDataProvider

pytestmark = pytest.mark.usefixtures("isolated_cache")


SAMPLE_ROWS = [
    "1,1,0,0,64,48,0.9,1,0.8",
    "1,2,32,24,64,48,0.7,7,0.25",
    "2,3,10,12,40,50,0.5,3,0.4",
]


def _write_csv(path: Path) -> None:
    path.write_text("\n".join(SAMPLE_ROWS) + "\n", encoding="utf-8")


def test_registered_mot_handler_reads_frames_from_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "mot.csv"
    _write_csv(csv_path)

    provider = get_file_decoder_factory(MOT_FILE)(str(csv_path), width=640, height=480)
    try:
        assert provider.get_total_frames() == 2
        assert provider.get_available_frames() == {0, 1}
        frame0 = provider.load_by_frame_id(FrameIdentifier(sequence_id=0, timestamp_monotime_us=0.0))
        assert frame0 is not None
        assert set(frame0.entities) == {"1", "2"}
        frame1 = provider.load_by_frame_id(FrameIdentifier(sequence_id=1, timestamp_monotime_us=1.0))
        assert frame1 is not None
        assert set(frame1.entities) == {"3"}
    finally:
        provider.close()


def test_mot_provider_reopens_from_warm_cache_without_parsing(tmp_path: Path) -> None:
    csv_path = tmp_path / "mot.csv"
    _write_csv(csv_path)
    MOTChallengeSceneDataProvider(csv_path, width=640, height=480).close()

    with patch(
        "ax_devil.plugins.decoders.mot.provider.prepare_mot_frame_payloads",
        side_effect=AssertionError("Warm cache must not parse the source again"),
    ):
        warm_provider = MOTChallengeSceneDataProvider(csv_path, width=640, height=480)
        try:
            assert warm_provider.get_total_frames() == 2
            scene = warm_provider.load_by_frame_id(FrameIdentifier(sequence_id=1, timestamp_monotime_us=1.0))
            assert scene is not None
            assert set(scene.entities) == {"3"}
        finally:
            warm_provider.close()


def test_mot_provider_rebuilds_cached_scenes_when_dimensions_change(tmp_path: Path) -> None:
    """A cache built for one frame size must not place boxes for another."""
    csv_path = tmp_path / "mot.csv"
    _write_csv(csv_path)
    frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=0.0)

    for width in (640, 1280):
        provider = MOTChallengeSceneDataProvider(csv_path, width=width, height=480)
        try:
            scene = provider.load_by_frame_id(frame_id)
            assert scene is not None
            left = scene.entities[EntityId("2")].observations[0].geometry.as_xywh()[0]
            assert left == pytest.approx(32 / width)
        finally:
            provider.close()


def test_sticky_mot_selection_uses_video_time_for_frame_keyed_samples(tmp_path: Path) -> None:
    """Frame numbers must never be interpreted as microseconds during retention."""
    from ax_devil.modules.data_sources.file_overlay_source import FileOverlaySource
    from ax_devil.modules.data_sources.timing_reports import FrameTimeline
    from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy, OverlayPersistenceSettings

    path = tmp_path / "sparse.csv"
    path.write_text("1,1,0,0,64,48,0.9,1,0.8\n4,4,0,0,64,48,0.9,1,0.8\n6,6,0,0,64,48,0.9,1,0.8\n")
    times = (0, 1_000_000, 1_900_000, 3_000_000, 4_000_000, 6_000_000, 9_000_000)
    source = FileOverlaySource(
        path, MOTChallengeSceneDataProvider, "MOT_FILE", frame_timeline=FrameTimeline.from_timestamps(times)
    )
    policy = OverlayPersistencePolicy(OverlayPersistenceSettings(enabled=True, timeout_ms=2050, opacity=0.4))
    try:
        for frame, expected in [(1, 0), (3, 3), (2, 0), (4, 3), (5, 5), (1, 0)]:
            selected = policy.select_from_source(source, FrameIdentifier(frame, times[frame]))
            assert selected.overlay is not None
            assert selected.overlay.frame_id == FrameIdentifier(expected, times[expected])
            assert selected.reused is (frame != expected)
            assert selected.overlay.metadata is not None
            assert selected.overlay.metadata["overlay_alignment_basis"] == "sequence"
        assert policy.select_from_source(source, FrameIdentifier(6, times[6])).overlay is None
        policy.update_settings(OverlayPersistenceSettings(enabled=False))
        assert policy.select_from_source(source, FrameIdentifier(4, times[4])).overlay is None
        assert policy.select_from_source(source, FrameIdentifier(5, times[5])).overlay is not None
    finally:
        source.close()
