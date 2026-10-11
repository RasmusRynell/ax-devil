"""Native UVG-VCM decoding through the registered file provider and playlist seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.data_sources.timing_reports import FrameTimeline
from ax_devil.modules.filtering import FilterState
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.scene.model import EntityId, Scene
from ax_devil.modules.workspace.core import FileOverlaySourceSpec
from ax_devil.plugins.decoders.uvg_vcm.plugin import UVG_VCM
from ax_devil.plugins.decoders.uvg_vcm.provider import UVGVCMSceneDataProvider
from ax_devil.plugins.playlist_resolvers.folder_pair.resolver import build_playlist_contents, discover_folder_pairs


def _detection(track_id: int = 7, class_id: int = 1, **fields: object) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "class_id": class_id,
        "x_min": 0.1,
        "y_min": 0.2,
        "x_max": 0.4,
        "y_max": 0.6,
        **fields,
    }


def _write(path: Path, frames: dict[str, object], version: str = "1.0") -> Path:
    path.write_text(json.dumps({"version": version, **frames}), encoding="utf-8")
    return path


def _load(provider: UVGVCMSceneDataProvider, frame: int) -> Scene:
    scene = provider.load_by_frame_id(FrameIdentifier(sequence_id=frame, timestamp_monotime_us=frame * 40_000))
    assert scene is not None
    return scene


def test_frame_alignment_tracking_and_empty_frames_survive_cache_and_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unordered JSON keys, empty frames, and cached history must retain the source's exact frame indices."""
    path = _write(
        tmp_path / "annotations.json",
        {"3": [_detection(x_min=-0.000001)], "1": [_detection(iscrowd=0, mask_color=[155, 255, 255])], "2": []},
    )
    timeline = FrameTimeline.from_timestamps((0, 40_000, 80_000))
    for warm in (False, True):
        with monkeypatch.context() as context:
            if warm:
                context.setattr(
                    "ax_devil.plugins.decoders.uvg_vcm.provider.iter_uvg_vcm_frames",
                    lambda _path: pytest.fail("The source was parsed despite a valid warm cache"),
                )
            provider = UVGVCMSceneDataProvider(path)
            try:
                assert provider.get_available_sequences() == {0, 1, 2}
                first = _load(provider, 0).entities[EntityId("7")].observations[0]
                assert first.frame_number == 0
                assert first.timestamp is None
                assert first.confidence is None
                assert first.geometry.as_xywh() == pytest.approx((0.1, 0.2, 0.3, 0.4))
                classification = first.primary_classification
                assert classification is not None
                assert classification.type == "person"
                assert classification.attribute_value("mask_color") == [155, 255, 255]
                assert classification.attribute_value("iscrowd") == 0
                assert not _load(provider, 1).entities
                assert _load(provider, 2).entities[EntityId("7")].observations[0].geometry.as_xywh()[0] == -0.000001
                assert provider.get_filter_config().supports_history_filtering
                history = provider.scene_history(timeline, allow_previous=False, max_sample_age_us=None)
                assert [(item.entity_id, item.types, item.spans) for item in history.objects] == [
                    ("7", ("person",), ((0, 0), (2, 2)))
                ]
            finally:
                provider.close()
    _write(path, {"1": [_detection(class_id=33)]})
    provider = UVGVCMSceneDataProvider(path)
    try:
        assert provider.get_available_sequences() == {0}
        classification = _load(provider, 0).entities[EntityId("7")].observations[0].primary_classification
        assert classification is not None and classification.type == "sports ball"
        assert provider.get_filter_config().option_ids() == ("show_sports_ball",)
    finally:
        provider.close()


