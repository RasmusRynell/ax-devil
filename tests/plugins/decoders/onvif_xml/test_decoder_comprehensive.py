"""Sample-frame tests for the ONVIF XML decoder."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ax_devil.modules.scene.model import RGB, BoundingBox, ColorClassification, Delete, EntityId, Rename, Scene
from ax_devil.plugins.decoders.onvif_xml.decoder import decode_onvif_xml

_EXPECTED_TIME = datetime(2025, 3, 11, 8, 11, 54, 8178, tzinfo=timezone.utc)
_EXPECTED_CLASSIFICATIONS = {
    "101": ("bus", 0.75),
    "102": ("car", 0.70),
    "103": ("human", 0.65),
    "104": ("face", 0.60),
    "105": ("license_plate", 0.55),
    "106": ("bike", 0.50),
    "107": ("truck", 0.45),
    "108": ("vehicle", 0.40),
}
_EXPECTED_VEHICLE_COLORS = {
    (255, 255, 255, 0.8),
    (128, 128, 128, 0.8),
    (0, 0, 0, 0.8),
    (255, 0, 0, 0.8),
    (0, 0, 255, 0.8),
    (0, 128, 0, 0.8),
    (255, 255, 0, 0.8),
}


@pytest.fixture
def sample_scene() -> Scene:
    """Decode the bundled sample frame once per test."""
    sample_path = Path(__file__).parent / "data" / "sample_frame.xml"
    xml_content = sample_path.read_text()
    xml_content = xml_content.replace('=" ', '="').replace(' " ', '" ')
    return decode_onvif_xml(xml_content.replace("SampleFrame", "Frame"))


def test_sample_frame_decodes_scene_entities_and_timestamps(sample_scene: Scene) -> None:
    assert sample_scene.time_slice.start == _EXPECTED_TIME
    assert sample_scene.time_slice.end == _EXPECTED_TIME
    assert set(sample_scene.entities) == {EntityId(entity_id) for entity_id in _EXPECTED_CLASSIFICATIONS}

    for entity in sample_scene.entities.values():
        assert len(entity.observations) == 1
        observation = entity.observations[0]
        assert observation.timestamp == _EXPECTED_TIME
        assert isinstance(observation.geometry, BoundingBox)
        assert 0 <= observation.geometry.top_left.x <= 1
        assert 0 <= observation.geometry.top_left.y <= 1
        assert 0 < observation.geometry.width <= 1
        assert 0 < observation.geometry.height <= 1

    bus_geometry = sample_scene.entities[EntityId("101")].observations[0].geometry
    assert isinstance(bus_geometry, BoundingBox)
    assert bus_geometry.top_left.x == pytest.approx(0.2, abs=0.01)
    assert bus_geometry.top_left.y == pytest.approx(0.2, abs=0.01)
    assert bus_geometry.width == pytest.approx(0.2, abs=0.01)
    assert bus_geometry.height == pytest.approx(0.2, abs=0.01)


def test_sample_frame_decodes_classifications(sample_scene: Scene) -> None:
    for entity_id, (classification_type, score) in _EXPECTED_CLASSIFICATIONS.items():
        observation = sample_scene.entities[EntityId(entity_id)].observations[0]
        classification = next(item for item in observation.classification if item.type == classification_type)
        assert classification.score.value == score


def test_sample_frame_decodes_events(sample_scene: Scene) -> None:
    rename_events = [event for event in sample_scene.events if isinstance(event, Rename)]
    delete_events = [event for event in sample_scene.events if isinstance(event, Delete)]

    assert len(rename_events) == 1
    assert rename_events[0].from_entity_id == "38"
    assert rename_events[0].to_entity_id == "20"
    assert len(delete_events) == 1
    assert delete_events[0].entity_id == "1"


def test_sample_frame_decodes_vehicle_colors(sample_scene: Scene) -> None:
    for entity_id in ("101", "102"):
        actual_colors = {
            (color.rgb.r, color.rgb.g, color.rgb.b, color.score.value)
            for color in _vehicle_colors(sample_scene, entity_id)
        }
        assert _EXPECTED_VEHICLE_COLORS <= actual_colors


def test_sample_frame_decodes_human_clothing_colors(sample_scene: Scene) -> None:
    observation = sample_scene.entities[EntityId("103")].observations[0]
    clothing_attrs = {
        attr.name: attr.value
        for classification in observation.classification
        for attr in classification.attributes
        if attr.name in {"upper_clothing_colors", "lower_clothing_colors"}
    }

    assert set(clothing_attrs) == {"upper_clothing_colors", "lower_clothing_colors"}
    for colors in clothing_attrs.values():
        assert isinstance(colors, list)
        assert colors
        for color in colors:
            assert isinstance(color, ColorClassification)
            assert isinstance(color.rgb, RGB)
            assert 0 <= color.score.value <= 1


def test_malformed_xml_errors() -> None:
    with pytest.raises(ET.ParseError):
        decode_onvif_xml("")

    with pytest.raises(ET.ParseError):
        decode_onvif_xml("<invalid>xml")

    with pytest.raises(ValueError, match="No.*Frame.*elements found"):
        decode_onvif_xml("<root><no_frames/></root>")


def test_minimal_timestamped_frame_without_detections_decodes() -> None:
    xml = (
        '<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" '
        'UtcTime="2025-01-01T00:00:00.000Z">'
        '<tt:Object ObjectId="test"/></tt:Frame>'
    )
    scene = decode_onvif_xml(xml)

    assert isinstance(scene, Scene)


def _vehicle_colors(scene: Scene, entity_id: str) -> list[ColorClassification]:
    observation = scene.entities[EntityId(entity_id)].observations[0]
    colors: list[ColorClassification] = []
    for classification in observation.classification:
        for attr in classification.attributes:
            if "vehicle_colors" in attr.name and isinstance(attr.value, list):
                colors.extend(color for color in attr.value if isinstance(color, ColorClassification))
    return colors
