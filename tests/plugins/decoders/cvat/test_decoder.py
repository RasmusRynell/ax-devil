from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.scene.model import Attribute, BoundingBox, EntityId, Polygon
from ax_devil.plugins.decoders.cvat.decoder import decode_cvat_frame, parse_cvat_document

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
