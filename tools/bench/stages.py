"""Instrumentation table: which functions are timed as which stage.

An entry is patched only if it exists in the code being measured, so older refs report that stage as null. Timing is
exclusive: a timed function nested inside another one is subtracted from the outer stage.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from time import perf_counter
from typing import Any

_PREPARATION = "ax_devil.modules.video_player.engine.quick.preparation"
TABLE: tuple[tuple[str, str, str], ...] = (
    (_PREPARATION, "label_quads", "label_layout"),
    (_PREPARATION, "DrawingBuffer._overlay_layers", "label_place"),  # Refs before the single quad stream.
    (_PREPARATION, "DrawingBuffer._text_and_labels", "label_place"),
    (_PREPARATION, "DrawingBuffer._flush", "flush"),
    (_PREPARATION, "DrawingBuffer.finish", "finish"),
)
STAGES = tuple(dict.fromkeys(stage for _module, _name, stage in TABLE))
_nested: list[float] = []  # Time spent in timed callees, one slot per open timed call.


def _timed(function: Callable[..., Any], totals: dict[str, float], stage: str) -> Callable[..., Any]:
    def timed(*args: Any, **kwargs: Any) -> Any:
        _nested.append(0.0)
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed = perf_counter() - started
            inner = _nested.pop()
            if _nested:
                _nested[-1] += elapsed
            totals[stage] += (elapsed - inner) * 1000

    return timed


def install(totals: dict[str, float]) -> None:
    """Patch every table entry that exists; each one adds its stage to *totals* (milliseconds, starting at 0)."""
    for module_name, name, stage in TABLE:
        owner: Any = importlib.import_module(module_name)
        *path, attribute = name.split(".")
        for part in path:
            owner = getattr(owner, part, None)
        if owner is None or not hasattr(owner, attribute):
            continue
        totals.setdefault(stage, 0.0)
        setattr(owner, attribute, _timed(getattr(owner, attribute), totals, stage))
