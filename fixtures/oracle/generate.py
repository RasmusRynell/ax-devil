"""Oracle generator: runs the OLD Python catalog engine and MOT decoder and writes the fixtures next to this file.

Runs only against a checkout of the reference commit e31a7453 with its Python environment (see README.md):

    QT_QPA_PLATFORM=offscreen <old-tree>/.venv/bin/python fixtures/oracle/generate.py --old-tree <old-tree>

It writes sheets/*.json (input Scenes), catalogs/<catalog>/<sheet>[.<variant>].json (canonical Drawing Instructions)
and mot/*.scenes.json + mot/*.warning.json (decoded MOT Scenes). The canonical forms are described in README.md.
"""

from __future__ import annotations

import argparse
import configparser
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE_COMMIT = "e31a7453"
TARGET = (1280, 720)
DECIMALS = 5
SCENE_VERSION = "4.0"

# (catalog file, sheets to dump). Standard and Tracking get every sheet; the others a smoke subset to stay small.
ALL_SHEETS = None
SMOKE_SHEETS = ("classes", "relations", "polygons")
CATALOGS = {
    "standard": ALL_SHEETS,
    "tracking": ALL_SHEETS,
    "minimal": SMOKE_SHEETS,
    "chunky": SMOKE_SHEETS,
    "glass": SMOKE_SHEETS,
    "classic": SMOKE_SHEETS,
}
# (catalog, sheet, variant name, features hidden by the viewer)
VARIANTS = (
    ("standard", "classes", "no-ids-score", ("ids", "score")),
    ("standard", "relations", "no-relations", ("relations",)),
    ("tracking", "classes", "no-ids", ("ids",)),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-tree", required=True, type=Path, help="checkout of the reference commit")
    parser.add_argument("--out", type=Path, default=HERE, help="fixtures/oracle folder to write")
    args = parser.parse_args()
    old = args.old_tree.resolve()
    sys.path.insert(0, str(old / "src"))
    try:
        from ax_devil.modules.scene import model  # noqa: F401
    except ImportError as error:
        raise SystemExit(f"{old} is not a usable checkout of {REFERENCE_COMMIT}: {error}") from error
    generate(old, args.out)


def generate(old: Path, out: Path) -> None:
    sheets = build_sheets()
    for name, scene in sheets.items():
        write(out / "sheets" / f"{name}.json", scene_document([scene]))
    for catalog, wanted in CATALOGS.items():
        for name, scene in sheets.items():
            if wanted is None or name in wanted:
                write_catalog(old, out, catalog, name, scene, ())
    for catalog, name, variant, hidden in VARIANTS:
        write_catalog(old, out, catalog, name, sheets[name], hidden, variant)
    generate_mot(out / "mot")


# ---------------------------------------------------------------- sheets (input Scenes)


def build_sheets() -> dict:
    """Seeded input Scenes; every value is authored, none depends on the platform."""
    from ax_devil.modules.scene.model import Scene, TimeSlice

    def scene() -> Scene:
        return Scene(TimeSlice(0, 0))

    sheets: dict[str, Scene] = {"empty": scene()}
    sheets["mot_like"] = mot_like(scene())
    sheets["classes"] = classes(scene())
    sheets["polygons"] = polygons(scene())
    sheets["relations"] = relations(scene())
    sheets["edges"] = edges(scene())
    sheets["history"] = history(scene())
    sheets["seeded_mixed"] = seeded_mixed(scene())
    return sheets


def box(x, y, w, h):
    from ax_devil.modules.scene.model import BoundingBox

    return BoundingBox.from_xywh(x, y, w, h, allow_outside=True)


def obs(geometry, *classes, frame=0, velocity=None):
    """An observation; each class is (type, score, confidence, {attribute: value})."""
    from ax_devil.modules.scene.model import Attribute, Classification, Confidence, ImageVelocity, Observation, Score

    classifications = [
        Classification(
            kind,
            None if score is None else Score(score),
            [Attribute(key, value) for key, value in attributes.items()],
            None if confidence is None else Confidence(confidence),
        )
        for kind, score, confidence, attributes in classes
    ]
    return Observation(
        geometry=geometry,
        classification=classifications,
        frame_number=frame,
        velocity_in_image_space=None if velocity is None else ImageVelocity(*velocity),
    )


def put(scene, entity_id, *observations, motion=None):
    from ax_devil.modules.scene.model import Entity, EntityId, MotionState

    entity = Entity(
        EntityId(entity_id),
        list(observations),
        motion_state=None if motion is None else MotionState(motion),
    )
    scene.add_entity(entity)
    return entity


def colors(*items):
    """A list of ColorClassification from (name, (r, g, b), score)."""
    from ax_devil.modules.scene.model import RGB, ColorClassification, Score

    return [ColorClassification(name, RGB(*rgb), Score(score)) for name, rgb, score in items]


def mot_like(scene):
    attrs = {"class_id": 1, "source": "det", "visibility_ratio": 1.0}
    put(scene, "1", obs(box(0.10, 0.20, 0.05, 0.30), ("person", 0.91, None, attrs)))
    put(scene, "2", obs(box(0.95, 0.50, 0.10, 0.20), ("person", 5.3, None, attrs)))
    put(scene, "untracked:0:2", obs(box(0.50, 0.50, 0.10, 0.07), ("person", 0.125, None, attrs)))
    put(scene, "3", obs(box(0.30, 0.60, 0.06, 0.12), ("?99", None, None, {"class_id": 99, "source": "gt"})))
    gt = {"class_id": 7, "source": "gt", "visibility_ratio": 0.5, "gt_mark": 1}
    put(scene, "4", obs(box(0.70, 0.10, 0.08, 0.20), ("static_person", None, None, gt)))
    put(scene, "5", obs(box(0.55, 0.30, 0.20, 0.25), ("car", 0.375, None, {"class_id": 3, "source": "det"})))
    return scene


def classes(scene):
    red, blue = (200, 30, 30), (30, 60, 200)
    put(
        scene,
        "human-1",
        obs(
            box(0.10, 0.15, 0.08, 0.40),
            (
                "human",
                0.93,
                0.88,
                {
                    "upper_clothing_colors": colors(("red", red, 0.9), ("blue", blue, 0.4)),
                    "lower_clothing_colors": colors(("blue", blue, 0.7)),
                    "occluded": False,
                },
            ),
            velocity=(0.04, -0.01),
        ),
        motion="moving",
    )
    put(
        scene,
        "human-2",
        obs(box(0.22, 0.30, 0.05, 0.25), ("human", 0.31, None, {"occluded": True}), velocity=(0.0, 0.0)),
        motion="stationary",
    )
    put(
        scene,
        "car-1",
        obs(
            box(0.40, 0.50, 0.25, 0.20),
            ("car", 0.97, 0.9, {"vehicle_colors": colors(("red", red, 0.8), ("blue", blue, 0.95))}),
            velocity=(0.12, 0.02),
        ),
        motion="moving",
    )
    put(scene, "bus-1", obs(box(0.05, 0.60, 0.30, 0.25), ("bus", 0.66, None, {})))
    put(scene, "truck-1", obs(box(0.70, 0.55, 0.28, 0.30), ("truck", None, 0.42, {})), motion="unknown")
    put(scene, "bike-1", obs(box(0.62, 0.20, 0.06, 0.12), ("bike", 0.5, None, {})))
    put(scene, "bicycle-1", obs(box(0.75, 0.20, 0.06, 0.12), ("bicycle", 0.52, None, {})))
    put(scene, "vehicle-other-1", obs(box(0.85, 0.05, 0.10, 0.10), ("vehicle_other", 0.7, None, {})))
    put(scene, "head-1", obs(box(0.12, 0.08, 0.04, 0.07), ("head", 0.85, None, {"face_visible": 0.8, "occluded": False})))
    put(scene, "head-2", obs(box(0.24, 0.24, 0.03, 0.05), ("head", 0.4, None, {"face_visible": 0.0, "occluded": True})))
    put(scene, "dog-1", obs(box(0.45, 0.10, 0.10, 0.08), ("animal", 0.77, None, {})))
    put(scene, "unmapped-1", obs(box(0.58, 0.05, 0.05, 0.05), ("?99", 0.2, None, {})))
    put(scene, "unclassified-1", obs(box(0.30, 0.85, 0.10, 0.10)), motion="moving")
    put(scene, "unclassified-2", obs(box(0.45, 0.85, 0.10, 0.10)), motion="stationary")
    return scene


def polygons(scene):
    from ax_devil.modules.scene.model import NormalizedPoint, Polygon

    def shape(*points):
        return Polygon([NormalizedPoint(x, y, allow_outside=True) for x, y in points])

    put(
        scene,
        "human-poly",
        obs(shape((0.10, 0.20), (0.18, 0.22), (0.20, 0.60), (0.12, 0.65), (0.08, 0.40)), ("human", 0.9, None, {})),
    )
    put(
        scene,
        "car-poly",
        obs(shape((0.40, 0.50), (0.70, 0.52), (0.72, 0.75), (0.38, 0.72)), ("car", 0.8, None, {}), velocity=(0.1, 0.0)),
    )
    put(scene, "plain-poly", obs(shape((0.80, 0.10), (0.95, 0.10), (0.88, 0.30))))
    put(scene, "edge-poly", obs(shape((0.90, 0.80), (1.10, 0.85), (0.95, 1.05)), ("head", 0.6, None, {})))
    return scene


def relations(scene):
    from ax_devil.modules.scene.model import Entity, EntityId, EntityRelation

    put(scene, "person", obs(box(0.10, 0.15, 0.10, 0.50), ("human", 0.9, None, {})))
    put(scene, "head", obs(box(0.13, 0.17, 0.04, 0.07), ("head", 0.8, None, {})))
    put(scene, "bag", obs(box(0.18, 0.40, 0.04, 0.08), ("human", 0.5, None, {})))
    scene.add_entity(Entity(EntityId("ghost"), []))
    for kind, source, target in (
        ("has_part", "person", "head"),
        ("has_part", "person", "bag"),
        ("has_part", "person", "missing"),
        ("has_part", "ghost", "head"),
        ("follows", "head", "bag"),
    ):
        scene.add_relation(EntityRelation(kind, EntityId(source), EntityId(target)))
    return scene


def edges(scene):
    put(scene, "tiny", obs(box(0.50, 0.50, 0.002, 0.003), ("car", 0.5, None, {})))
    put(scene, "zero-area", obs(box(0.20, 0.20, 0.0, 0.1), ("human", 0.5, None, {})))
    put(scene, "full-frame", obs(box(0.0, 0.0, 1.0, 1.0), ("human", 1.0, 1.0, {})))
    put(scene, "very-long-track-identifier-0123456789", obs(box(0.60, 0.10, 0.20, 0.10), ("car", 0.0, 0.0, {})))
    put(scene, "past-edge", obs(box(-0.05, 0.80, 0.15, 0.30), ("human", 123.456, -0.5, {})))
    put(scene, "rounding", obs(box(0.30, 0.40, 0.10, 0.10), ("head", 0.125, 0.995, {})))
    put(scene, "sliver", obs(box(0.80, 0.30, 0.0005, 0.4)))
    put(scene, "fast", obs(box(0.40, 0.70, 0.05, 0.05), ("human", 0.6, None, {}), velocity=(2.0, -2.0)), motion="moving")
    return scene


def history(scene):
    put(
        scene,
        "mover",
        obs(box(0.10, 0.10, 0.10, 0.20), ("human", 0.9, None, {}), frame=0),
        obs(box(0.30, 0.30, 0.10, 0.20), ("car", 0.8, None, {}), frame=1),
        obs(box(0.50, 0.50, 0.10, 0.20), ("human", 0.7, None, {}), frame=2),
    )
    put(
        scene,
        "ranked",
        obs(
            box(0.60, 0.10, 0.10, 0.20),
            ("human", 0.3, None, {}),
            ("car", 0.8, None, {}),
            ("head", None, 0.99, {}),
        ),
    )
    put(scene, "confidence-ranked", obs(box(0.75, 0.10, 0.10, 0.20), ("head", None, 0.2, {}), ("bus", None, 0.6, {})))
    return scene


def seeded_mixed(scene):
    rng = random.Random(20260101)
    kinds = [None, "human", "car", "bus", "truck", "bike", "bicycle", "head", "animal", "?7", "person"]
    for index in range(24):
        kind = rng.choice(kinds)
        width, height = round(rng.uniform(0.02, 0.2), 4), round(rng.uniform(0.04, 0.3), 4)
        x, y = round(rng.uniform(0.0, 1.0 - width), 4), round(rng.uniform(0.0, 1.0 - height), 4)
        score = None if rng.random() < 0.2 else round(rng.uniform(0.0, 1.0), 3)
        velocity = None if rng.random() < 0.5 else (round(rng.uniform(-0.1, 0.1), 3), round(rng.uniform(-0.1, 0.1), 3))
        motion = rng.choice([None, "moving", "stationary", "unknown"])
        classes_ = () if kind is None else ((kind, score, None, {}),)
        put(scene, f"{rng.randrange(1, 400)}-{index}", obs(box(x, y, width, height), *classes_, velocity=velocity), motion=motion)
    return scene


# ---------------------------------------------------------------- Scene JSON (the C2 form)


def time_json(value):
    from datetime import datetime

    from ax_devil.core.time_units import datetime_to_us

    return {"us": datetime_to_us(value)} if isinstance(value, datetime) else {"frame": int(value)}


def value_json(value):
    """Attribute and debug values: JSON scalars, lists and maps; a ColorClassification becomes a map."""
    from ax_devil.modules.scene.model import ColorClassification

    if isinstance(value, ColorClassification):
        return {"name": value.name, "rgb": [value.rgb.r, value.rgb.g, value.rgb.b], "score": value.score.value}
    if isinstance(value, (list, tuple)):
        return [value_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): value_json(item) for key, item in value.items()}
    return value


