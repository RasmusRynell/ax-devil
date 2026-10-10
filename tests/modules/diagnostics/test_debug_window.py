"""Qt interaction checks for rendering diagnostics."""

from pathlib import Path
from time import perf_counter

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.diagnostics.metrics_store import get_metrics_store, set_metrics_enabled
from ax_devil.modules.diagnostics.render_metrics import get_render_metrics_store
from tests.helpers.render_metrics import sample
from tests.helpers.widgets import button


def _cell(window: DebugWindow, row: int) -> str:
    item = window.timings_table.item(row, 1)
    assert item is not None
    return item.text()


def _source_roots(window: DebugWindow) -> dict[str, QTreeWidgetItem]:
    """Return the top-level source rows keyed by source id."""
    tree = window.source_tree
    roots = (tree.topLevelItem(index) for index in range(tree.topLevelItemCount()))
    return {root.toolTip(0): root for root in roots if root is not None}


def test_debug_window_selection_filter_refresh_and_reset(qtbot: QtBot) -> None:
    set_metrics_enabled(True)
    store = get_render_metrics_store()
    store.register("ui-a", "Camera A")
    store.register("ui-b", "Camera B")
    now = perf_counter()
    store.record("ui-a", sample(now), paint_started_at=now - 0.004)
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.show()
        window.filter_input.setText("Camera A")
        assert window.title_label.text() == "Camera A"
        assert "#1" in window.progress_labels["Painted"].text()
        assert _cell(window, 0) == "4.000"
        build_cell = window.build_table.item(0, 1)
        assert build_cell is not None and build_cell.text() == "—"
        window.filter_input.setText("Camera B")
        assert window.title_label.text() == "Camera B"
        assert _cell(window, 0) == "—"
        window.timer.timeout.emit()
        assert window.title_label.text() == "Camera B"
        window.filter_input.setText("missing")
        assert window.title_label.text() == "No matching viewers"
        window.filter_input.clear()
        button(window, "Reset history").click()
        assert _cell(window, 0) == "—"
        store.remove("ui-a")
        store.remove("ui-b")
        window.timer.timeout.emit()
        assert window.viewer_list.count() == len(store.snapshot())
        window.close()
        assert not window.timer.isActive()
    finally:
        store.remove("ui-a")
        store.remove("ui-b")


def test_sources_update_in_place_and_tabs_work_at_small_size(qtbot: QtBot) -> None:
    metrics = get_metrics_store()
    metrics.set_metric("test source", "Overlay offset (ms)", 4.0)
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.resize(720, 500)
        window.show()
        tabs = window.findChild(QTabWidget)
        assert tabs is not None
        tabs.setCurrentIndex(1)
        root = _source_roots(window)["test source"]
        root.setExpanded(False)
        metrics.set_metric("test source", "Overlay offset (ms)", None)
        window.timer.timeout.emit()
        assert _source_roots(window)["test source"] is root
        assert not root.isExpanded()
        child = root.child(0)
        assert child is not None and child.text(1) == "—"
        tabs.setCurrentIndex(2)
        assert window.trace_controls.isVisible()
        window.close()
    finally:
        metrics.remove_instance("test source")


def test_viewer_refresh_preserves_selection_and_relabels_sorted_items(qtbot: QtBot) -> None:
    store = get_render_metrics_store()
    store.register("polish-a", "A viewer")
    store.register("polish-b", "B viewer")
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.viewer_list.setCurrentItem(window.viewer_list.findItems("B viewer", Qt.MatchFlag.MatchExactly)[0])
        selected = window.viewer_list.currentItem()
        store.register("polish-b", "0 renamed viewer")
        window.timer.timeout.emit()
        assert window.viewer_list.currentItem() is selected
        assert window.title_label.text() == "0 renamed viewer"
        assert selected is not None and "0 renamed viewer" in selected.toolTip()
        assert window.viewer_list.row(selected) == 0
        store.remove("polish-b")
        window.timer.timeout.emit()
        assert window.title_label.text() == "A viewer"
        assert window.measurements.isHidden()
        assert "first paint" in window.empty_label.text()
        set_metrics_enabled(False)
        window.timer.timeout.emit()
        assert "Collect Debug Metrics" in window.empty_label.text()
        window.close()
    finally:
        store.remove("polish-a")
        store.remove("polish-b")


def test_narrow_timing_table_keeps_last_row_above_horizontal_scrollbar(qtbot: QtBot) -> None:
    store = get_render_metrics_store()
    store.register("narrow", "Narrow viewer")
    now = perf_counter()
    store.record("narrow", sample(now), paint_started_at=now - 0.004)
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.show()
        window.resize(640, 500)
        qtbot.waitUntil(lambda: window.timings_table.horizontalScrollBar().maximum() > 0)
        table = window.timings_table
        bottom = table.rowViewportPosition(table.rowCount() - 1) + table.rowHeight(table.rowCount() - 1)
        assert bottom <= table.viewport().height()
        assert table.verticalScrollBar().maximum() == 0
        window.close()
    finally:
        store.remove("narrow")


