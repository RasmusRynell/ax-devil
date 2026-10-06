"""Frozen Scene values retain the existing dataclass pickle wire format."""

from __future__ import annotations

import pickle
from copy import deepcopy
from dataclasses import fields
from typing import Any

import pytest

from ax_devil.modules.scene.model import RGB, Attribute, BoundingBox, ColorClassification, NormalizedPoint, Score


@pytest.mark.parametrize(
    "value",
    [
        Score(0.73),
        NormalizedPoint(-0.1, 1.2, allow_outside=True),
        RGB(4, 80, 200),
        ColorClassification("blue", RGB(4, 80, 200), Score(0.73)),
        Attribute("colors", [ColorClassification("blue", RGB(4, 80, 200), Score(0.73))]),
        BoundingBox.from_xywh(0.1, 0.2, 0.3, 0.4),
    ],
)
def test_cached_value_restoration_preserves_dataclass_state(value: Any) -> None:
    state = [getattr(value, field.name) for field in fields(value)]
    restored = object.__new__(type(value))
    restored.__setstate__(state)
    assert restored == value
    assert pickle.loads(pickle.dumps(value)) == value
    assert deepcopy(value) == value