def geometry_json(geometry):
    from ax_devil.modules.scene.model import BoundingBox

    if isinstance(geometry, BoundingBox):
        return {"box": [geometry.top_left.x, geometry.top_left.y, geometry.bottom_right.x, geometry.bottom_right.y]}
    return {"polygon": [[point.x, point.y] for point in geometry.points]}


def observation_json(observation):
    result = {
        "time": time_json(observation.frame_number if observation.frame_number is not None else observation.timestamp),
        "geometry": geometry_json(observation.geometry),
        "classifications": [
            {
                "type": item.type,
                "score": None if item.score is None else item.score.value,
                "confidence": None if item.confidence is None else item.confidence.value,
                "attributes": {attribute.name: value_json(attribute.value) for attribute in item.attributes},
            }
            for item in observation.classification
        ],
    }
    if observation.velocity_in_image_space is not None:
        velocity = observation.velocity_in_image_space
        result["measurements"] = {"image_velocity": {"vx": velocity.vx, "vy": velocity.vy}}
    if observation.source_data:
        result["source_data"] = value_json(observation.source_data)
    if observation.debug:
        result["debug"] = value_json(observation.debug)
    return result


def entity_json(entity):
    return {
        "id": str(entity.id),
        "observations": [observation_json(item) for item in entity.observations],
        "end_reason": entity.end_reason,
        "motion_state": None if entity.motion_state is None else entity.motion_state.value,
    }


