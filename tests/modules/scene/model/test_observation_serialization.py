"""Observation serialization preserves decoder diagnostics."""

from __future__ import annotations

import pickle
from datetime import datetime, timezone

from ax_devil.modules.scene.model import (
    BoundingBox,
    Observation,
)

_TS = datetime(2024, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_observation_pickle_preserves_debug() -> None:
    """Native pickle restoration preserves current observation fields."""
    observation = Observation(
        geometry=BoundingBox.from_xywh(0.1, 0.1, 0.2, 0.2),
        timestamp=_TS,
        debug={"motion_debug": {"normalized_speed": 0.42}},
    )

    assert pickle.loads(pickle.dumps(observation)) == observation
