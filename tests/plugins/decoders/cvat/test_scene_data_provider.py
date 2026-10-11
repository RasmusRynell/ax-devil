"""Tests for the CVAT scene data provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.cache import IndexedFrameCache
from ax_devil.modules.data_sources.file_data_provider.base import FrameIdentifierDataProvider
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.plugins.decoders.cvat.decoder import decode_cvat_frame, parse_cvat_document
from ax_devil.plugins.decoders.cvat.plugin import CVAT
from ax_devil.plugins.decoders.cvat.provider import CVATSceneDataProvider

from .sample_data import write_sample_cvat_xml

pytestmark = pytest.mark.usefixtures("isolated_cache")

PARSE = "ax_devil.plugins.decoders.cvat.provider.parse_cvat_document"


def _write_sample_xml(tmp_path: Path) -> Path:
    xml_path = tmp_path / "sample_cvat.xml"
    write_sample_cvat_xml(xml_path)
    return xml_path


def _assert_serves_sample(provider: FrameIdentifierDataProvider) -> None:
    """The sample task's frames and label filters are available."""
    assert provider.get_total_frames() == 2
    assert provider.get_available_frames() == {0, 1}
    filter_config = provider.get_filter_config()
    assert filter_config is not None
    assert {option.id for option in filter_config.options} == {"show_car", "show_person"}
    scene = provider.load_by_frame_id(FrameIdentifier(sequence_id=1, timestamp_monotime_us=1.0))
    assert scene is not None
    assert set(scene.entities) == {"cvat_track_10", "cvat_track_11"}


def test_registered_cvat_handler_serves_frames_and_label_filters(tmp_path: Path) -> None:
    provider = get_file_decoder_factory(CVAT)(str(_write_sample_xml(tmp_path)))
    try:
        _assert_serves_sample(provider)
    finally:
        provider.close()


def test_cvat_provider_reopens_from_warm_cache_without_parsing(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)
    CVATSceneDataProvider(xml_path).close()

    with patch(PARSE, side_effect=AssertionError("Warm cache must not parse the source again")):
        provider = CVATSceneDataProvider(xml_path)
        try:
            _assert_serves_sample(provider)
        finally:
            provider.close()


def test_cvat_provider_rebuilds_an_outdated_cache(tmp_path: Path) -> None:
    """A cache written without the current metadata is rebuilt instead of losing labels or length."""
    xml_path = _write_sample_xml(tmp_path)
    parse_result = parse_cvat_document(xml_path)
    scenes_by_frame = {}
    for payload in parse_result.payloads:
        scene = decode_cvat_frame(payload)
        assert isinstance(scene.time_slice.start, int)
        scenes_by_frame[scene.time_slice.start] = scene
    stale_cache = IndexedFrameCache(xml_path)
    stale_cache.save(frames=scenes_by_frame, meta={"timestamp_to_sequence": {"0": 0, "1": 1}})
    stale_cache.close()

    provider = CVATSceneDataProvider(xml_path)
    try:
        _assert_serves_sample(provider)
    finally:
        provider.close()

    with patch(PARSE, side_effect=AssertionError("The rebuilt cache must be reused")):
        provider = CVATSceneDataProvider(xml_path)
        try:
            _assert_serves_sample(provider)
        finally:
            provider.close()
