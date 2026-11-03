"""Plausible detection data for example objects: track id formats and attribute values as decoders report them."""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from ax_devil.modules.scene.decoding.decoder_utils import rgb_from_map
from ax_devil.modules.scene.model import (
    ColorClassification,
    Score,
)

ID_FORMATS: tuple[Callable[[random.Random], str], ...] = (
    lambda rng: str(uuid.UUID(int=rng.getrandbits(128), version=4)),
    lambda rng: str(rng.randint(1, 99)),
    lambda rng: str(rng.randint(100, 99999)),
    lambda rng: f"track-{rng.randint(0, 999999):06d}",
    lambda rng: f"obj_{rng.randint(1, 400)}_cam{rng.randint(1, 4)}",
    lambda rng: f"{rng.getrandbits(16 * rng.choice((1, 2))):x}",
)
"""Track id formats seen from decoders: UUIDs, small and large counters, prefixed track names and short hex."""
ID_WEIGHTS = (5, 2, 2, 1, 1, 1)

_PALETTES: Mapping[str, tuple[tuple[str, int], ...]] = {
    "upper_clothing_colors": (
        ("black", 26),
        ("white", 14),
        ("gray", 14),
        ("blue", 14),
        ("red", 8),
        ("green", 6),
        ("beige", 5),
        ("brown", 4),
        ("yellow", 3),
        ("pink", 3),
        ("orange", 2),
        ("purple", 1),
    ),
    "lower_clothing_colors": (
        ("black", 30),
        ("blue", 28),
        ("gray", 16),
        ("beige", 9),
        ("brown", 7),
        ("white", 5),
        ("green", 3),
        ("red", 2),
    ),
    "vehicle_colors": (
        ("white", 25),
        ("black", 18),
        ("gray", 17),
        ("silver", 15),
        ("blue", 9),
        ("red", 8),
        ("brown", 3),
        ("green", 2),
        ("yellow", 2),
        ("orange", 1),
    ),
}
"""How often each color is reported for clothing and vehicles; other color attributes use any color equally."""
_ANY_COLOR = tuple((name, 1) for name in ("black", "white", "gray", "blue", "red", "green", "yellow", "brown"))
_TRUE_SHARE: Mapping[str, float] = {"carries_bag": 0.3, "occluded": 0.15, "keyframe": 0.1}
"""How often a flag is set; other flags are set three times in ten."""
_NUMBERS: Mapping[str, Callable[[random.Random], float]] = {
    # Faces are mostly either clearly visible or turned away.
    "face_visible": lambda rng: round(rng.betavariate(0.7, 0.7), 2),
    "z_order": lambda rng: float(rng.randint(0, 4)),
}
"""Plausible ranges of number attributes decoders report; others are a share between 0.05 and 0.95."""


def _share(rng: random.Random) -> float:
    return round(rng.uniform(0.05, 0.95), 2)


def _color_value(name: str, rng: random.Random) -> list[ColorClassification]:
    palette = _PALETTES.get(name, _ANY_COLOR)
    wanted = rng.choices((1, 2, 3), (55, 35, 10))[0]
    names: list[str] = []
    while len(names) < wanted:
        color = rng.choices([color for color, _weight in palette], [weight for _color, weight in palette])[0]
        if color not in names:
            names.append(color)
    score = rng.uniform(0.45, 0.95)
    colors = []
    for color in names:
        colors.append(ColorClassification(color, rgb_from_map(color), Score(round(score, 2))))
        score *= rng.uniform(0.3, 0.7)
    return colors


VALUES: Mapping[str, Callable[[str, random.Random], Any]] = {
    "bool": lambda name, rng: rng.random() < _TRUE_SHARE.get(name, 0.3),
    "number": lambda name, rng: _NUMBERS.get(name, _share)(rng),
    "color_classification_list": _color_value,
}
"""A plausible value for an attribute, by the shape a binding reads it as."""