def scene_document(scenes):
    """The C2 document {"scene_version","scenes"} as text, one entity per line."""
    blocks = []
    for scene in scenes:
        relations = sorted(scene.relations, key=lambda item: (item.type, item.source_entity_id, item.target_entity_id))
        head = {"start": time_json(scene.time_slice.start), "end": time_json(scene.time_slice.end)}
        entities = ",\n".join(compact(entity_json(entity)) for entity in scene.entities.values())
        relation_list = [
            {"type": item.type, "source": str(item.source_entity_id), "target": str(item.target_entity_id)}
            for item in relations
        ]
        blocks.append(
            f'{{"time":{compact(head)},"entities":[\n{entities}\n],"events":[],'
            f'"relations":{compact(relation_list)},"debug":{compact(value_json(scene.debug))}}}'
        )
    body = ",\n".join(blocks)
    return f'{{"scene_version":{compact(SCENE_VERSION)},"scenes":[\n{body}\n]}}\n'


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


# ---------------------------------------------------------------- canonical Drawing Instructions (the C5 form)


class Canonical:
    """A DrawingTarget that resolves every batch row to the canonical row of README.md."""

    SHAPE_PAINT = {
        "boxes": ("pen", "pen_width", "dashed", "brush", "radius"),
        "circles": ("pen", "pen_width", "dashed", "brush"),
        "lines": ("pen", "pen_width", "dashed"),
        "points": ("pen", "pen_width"),
        "polygons": ("pen", "pen_width", "dashed", "brush"),
        "polylines": ("pen", "pen_width", "dashed"),
    }

    def __init__(self) -> None:
        self.rows: dict[str, list] = {
            kind: [] for kind in ("boxes", "circles", "lines", "points", "polygons", "polylines", "texts", "labels")
        }

    # paint ---------------------------------------------------------------
    @staticmethod
    def _at(value, row):
        import numpy as np

        item = value[row] if isinstance(value, np.ndarray) and value.ndim else value
        return item.item() if hasattr(item, "item") else item

    def _paint(self, paint, row, fields):
        at = lambda name: self._at(getattr(paint, name), row)  # noqa: E731
        values = {
            "pen": [int(at("pen_r")), int(at("pen_g")), int(at("pen_b"))],
            "pen_width": rounded(float(at("pen_width"))),
            "dashed": bool(at("dashed")),
            "brush": [int(at("brush_r")), int(at("brush_g")), int(at("brush_b")), int(at("brush_a"))],
            "radius": rounded(float(at("radius"))),
        }
        return {name: values[name] for name in fields}

    def _shape(self, kind, geometry, paint, index):
        self.rows[kind].append({**geometry, **self._paint(paint, index, self.SHAPE_PAINT[kind])})

    # DrawingTarget -------------------------------------------------------
    def boxes(self, x, y, w, h, paint):
        for i in range(len(x)):
            geometry = {"x": rounded(x[i]), "y": rounded(y[i]), "w": rounded(w[i]), "h": rounded(h[i])}
            self._shape("boxes", geometry, paint, i)

    def circles(self, x, y, radius, paint):
        for i in range(len(x)):
            geometry = {"x": rounded(x[i]), "y": rounded(y[i]), "r": rounded(radius[i])}
            self._shape("circles", geometry, paint, i)

    def lines(self, x1, y1, x2, y2, paint):
        for i in range(len(x1)):
            geometry = {"x1": rounded(x1[i]), "y1": rounded(y1[i]), "x2": rounded(x2[i]), "y2": rounded(y2[i])}
            self._shape("lines", geometry, paint, i)

    def points(self, x, y, paint):
        for i in range(len(x)):
            self._shape("points", {"x": rounded(x[i]), "y": rounded(y[i])}, paint, i)

    def polygons(self, points, paint):
        for i, shape in enumerate(points):
            self._shape("polygons", {"points": pairs(shape)}, paint, i)

    def polylines(self, points, paint):
        for i, shape in enumerate(points):
            self._shape("polylines", {"points": pairs(shape)}, paint, i)

    def texts(self, x, y, text, anchor, paint):
        for i in range(len(x)):
            self.rows["texts"].append(
                {
                    "x": rounded(x[i]),
                    "y": rounded(y[i]),
                    "text": str(text[i]),
                    "anchor": str(anchor[i]),
                    "color": [int(self._at(paint.pen_r, i)), int(self._at(paint.pen_g, i)), int(self._at(paint.pen_b, i))],
                    "font_family": str(self._at(paint.font_family, i)),
                    "font_size": rounded(float(self._at(paint.font_size, i))),
                }
            )

    def labels(self, x, y, anchor, content):
        import numpy as np

        count = len(x)
        anchors = anchor.tolist() if isinstance(anchor, np.ndarray) else [anchor] * count
        for i in range(count):
            runs = []
            for run in content.runs:
                text = str(self._at(run.text, i))
                bar = float(self._at(run.bar, i))
                if not self._at(run.present, i) or not (text or not math.isnan(bar)):
                    continue
                runs.append(
                    {
                        "text": text,
                        "bar": None if math.isnan(bar) else rounded(bar),
                        "rgb": [int(self._at(run.red, i)), int(self._at(run.green, i)), int(self._at(run.blue, i))],
                        "weight": str(self._at(run.weight, i)),
                    }
                )
            if not runs:
                continue
            background = None
            if content.background is not None:
                red, green, blue, alpha = (int(self._at(channel, i)) for channel in content.background)
                background = [red, green, blue, alpha] if alpha else None
            self.rows["labels"].append(
                {
                    "x": rounded(x[i]),
                    "y": rounded(y[i]),
                    "anchor": str(anchors[i]),
                    "size": rounded(float(self._at(content.size, i))),
                    "family": str(self._at(content.family, i)),
                    "background": background,
                    "padding_x": rounded(float(self._at(content.padding_x, i))),
                    "padding_y": rounded(float(self._at(content.padding_y, i))),
                    "radius": rounded(float(self._at(content.radius, i))),
                    "gap": rounded(float(self._at(content.gap, i))),
                    "runs": runs,
                }
            )

    def document(self) -> dict:
        return {kind: sorted(rows, key=sort_key) for kind, rows in self.rows.items()}


