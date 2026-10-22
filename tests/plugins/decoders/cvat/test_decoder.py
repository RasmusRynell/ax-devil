from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.scene.model import Attribute, BoundingBox, EntityId, Polygon
from ax_devil.plugins.decoders.cvat.decoder import decode_cvat_frame
from ax_devil.plugins.decoders.cvat.xml_reader import parse_cvat_document

from .sample_data import write_sample_cvat_xml


def _write_sample(tmp_path: Path) -> Path:
    xml_path = tmp_path / "sample_cvat.xml"
    write_sample_cvat_xml(xml_path)
    return xml_path


def test_parse_cvat_document_reads_task_metadata_and_one_payload_per_frame(tmp_path: Path) -> None:
    xml_path = _write_sample(tmp_path)

    parse_result = parse_cvat_document(xml_path)
    assert parse_result.metadata.width == 1280
    assert parse_result.metadata.height == 720
    assert parse_result.metadata.task_name == "Demo Task"
    assert parse_result.metadata.labels == ("car", "person")
    assert parse_result.metadata.size == 2
    assert len(parse_result.payloads) == 2


def test_decode_cvat_frame_reconstructs_entities_and_geometry(tmp_path: Path) -> None:
    xml_path = _write_sample(tmp_path)
    parse_result = parse_cvat_document(xml_path)

    frame0 = decode_cvat_frame(parse_result.payloads[0])
    assert set(frame0.entities.keys()) == {EntityId("cvat_track_10")}
    frame0_entity = frame0.entities[EntityId("cvat_track_10")]
    assert isinstance(frame0_entity.observations[0].geometry, BoundingBox)
    assert frame0_entity.observations[0].geometry.as_xywh() == pytest.approx(
        (100 / 1280, 200 / 720, 100 / 1280, 200 / 720)
    )
    assert frame0_entity.observations[0].classification[0].type == "car"
    assert frame0_entity.observations[0].frame_number == 0

    attributes = frame0_entity.observations[0].classification[0].attributes
    assert Attribute("color", "blue") in attributes

    frame1 = decode_cvat_frame(parse_result.payloads[1])
    assert set(frame1.entities.keys()) == {EntityId("cvat_track_10"), EntityId("cvat_track_11")}

    polygon_observation = frame1.entities[EntityId("cvat_track_11")].observations[0]
    assert isinstance(polygon_observation.geometry, Polygon)
    assert [(point.x, point.y) for point in polygon_observation.geometry.points] == [
        (50 / 1280, 60 / 720),
        (80 / 1280, 60 / 720),
        (80 / 1280, 90 / 720),
        (50 / 1280, 90 / 720),
    ]
    assert polygon_observation.classification[0].type == "person"
    assert polygon_observation.frame_number == 1
    car_attributes = frame1.entities[EntityId("cvat_track_10")].observations[0].classification[0].attributes
    assert any(attr.name == "occluded" and attr.value is True for attr in car_attributes)


def test_outside_shape_is_not_decoded_as_a_visible_entity(tmp_path: Path) -> None:
    """A track leaving the image must not leave a phantom overlay in that frame."""
    xml_path = _write_sample(tmp_path)
    xml_path.write_text(xml_path.read_text().replace('<box frame="1" outside="0"', '<box frame="1" outside="1"'))

    frames = [decode_cvat_frame(payload) for payload in parse_cvat_document(xml_path).payloads]

    assert set(frames[0].entities) == {EntityId("cvat_track_10")}
    assert set(frames[1].entities) == {EntityId("cvat_track_11")}


def test_cvat_object_metadata_survives_xml_to_scene(tmp_path: Path) -> None:
    """Inspection must retain group IDs and XML fields without a scene-model equivalent."""
    xml_path = _write_sample(tmp_path)
    xml_path.write_text(
        xml_path.read_text()
        .replace(
            '<track id="10" label="Car">',
            '<track id="10" label="Car" group_id="7" source="manual"><attribute name="reviewed">yes</attribute>',
        )
        .replace('z_order="0">', 'z_order="0" rotation="15" vendor_field="extra">')
        .replace('z_order="1"', 'z_order="1" group_id="0"')
        .replace(
            '<attribute name="color">blue</attribute>',
            '<attribute name="color" origin="review">blue</attribute><extra><value unit="m">12</value></extra>',
        )
    )

    parsed = parse_cvat_document(xml_path)
    scene = decode_cvat_frame(parsed.payloads[0])
    observation = scene.entities[EntityId("cvat_track_10")].observations[0]

    assert Attribute("group_id", 7) in observation.classification[0].attributes
    assert Attribute("reviewed", "yes") in observation.classification[0].attributes
    raw = observation.debug["cvat"]
    assert raw["track"]["@attributes"]["source"] == "manual"
    assert raw["shape"]["@attributes"]["rotation"] == "15"
    assert raw["shape"]["@attributes"]["vendor_field"] == "extra"
    assert raw["shape"]["attribute"] == {"@attributes": {"name": "color", "origin": "review"}, "#text": "blue"}
    assert raw["shape"]["extra"]["value"] == {"@attributes": {"unit": "m"}, "#text": "12"}

    next_scene = decode_cvat_frame(parsed.payloads[1])
    next_observation = next_scene.entities[EntityId("cvat_track_10")].observations[0]
    assert Attribute("group_id", 0) in next_observation.classification[0].attributes


def test_cvat_image_metadata_and_unrendered_shapes_are_retained(tmp_path: Path) -> None:
    """Image dimensions govern geometry; unsupported and outside shapes remain available as debug data."""
    xml_path = tmp_path / "images.xml"
    xml_path.write_text(
        "<annotations><version>1.1</version><meta><task><size>3</size></task></meta>"
        '<image id="0" name="frame.jpg" width="200" height="100" subset="validation">'
        '<box label="Car" group_id="3" xtl="20" ytl="10" xbr="60" ybr="30"/>'
        '<points label="Person" points="5,6" custom="kept"/>'
        '</image><image id="1" name="next.jpg" width="200" height="100">'
        '<box label="Car" outside="1" xtl="20" ytl="10" xbr="60" ybr="30"/>'
        '</image><image id="2" name="empty.jpg" width="200" height="100"/></annotations>'
    )

    parsed = parse_cvat_document(xml_path)
    scene, outside_scene, empty_scene = [decode_cvat_frame(payload) for payload in parsed.payloads]
    observation = next(iter(scene.entities.values())).observations[0]

    assert isinstance(observation.geometry, BoundingBox)
    assert observation.geometry.as_xywh() == pytest.approx((0.1, 0.1, 0.2, 0.2))
    assert Attribute("group_id", 3) in observation.classification[0].attributes
    assert observation.debug["cvat"]["image"]["@attributes"]["name"] == "frame.jpg"
    assert scene.debug["cvat"]["image"]["@attributes"]["subset"] == "validation"
    unrendered = scene.debug["cvat"]["unrendered_shapes"][0]
    assert unrendered["shape_type"] == "points"
    assert unrendered["shape"]["@attributes"]["custom"] == "kept"
    assert not outside_scene.entities
    assert outside_scene.debug["cvat"]["unrendered_shapes"][0]["shape"]["@attributes"]["outside"] == "1"
    assert not empty_scene.entities
    assert empty_scene.debug["cvat"]["image"]["@attributes"]["name"] == "empty.jpg"
    assert parsed.metadata.all_metadata["@document"]["version"] == "1.1"