def test_freeze_workload_selection_disclosures_and_source_ownership(qtbot: QtBot) -> None:
    """Inspection stays stable while capture advances and sources follow the selected viewer."""
    from dataclasses import replace

    from PySide6.QtWidgets import QToolButton

    from ax_devil.modules.diagnostics.render_metrics import PaintSelection

    set_metrics_enabled(True)
    render = get_render_metrics_store()
    sources = get_metrics_store()
    render.register("inspect", "Inspection viewer")
    render.set_sources("inspect", ("owned",))
    sources.set_metric("owned", "Value", 1)
    sources.set_metric("unrelated", "Value", 2)
    now = perf_counter()
    render.record("inspect", sample(now - 0.050), paint_started_at=now - 0.054)
    render.record("inspect", replace(sample(now - 0.025), paint_ms=1), paint_started_at=now - 0.026)
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.show()
        window.filter_input.setText("Inspection viewer")
        assert _cell(window, 0) == "4.000"
        window.paint_selection.setCurrentText(PaintSelection.REPAINTS.value)
        assert _cell(window, 0) == "1.000"
        window.source_scope.setChecked(True)
        assert set(_source_roots(window)) == {"owned"}
        disclosure = next(button for button in window.findChildren(QToolButton) if button.text() == "Build costs")
        disclosure.click()
        assert window.build_table.isVisible()
        window.freeze_button.click()
        render.record("inspect", replace(sample(now), paint_ms=9), paint_started_at=now - 0.009)
        window.timer.timeout.emit()
        assert _cell(window, 0) == "1.000"
        window.chart_mode.setCurrentIndex(1)
        assert _cell(window, 0) == "1.000"
        window.freeze_button.click()
        assert _cell(window, 0) == "9.000"
        assert window.build_table.isVisible()
        window.close()
    finally:
        render.remove("inspect")
        sources.remove_instance("owned")
        sources.remove_instance("unrelated")


def test_graph_selection_freezes_exact_paint_and_exports_inspection(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Select a dense spike by its height, keep historical context and navigate retained neighbors."""
    import json
    from dataclasses import replace

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog

    set_metrics_enabled(True)
    render = get_render_metrics_store()
    sources = get_metrics_store()
    render.register("spike", "Spike camera")
    render.set_sources("spike", ("spike-source",))
    sources.set_metric("spike-source", "Frame queue", 12)
    now = perf_counter()
    for index, duration in enumerate((4.0, 40.0, 4.0)):
        item = replace(sample(now - 0.1 + index / 1000, index), paint_ms=duration, compose_ms=duration - 3)
        render.record("spike", item, paint_started_at=item.completed_at - duration / 1000)
    try:
        window = DebugWindow()
        qtbot.addWidget(window)
        window.show()
        window.resize(1100, 900)
        window.filter_input.setText("Spike camera")
        window.chart_mode.setCurrentIndex(window.chart_mode.findData("paint"))
        qtbot.waitUntil(lambda: window.chart.width() > 100)
        point = window.chart._position(1, 40)
        QTest.mouseClick(window.chart, Qt.MouseButton.LeftButton, pos=point.toPoint())
        assert window.freeze_button.isChecked()
        assert "Paint 2 / 3" in window.inspector.identity.text()
        assert "Prepare overlays" in window.inspector.findings.text()
        qtbot.waitUntil(lambda: window.inspector.workload.y() > window.inspector.timings.geometry().bottom())
        sources.set_metric("spike-source", "Frame queue", 0)
        render.record("spike", sample(now + 100, 30), paint_started_at=now + 99.996)
        window.timer.timeout.emit()
        assert "Paint 2 / 3" in window.inspector.identity.text()
        source_root = window.inspector.sources.topLevelItem(0)
        assert source_root is not None
        source_row = source_root.child(0)
        assert source_row is not None and source_row.text(1) == "12"
        window.inspector.copy.click()
        assert "Prepare overlays" in QApplication.clipboard().text()
        export = tmp_path / "diagnostics.json"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(export), ""))
        button(window, "Export…").click()
        serialized = json.loads(export.read_text(encoding="utf-8"))
        assert serialized["inspection"] == {"viewer_id": "spike", "sample_index": 1, "metric_key": "paint"}
        viewer = next(item for item in serialized["viewers"] if item["viewer_id"] == "spike")
        assert viewer["history"][1]["sources"]["spike-source"]["observations"]["Frame queue"]["value"] == 12
        window.inspector.previous.click()
        assert "Paint 1 / 3" in window.inspector.identity.text()
        assert not window.inspector.previous.isEnabled()
        window.inspect_largest.click()
        assert "Paint 2 / 3" in window.inspector.identity.text()
        QTest.keyClick(window.chart, Qt.Key.Key_Right)
        assert "Paint 3 / 3" in window.inspector.identity.text()
        window.freeze_button.click()
        assert window.detail_tabs.currentIndex() == 0
        window.close()
    finally:
        render.remove("spike")
        sources.remove_instance("spike-source")


def test_chart_arrows_skip_unmeasured_paints_without_jumping_to_start(qtbot: QtBot) -> None:
    """Changing metrics preserves chronological navigation through measured points."""
    from PySide6.QtTest import QTest

    from ax_devil.modules.diagnostics.history_chart import PaintHistoryChart
    from ax_devil.modules.diagnostics.render_metrics import PaintObservation

    chart = PaintHistoryChart()
    qtbot.addWidget(chart)
    history = tuple(
        PaintObservation(sample(float(index)), None, index % 2 == 0, 40.0 if index % 2 == 0 else None)
        for index in range(5)
    )
    selected: list[int] = []
    chart.sampleSelected.connect(selected.append)
    chart.set_history(history, 5.0, "interval", selected_index=3)
    QTest.keyClick(chart, Qt.Key.Key_Right)
    QTest.keyClick(chart, Qt.Key.Key_Left)
    assert selected == [4, 2]
