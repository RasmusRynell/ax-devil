"""Result records: statistics, merging repeated runs, JSON lines and text tables."""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

SCHEMA = 1
Record = dict[str, Any]


def environment(window: Any = None) -> dict[str, str | None]:
    """Describe the Qt build, platform, graphics API and renderer of the running process (*window*: a QQuickWindow)."""
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication

    api = renderer = None
    if window is not None:
        api = str(window.rendererInterface().graphicsApi())
        rhi = window.rhi()
        info = rhi.driverInfo() if rhi is not None else None  # Keep it: its name is freed with it.
        renderer = bytes(info.deviceName.data()).decode(errors="replace") if info is not None else None
    return {
        "qt": qVersion(),
        "platform": QGuiApplication.platformName(),
        "graphics_api": api,
        "gl_renderer": renderer,
        "display": os.environ.get("DISPLAY"),
    }


def stats(samples: Sequence[Mapping[str, float]]) -> tuple[dict[str, float], dict[str, float]]:
    """Return the median and 90th percentile of every key across *samples*."""
    medians: dict[str, float] = {}
    p90s: dict[str, float] = {}
    for key in samples[0]:
        values = sorted(sample[key] for sample in samples)
        medians[key] = round(median(values), 3)
        p90s[key] = round(values[max(0, math.ceil(len(values) * 0.9) - 1)], 3)
    return medians, p90s


def merge(runs: Sequence[Record]) -> Record:
    """Combine repeated runs of one measurement: the median of their medians, null where a run lacked a stage."""
    merged = dict(runs[0])
    for field in ("median_ms", "p90_ms"):
        merged[field] = {key: _middle([run[field].get(key) for run in runs]) for key in runs[0][field]}
    for field in ("lane_fps", "process_cpu_ms"):
        merged[field] = _middle([run.get(field) for run in runs])
    merged["rounds"] = sum(run["rounds"] for run in runs)
    return merged


def _middle(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(median(present), 3) if present else None


def emit(text: str = "") -> None:
    """Write a line of results to standard output."""
    sys.stdout.write(f"{text}\n")


def append_json(path: str, record: Record) -> None:
    """Append *record* as one line to the JSONL file *path*."""
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _cell(value: float | None) -> str:
    return f"{'-':>10}" if value is None else f"{value:10.2f}"


def _rows(record: Record) -> dict[str, float | None]:
    rows = dict(record["median_ms"])
    for key in ("process_cpu_ms", "lane_fps"):
        if record.get(key) is not None:
            rows[key] = record[key]
    return rows


def table(records: Mapping[str, Record]) -> str:
    """Return a table of stages with one column per record; with two records also the delta and delta percent."""
    columns = {label: _rows(record) for label, record in records.items()}
    labels = list(columns)
    stages = list(dict.fromkeys(stage for rows in columns.values() for stage in rows))
    header = f"{'stage':20}" + "".join(f"{label:>10}" for label in labels)
    if len(labels) == 2:
        header += f"{'delta':>10}{'delta%':>10}"
    lines = [header]
    for stage in stages:
        values = [columns[label].get(stage) for label in labels]
        line = f"{stage:20}" + "".join(_cell(value) for value in values)
        if len(values) == 2:
            first, second = values
            both = first is not None and second is not None
            line += _cell(second - first if both else None)  # type: ignore[operator]
            line += _cell(100 * (second - first) / first if both and first else None)  # type: ignore[operator]
        lines.append(line)
    return "\n".join(lines)


def describe(record: Record) -> str:
    """Return the one-line summary of what a record measured and on which platform."""
    env = record["env"]
    dirty = "+dirty" if record["dirty"] else ""
    return f"{record['ref']}@{record['commit']}{dirty} on {env['platform']} {env['graphics_api']} {env['gl_renderer']}"
