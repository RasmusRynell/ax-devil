"""Tests for the ONVIF XML data provider."""

from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from unittest.mock import patch

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.cache import CacheManager
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.scene.model import EntityId, Scene
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML
from ax_devil.plugins.decoders.onvif_xml.provider import ONVIFXMLDataProvider

SIMPLE_FRAME = (
    '<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="2024-01-01T00:00:00Z">'
    '<tt:Object ObjectId="obj-1">'
    "<tt:Appearance>"
    "<tt:Class>"
    '<tt:Type Likelihood="0.95">person</tt:Type>'
    "</tt:Class>"
    "<tt:Shape>"
    '<tt:BoundingBox left="-0.2" top="0.2" right="0.2" bottom="-0.2" />'
    "</tt:Shape>"
    "</tt:Appearance>"
    "</tt:Object>"
    "</tt:Frame>"
)

NESTED_FRAME = (
    '<tt:MetadataStream xmlns:tt="http://www.onvif.org/ver10/schema">'
    "<tt:VideoAnalytics>"
    '<tt:Frame UtcTime="2024-01-01T00:00:01Z" />'
    "</tt:VideoAnalytics>"
    "</tt:MetadataStream>"
)


def _get_file_decoder_definition(handler_type: str) -> FileToSceneDecoderDefinition:
    for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
        if record.status != PluginStatus.LOADED:
            continue
        definition = cast(DecoderPluginDefinition, record.definition)
        for file_decoder in definition.file_to_scene_decoders:
            if file_decoder.handler_type == handler_type:
                return file_decoder
    raise ValueError(f"Unsupported file-to-scene decoder type: {handler_type}")


def test_onvif_xml_provider_reads_lines_and_decodes(tmp_path: Path) -> None:
    """Ensure the provider reads non-empty XML lines and decodes them with the ONVIF decoder."""
    xml_file = tmp_path / "frames.xml"
    xml_file.write_text(f"{SIMPLE_FRAME}\n\n   \n{NESTED_FRAME}\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    with patch.object(CacheManager, "get_cache_subdir", return_value=cache_dir):
        provider = ONVIFXMLDataProvider(file_path=xml_file)
        try:
            assert provider.get_total_frames() == 2
            expected_ts_0 = 1_704_067_200_000_000
            expected_ts_1 = 1_704_067_201_000_000
            assert provider.get_available_frames() == {expected_ts_0, expected_ts_1}

            scene_0 = provider.load_by_frame_id(FrameIdentifier(0, expected_ts_0))
            assert scene_0 is not None
            assert scene_0.time_slice.start == datetime(2024, 1, 1, tzinfo=timezone.utc)
            assert set(scene_0.entities) == {EntityId("obj-1")}

            scene_1 = provider.load_by_frame_id(FrameIdentifier(1, expected_ts_1))
            assert scene_1 is not None
            assert scene_1.time_slice.start == datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
            assert scene_1.entities == {}
        finally:
            provider.close()


def test_onvif_xml_factory_creates_provider(tmp_path: Path) -> None:
    """Factory lookup should construct a working ONVIF XML provider."""
    xml_file = tmp_path / "sample.xml"
    xml_file.write_text(f"{SIMPLE_FRAME}\n", encoding="utf-8")

    handler_class = _get_file_decoder_definition(ONVIF_XML).factory
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    with patch.object(CacheManager, "get_cache_subdir", return_value=cache_dir):
        provider = handler_class(str(xml_file))
        try:
            assert isinstance(provider, ONVIFXMLDataProvider)
            assert provider.get_total_frames() == 1

            ts_expected = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000)
            frame_id = FrameIdentifier(sequence_id=0, timestamp_monotime_us=float(ts_expected))
            scene = provider.load_by_frame_id(frame_id)
            assert isinstance(scene, Scene)
            assert scene is not None
            assert scene.time_slice.start == datetime(2024, 1, 1, tzinfo=timezone.utc)
        finally:
            provider.close()
