"""Tests for the ONVIF XML data provider."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ax_devil.core.data_types import FrameIdentifier
from ax_devil.modules.plugin_system import get_file_decoder_factory
from ax_devil.modules.scene.model import EntityId
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML

pytestmark = pytest.mark.usefixtures("isolated_cache")


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


def test_registered_onvif_handler_reads_one_frame_per_xml_line(tmp_path: Path) -> None:
    """Blank lines are skipped and each frame is found by its UTC time."""
    xml_file = tmp_path / "frames.xml"
    xml_file.write_text(f"{SIMPLE_FRAME}\n\n   \n{NESTED_FRAME}\n", encoding="utf-8")
    provider = get_file_decoder_factory(ONVIF_XML)(str(xml_file))
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
