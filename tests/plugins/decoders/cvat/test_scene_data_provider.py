"""Tests for the CVAT scene data provider."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.cache import IndexedFrameCache
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.model import Scene
from ax_devil.plugins.decoders.cvat.decoder import decode_cvat_frame, parse_cvat_document
from ax_devil.plugins.decoders.cvat.plugin import CVAT
from ax_devil.plugins.decoders.cvat.provider import CVATSceneDataProvider

from .sample_data import write_sample_cvat_xml

pytestmark = pytest.mark.usefixtures("isolated_cache")


def _write_sample_xml(tmp_path: Path) -> Path:
    xml_path = tmp_path / "sample_cvat.xml"
    write_sample_cvat_xml(xml_path)
    return xml_path


def _get_file_decoder_definition(handler_type: str) -> FileToSceneDecoderDefinition:
    for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
        if record.status != PluginStatus.LOADED:
            continue
        definition = cast(DecoderPluginDefinition, record.definition)
        for file_decoder in definition.file_to_scene_decoders:
            if file_decoder.handler_type == handler_type:
                return file_decoder
    raise ValueError(f"Unsupported file-to-scene decoder type: {handler_type}")


def test_cvat_provider_decodes_and_exposes_metadata(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)

    provider = CVATSceneDataProvider(xml_path)
    try:
        metadata = provider.get_cvat_metadata()
        assert metadata.labels == ("car", "person")
        assert provider.get_available_frames() == {0, 1}

        filter_config = provider.get_filter_config()
        option_ids = {option.id for option in filter_config.options}
        assert option_ids == {"show_car", "show_person"}

        frame0_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=0.0)
        frame0 = provider.load_by_frame_id(frame0_id)
        assert isinstance(frame0, Scene)
        assert frame0 is not None
        assert len(frame0.entities) == 1
    finally:
        provider.close()


def test_cvat_provider_restores_metadata_from_cache(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)

    warm_metadata = None
    provider = CVATSceneDataProvider(xml_path)
    try:
        warm_metadata = provider.get_cvat_metadata()
    finally:
        provider.close()

    assert warm_metadata is not None

    with (
        patch(
            "ax_devil.plugins.decoders.cvat.provider.parse_cvat_document",
            side_effect=AssertionError("Warm cache must not parse the source again"),
        ),
    ):
        provider = CVATSceneDataProvider(xml_path)
        try:
            cached_metadata = provider.get_cvat_metadata()
            assert cached_metadata == warm_metadata
            scene = provider.load_by_frame_id(FrameIdentifier(sequence_id=1, timestamp_monotime_us=1.0))
            assert scene is not None
            assert set(scene.entities) == {"cvat_track_10", "cvat_track_11"}
        finally:
            provider.close()


def test_cvat_provider_regenerates_cache_when_required_store_metadata_missing(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)

    parse_result = parse_cvat_document(xml_path)
    scenes_by_frame = {}
    for payload in parse_result.payloads:
        scene = decode_cvat_frame(payload)
        frame_key = scene.time_slice.start
        assert isinstance(frame_key, int)
        scenes_by_frame[frame_key] = scene

    incomplete_metadata = {
        "width": parse_result.metadata.width,
        "height": parse_result.metadata.height,
        "task_name": parse_result.metadata.task_name,
        "start_frame": parse_result.metadata.start_frame,
        "stop_frame": parse_result.metadata.stop_frame,
        "size": parse_result.metadata.size,
        "labels": list(parse_result.metadata.labels),
        "all_metadata": parse_result.metadata.all_metadata,
        "timestamp_to_sequence": {"0": 0, "1": 1},
    }

    incomplete_cache = IndexedFrameCache(xml_path)
    incomplete_cache.save(frames=scenes_by_frame, meta=incomplete_metadata)
    incomplete_cache.close()

    provider = CVATSceneDataProvider(xml_path)
    try:
        metadata = provider.get_cvat_metadata()
        assert metadata.labels == parse_result.metadata.labels
        option_ids = {option.id for option in provider.get_filter_config().options}
        assert option_ids == {"show_car", "show_person"}
    finally:
        provider.close()

    refreshed_cache = IndexedFrameCache(xml_path)
    try:
        refreshed_metadata = refreshed_cache.load_metadata()
        assert isinstance(refreshed_metadata, dict)
        assert "artifact_identity" in refreshed_metadata
        assert "cvat_metadata" in refreshed_metadata
    finally:
        refreshed_cache.close()


def test_cvat_provider_metadata_accessors(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)

    provider = CVATSceneDataProvider(xml_path)
    try:
        task_name = provider.get_metadata_value("task.name")
        assert task_name == "Demo Task"

        paths = provider.list_metadata_paths()
        assert "task.name" in paths
    finally:
        provider.close()


def test_cvat_provider_registry_entries_exist(tmp_path: Path) -> None:
    xml_path = _write_sample_xml(tmp_path)

    handler_cls = _get_file_decoder_definition(CVAT).factory
    assert handler_cls is CVATSceneDataProvider

    provider = handler_cls(str(xml_path))
    try:
        assert isinstance(provider, CVATSceneDataProvider)
        assert provider.get_cvat_metadata().labels == ("car", "person")
    finally:
        provider.close()
