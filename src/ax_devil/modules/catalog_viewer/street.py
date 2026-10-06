"""What the camera of the **Street** sheet sees: a street running away from it, and the people and traffic on it.

Positions are normalized frame coordinates. Things standing higher up the frame are further away, so smaller; the
street meets the sky at ``HORIZON``. Each actor carries the detection data a decoder would report for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from ax_devil.modules.scene.decoding.decoder_utils import rgb_from_map
from ax_devil.modules.scene.model import ColorClassification, Score

FRAME_WIDTH, FRAME_HEIGHT = 1280, 720


HORIZON = 0.34
"""Where the street meets the sky in **Street** and **Crowd**: things standing higher up the frame are further away."""


def metre(ground: float) -> float:
    """Return the frame height of one metre for something standing at *ground*, the y where it meets the street."""
    return 0.32 * (ground - HORIZON)


def clipped(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Return *box* cut to the frame, as a detector reports an object partly outside it."""
    x, y, w, h = box
    left, top = max(0.0, x), max(0.0, y)
    return left, top, min(1.0, x + w) - left, min(1.0, y + h) - top


def reported_colors(*names: str, top: float = 0.82) -> list[ColorClassification]:
    """Return a color list as decoders report it: the most likely color first, each next one less likely."""
    scores = (round(top * 0.4**index, 2) for index in range(len(names)))
    return [ColorClassification(name, rgb_from_map(name), Score(score)) for name, score in zip(names, scores)]


@dataclass(frozen=True, slots=True)
class Actor:
    """One thing a camera sees on the street: its class, where it is, and how detection reports it."""

    name: str | None
    box: tuple[float, float, float, float]
    score: float
    velocity: tuple[float, float] | None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    parts: tuple[Actor, ...] = ()


def lane(bottom: float, ground: float) -> float:
    """Return where a line along the street that reaches the frame's bottom at x *bottom* is at *ground*."""
    return 0.5 + (bottom - 0.5) * (ground - HORIZON) / (1 - HORIZON)


def standing(
    x: float, ground: float, size: tuple[float, float], lift: float = 0.0
) -> tuple[float, float, float, float]:
    """Return the box of something *size* metres wide and tall, centred on *x*, *lift* metres above *ground*."""
    scale = metre(ground)
    w, h = size[0] * scale * FRAME_HEIGHT / FRAME_WIDTH, size[1] * scale
    return x - w / 2, ground - h - lift * scale, w, h


def _speed(ground: float, across: float, toward: float) -> tuple[float, float]:
    """Return the image velocity of moving *across* and *toward* the camera in metres per second at *ground*."""
    scale = metre(ground)
    return across * scale * FRAME_HEIGHT / FRAME_WIDTH, toward * scale * 0.3


def _person(
    x: float,
    ground: float,
    score: float,
    walk: tuple[float, float] | None,
    upper: tuple[str, ...],
    lower: tuple[str, ...],
    bag: bool = False,
    occluded: bool = False,
    height: float = 1.75,
) -> Actor:
    """Return a person and, when they are near enough to see one, their head, which faces the way they walk."""
    box = standing(x, ground, (height * 0.34, height))
    left, top, w, h = box
    attributes = {
        "upper_clothing_colors": reported_colors(*upper, top=round(0.4 + score * 0.5, 2)),
        "lower_clothing_colors": reported_colors(*lower, top=round(0.35 + score * 0.5, 2)),
        "carries_bag": bag,
        "occluded": occluded,
    }
    velocity = _speed(ground, *walk) if walk is not None else None
    facing = 0.5 if walk is None else 0.5 + 0.45 * walk[1] / max(abs(walk[0]) + abs(walk[1]), 0.1)
    head = Actor(
        "head",
        (left + w * 0.3, top + h * 0.01, w * 0.4, h * 0.13),
        round(score * 0.9, 2),
        velocity,
        {"face_visible": round(facing, 2), "occluded": False},
    )
    parts = (head,) if h > 0.09 else ()
    return Actor("human", box, score, velocity, attributes, parts)


def _vehicle(
    name: str,
    x: float,
    ground: float,
    size: tuple[float, float],
    score: float,
    drive: tuple[float, float] | None,
    *colors: str,
    occluded: bool = False,
) -> Actor:
    attributes = {"vehicle_colors": reported_colors(*colors, top=round(0.45 + score * 0.45, 2)), "occluded": occluded}
    velocity = _speed(ground, *drive) if drive is not None else None
    return Actor(name, standing(x, ground, size), score, velocity, attributes)


@cache
def street_actors() -> tuple[Actor, ...]:
    """Return what the **Street** camera sees, furthest first, so nearer things are painted over further ones."""
    return (
        # Far away, near the end of the street: a few pixels tall and detected with little confidence.
        _person(lane(0.02, 0.395), 0.395, 0.41, (0.0, -1.2), ("black",), ("blue",)),
        _person(lane(1.05, 0.4), 0.4, 0.55, (0.0, 1.1), ("white", "gray"), ("black",)),
        _person(lane(1.1, 0.403), 0.403, 0.36, (0.0, 1.1), ("red",), ("blue",), occluded=True),
        _vehicle("car", lane(0.3, 0.405), 0.405, (1.8, 1.5), 0.62, (0.0, 9.0), "white", "silver"),
        _vehicle("car", lane(0.7, 0.415), 0.415, (1.8, 1.45), 0.71, (0.0, -8.0), "red"),
        _vehicle("truck", lane(0.76, 0.45), 0.45, (2.5, 3.4), 0.66, (0.0, -6.0), "white"),
        # Leaves moving in the wind, reported without a class.
        Actor(None, standing(lane(1.02, 0.5), 0.5, (2.2, 2.0), lift=3.6), 0.5, (0.004, -0.002)),
        # The middle distance.
        _person(lane(0.4, 0.565), 0.565, 0.46, (1.0, 0.0), ("gray",), ("black",), occluded=True),
        _vehicle("car", lane(0.53, 0.56), 0.56, (1.85, 1.5), 0.88, (0.0, -7.0), "black"),
        _vehicle("bus", lane(0.28, 0.6), 0.6, (2.55, 3.2), 0.93, (0.0, 7.5), "yellow", "white"),
        _person(lane(1.0, 0.6), 0.6, 0.91, (-0.2, 1.3), ("blue",), ("black",), bag=True),
        _person(lane(1.07, 0.618), 0.618, 0.84, None, ("beige", "white"), ("blue",), height=1.65),
        _vehicle("bike", lane(0.8, 0.645), 0.645, (0.7, 1.75), 0.74, (-4.5, 0.5), "black", "silver"),
        _person(lane(0.0, 0.68), 0.68, 0.77, (0.0, -1.3), ("black",), ("black", "gray")),
        # Near the camera: large, on the crossing, cut by the frame's edges and in front of each other.
        _vehicle("car", lane(0.28, 0.8), 0.8, (2.0, 1.5), 0.9, (0.0, 5.0), "gray", "silver"),
        _person(0.47, 0.87, 0.96, (1.4, 0.0), ("white",), ("blue",)),
        _person(0.565, 0.885, 0.94, (-1.2, 0.2), ("red", "black"), ("black",), bag=True, height=1.68),
        _vehicle("car", lane(0.95, 0.93), 0.93, (2.9, 1.5), 0.97, None, "silver", "gray"),
        _person(0.07, 1.06, 0.89, (0.2, -1.0), ("green",), ("beige",)),
    )
