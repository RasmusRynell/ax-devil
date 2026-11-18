"""Regenerate Raw-copy fixtures through the reference app; maintenance only, never run by CI."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(reference: Path, output: Path) -> None:
    """Read the reference without writing bytecode, and capture its exact section-copy output."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(reference / "src"))
    from ax_devil.modules.object_inspection.fields_model import _json_of
    from ax_devil.modules.scene.inspection import FieldGroup, entity_fields
    from ax_devil.modules.scene.model import BoundingBox, Entity, EntityId, Observation

    cases = []
    inputs = [
        ("both clocks", 17, 1250000, 'é😀\n"', {}),
        ("large integer", 9007199254740993, None, None, {}),
        (
            "nested diagnostics", 2, None, "done",
            {"nested": {"nonascii": "é😀", "big": 9007199254740993,
                        "float": 1.2345, "list": [{}, [], None, False]}},
        ),
        ("timestamp only", None, 1700000000123456, None, {"float": -0.0}),
        ("empty groups", None, None, None, {}),
    ]
    for name, frame, us, reason, diagnostics in inputs:
        entity = Entity(EntityId("7"), end_reason=reason)
        observations = []
        if frame is not None or us is not None:
            timestamp = (
                None if us is None else
                datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=us)
            )
            entity.observations.append(Observation(
                BoundingBox.from_xywh(0., 0., 1., 1.), frame_number=frame,
                timestamp=timestamp, debug=diagnostics,
            ))
            observations = [{
                "time": {key: value for key, value in [("frame", frame), ("us", us)]
                         if value is not None},
                "geometry": {"box": [0., 0., 1., 1.]},
                "debug": diagnostics,
            }]
        groups = dict(entity_fields(entity))
        raw = _json_of(groups.get(FieldGroup.RAW, ()))
        clock = {"us": us} if us is not None else {"frame": frame or 0}
        scene = {
            "time": {"start": clock, "end": clock},
            "entities": [{"id": "7", "observations": observations, "end_reason": reason}],
        }
        cases.append({"name": name, "scene": scene, "raw": raw})
    # Binary output keeps LF bytes identical on Windows as well as Unix.
    output.write_bytes((json.dumps(cases, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="reference checkout at the documented commit")
    parser.add_argument("output", type=Path, help="JSON file to write")
    arguments = parser.parse_args()
    generate(arguments.reference, arguments.output)
