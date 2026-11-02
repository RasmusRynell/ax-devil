#!/usr/bin/env python3
"""Converts a CSV of per-object rows into ax-devil Scenes.

The model converter: read the request line from stdin, the overlay file from the path in it, and write one JSON
line per fragment on stdout. Rows of one frame become several `sample` lines; ax-devil merges them by start key.
Geometry is in pixels in the CSV and normalised here with the `width` and `height` options. See the write-plugin
skill for the format.
"""

import csv
import json
import sys

COLORS = {"red": [200, 20, 20], "blue": [20, 40, 200], "white": [240, 240, 240]}


def emit(line):
    print(json.dumps(line, separators=(",", ":")), flush=True)


def number(text):
    """A float, or None for an empty cell."""
    return float(text) if text else None


def geometry(row, width, height):
    if row["polygon"]:
        points = [point.split(":") for point in row["polygon"].split(";")]
        return {"polygon": [[float(x) / width, float(y) / height] for x, y in points]}
    left, top, right, bottom = (float(value) for value in row["box"].split(":"))
    return {"box": [left / width, top / height, right / width, bottom / height]}


def observation(row, frame, width, height, camera):
    attributes = {}
    if row["color"]:
        attributes["color_list"] = [{"name": row["color"], "rgb": COLORS[row["color"]], "score": 0.8}]
    return {
        "time": {"frame": frame},
        "geometry": geometry(row, width, height),
        "classifications": [
            {
                "type": row["class"],
                "score": number(row["score"]),
                "confidence": number(row["confidence"]),
                "attributes": attributes,
            }
        ],
        "measurements": {"image_velocity": {"vx": float(row["vx"]), "vy": float(row["vy"])}},
        "source_data": {"camera": camera, "raw_class": row["class"]},
        "debug": {"row": row["frame"] + ":" + row["id"]},
    }


def sample(row, width, height, camera):
    frame = int(row["frame"])
    entity = {
        "id": row["id"],
        "end_reason": row["end"] or None,
        "motion_state": row["state"] or None,
        "observations": [observation(row, frame, width, height, camera)],
    }
    scene = {"time": {"start": {"frame": frame}, "end": {"frame": frame}}, "entities": [entity]}
    if row["renamed_from"]:
        scene["events"] = [
            {
                "time": {"start": {"frame": frame}, "end": {"frame": frame}},
                "operation": {"rename": {"from": row["renamed_from"], "to": row["id"]}},
            }
        ]
    return scene


def main():
    request = json.loads(sys.stdin.readline())
    options = request["options"]
    width, height = float(options["width"]), float(options["height"])
    camera = options["camera"]
    emit(
        {
            "header": {
                "coverage": "dense",
                "filter": [
                    {"id": "show_car", "label": "Cars", "default": True, "types": ["car"]},
                    {"id": "show_person", "label": "People", "default": True, "types": ["person"]},
                ],
                "metadata": {"source": "python-tracks", "camera": camera},
            }
        }
    )
    with open(request["path"], newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                if None in row.values():  # a short row: DictReader leaves its missing cells None
                    raise ValueError("fewer cells than the header")
                emit({"sample": sample(row, width, height, camera)})
            except (KeyError, ValueError) as error:
                emit({"skipped": f"row {line}: {error}"})


main()