def rounded(value) -> float:
    number_ = round(float(value), DECIMALS)
    return 0.0 if number_ == 0 else number_


def pairs(shape):
    return [[rounded(x), rounded(y)] for x, y in (tuple(map(float, point)) for point in shape)]


def sort_key(row):
    """Leaves in field order, each tagged by type so None, numbers and text never compare to each other."""

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from leaves(item)
        elif value is None:
            yield (0, 0)
        elif isinstance(value, str):
            yield (2, value)
        else:
            yield (1, float(value))

    return tuple(leaves(row))


def write_catalog(old: Path, out: Path, catalog: str, sheet: str, scene, hidden, variant: str | None = None) -> None:
    from ax_devil.modules.scene.rendering.catalog_loader import SceneRenderCatalogLoader
    from ax_devil.modules.scene.rendering.visibility import OverlayFeature, OverlayVisibility
    from ax_devil.modules.video_player.engine.render_context import RenderContext

    path = old / "src/ax_devil/modules/scene/rendering/catalog_definitions" / f"{catalog}.json"
    revision = SceneRenderCatalogLoader().load_revision(path)
    visibility = OverlayVisibility(frozenset(OverlayFeature(name) for name in hidden))
    compiled = revision.specialize(visibility)
    target, diagnostics = Canonical(), []
    compiled.render_scene(scene, RenderContext.create(*TARGET), target, diagnostics=diagnostics)
    instructions = target.document()
    lines = [
        f'"{kind}":[' + ("\n" + ",\n".join(compact(row) for row in rows) + "\n]" if rows else "]")
        for kind, rows in instructions.items()
    ]
    header = {
        "catalog": catalog,
        "sheet": sheet,
        "variant": variant,
        "target": list(TARGET),
        "hidden": sorted(hidden),
        "diagnostics": [{"code": d.code, "location": d.location} for d in diagnostics],
    }
    text = compact(header)[:-1] + ',"instructions":{\n' + ",\n".join(lines) + "\n}}\n"
    name = sheet if variant is None else f"{sheet}.{variant}"
    write(out / "catalogs" / catalog / f"{name}.json", text)


# ---------------------------------------------------------------- MOT


def generate_mot(folder: Path) -> None:
    from ax_devil.plugins.decoders.mot.decoder import decode_mot_frame, prepare_mot_frame_payloads

    info = configparser.ConfigParser()
    info.read(folder / "seqinfo.ini")
    width, height = int(info["Sequence"]["imwidth"]), int(info["Sequence"]["imheight"])
    for name in ("det", "gt"):
        rows = (folder / f"{name}.txt").read_text(encoding="utf-8").splitlines(keepends=True)
        payloads, warning = prepare_mot_frame_payloads(rows, width=width, height=height)
        scenes = [decode_mot_frame(payload) for payload in payloads]
        write(folder / f"{name}.scenes.json", scene_document(scenes))
        document = None if warning is None else warning.to_metadata()
        write(folder / f"{name}.warning.json", compact(document) + "\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json.loads(text)  # every file must be valid JSON
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
