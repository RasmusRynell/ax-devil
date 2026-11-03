"""Cyclic garbage collection runs on the GUI thread, oldest overdue generation first."""

from __future__ import annotations

import gc

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from ax_devil.app import _collect_due_generation, _collect_garbage_on_gui_thread
from ax_devil.modules.settings.logging_config import get_logger


def test_startup_heap_is_frozen_and_automatic_collection_disabled(qtbot: QtBot) -> None:
    parent = QObject()
    # The suite already runs without automatic collection and with a frozen heap; start from the app's state at launch.
    gc.unfreeze()
    gc.enable()
    try:
        _collect_garbage_on_gui_thread(parent, get_logger(__name__))
        assert gc.get_freeze_count() > 0
        assert not gc.isenabled()
    finally:
        gc.disable()


@pytest.mark.parametrize(
    ("counts", "expected"),
    [((5, 0, 0), None), ((701, 0, 0), 0), ((701, 11, 0), 1), ((701, 11, 11), 2)],
)
def test_collects_oldest_overdue_generation(
    monkeypatch: pytest.MonkeyPatch, counts: tuple[int, int, int], expected: int | None
) -> None:
    collected: list[int] = []
    monkeypatch.setattr(gc, "get_count", lambda: counts)
    monkeypatch.setattr(gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(gc, "collect", collected.append)

    _collect_due_generation()

    assert collected == ([] if expected is None else [expected])