def test_polygons_and_category_ids_decode_through_a_folder_pair_playlist(tmp_path: Path) -> None:
    """The native handler must be discoverable and keep polygons, IDs, and the dataset's contiguous class mapping."""
    videos, overlays = tmp_path / "videos", tmp_path / "overlays"
    videos.mkdir()
    overlays.mkdir()
    (videos / "HighwayDrive.mp4").touch()
    annotation_path = _write(
        overlays / "HighwayDrive.json",
        {
            "1": [
                _detection(3, 3, class_name="car", polygon=[0.1, 0.2, 0.4, 0.2, 0.3, 0.6]),
                _detection(8, 8),
                _detection(10, 10),
                _detection(33, 33),
                _detection(80, 80),
            ]
        },
    )
    playlist = build_playlist_contents(discover_folder_pairs(videos, overlays), UVG_VCM)[0]
    overlay = playlist.entries[0].lanes[0].overlay
    assert overlay is not None
    assert overlay.source_spec == FileOverlaySourceSpec(path=annotation_path, handler_type=UVG_VCM)
    factory = get_file_decoder_factory(overlay.source_spec.handler_type)
    provider = factory(annotation_path)
    try:
        scene = provider.load_by_frame_id(FrameIdentifier(sequence_id=0, timestamp_monotime_us=0))
        assert scene is not None
        observation = scene.entities[EntityId("3")].observations[0]
        points = observation.geometry.polygon_points()
        assert points is not None
        assert [coordinate for point in points for coordinate in (point.x, point.y)] == [0.1, 0.2, 0.4, 0.2, 0.3, 0.6]
        assert scene.entities[EntityId("8")].observations[0].geometry.as_xywh() == pytest.approx((0.1, 0.2, 0.3, 0.4))
        labels = {entity.observations[0].classification[0].type for entity in scene.entities.values()}
        assert labels == {"car", "truck", "traffic light", "sports ball", "toothbrush"}
        config = provider.get_filter_config()
        assert config is not None
        state = FilterState(config)
        cars = next(option for option in config.options if option.id == "show_car")
        assert cars.predicate(scene.entities[EntityId("3")], state)
        assert not cars.predicate(scene.entities[EntityId("8")], state)
        assert cars.classification_filter is not None and cars.classification_filter.matches_types(("car",))
    finally:
        provider.close()


def test_duplicate_ids_keep_every_box_without_inventing_persistent_tracks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ID collisions must be visible and diagnosable on both cold and cached loads."""
    path = _write(
        tmp_path / "annotations.json",
        {"1": [_detection(), _detection(x_min=0.2)], "2": [_detection()]},
    )
    for _ in range(2):
        caplog.clear()
        provider = UVGVCMSceneDataProvider(path)
        try:
            scene = _load(provider, 0)
            assert len(scene.entities) == 2
            assert EntityId("7") not in scene.entities
            assert {entity.observations[0].geometry.as_xywh()[0] for entity in scene.entities.values()} == {0.1, 0.2}
            assert all(
                entity.observations[0].classification[0].attribute_value("track_id") == 7
                for entity in scene.entities.values()
            )
            assert set(_load(provider, 1).entities) == {"7"}
            assert "duplicate UVG-VCM tracking IDs in source frames [1]" in caplog.text
        finally:
            provider.close()


@pytest.mark.parametrize(
    ("frames", "version", "message"),
    [
        ({"1": []}, "0.9", "version '1.0'"),
        ({"2": []}, "1.0", "consecutive integers"),
        ({"1": {}}, "1.0", "expected a JSON array"),
        ({"1": [_detection()], "2": [_detection(class_id=81)]}, "1.0", "Frame 2 detection 0.class_id"),
        ({"1": [_detection(class_id=True)]}, "1.0", "class_id"),
        ({"1": [_detection(x_max=0.0)]}, "1.0", "maxima"),
        ({"1": [_detection(x_min=float("nan"))]}, "1.0", "finite number"),
        ({"1": [_detection(polygon=[0.1, 0.2, 0.3])]}, "1.0", "coordinate pairs"),
        ({"1": [_detection(class_name="car")]}, "1.0", "does not match"),
    ],
)
def test_invalid_annotations_fail_the_load_without_silently_dropping_frames(
    tmp_path: Path, frames: dict[str, object], version: str, message: str
) -> None:
    """Unsupported versions and malformed geometry must not produce a misleading partial overlay source."""
    path = _write(tmp_path / "annotations.json", frames, version)
    with pytest.raises(ValueError, match=message):
        UVGVCMSceneDataProvider(path)


def test_all_empty_frames_can_be_loaded_and_filtered(tmp_path: Path) -> None:
    """An explicitly empty annotation frame still clears overlays and counts toward sequence length."""
    provider = UVGVCMSceneDataProvider(_write(tmp_path / "annotations.json", {"1": [], "2": []}))
    try:
        assert provider.get_total_frames() == 2
        assert not _load(provider, 0).entities
        assert not _load(provider, 1).entities
        assert provider.get_filter_config().supports_history_filtering
    finally:
        provider.close()
