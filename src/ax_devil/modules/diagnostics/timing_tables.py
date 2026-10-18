"""Tables of timing distributions for the rendering diagnostics, one row per measured stage."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHeaderView, QTableWidget, QTableWidgetItem

from ax_devil.modules.diagnostics.paint_inspector import show_all_rows
from ax_devil.modules.diagnostics.render_metrics import PAINT_STAGES, PaintSelection, TimingStage, ViewerSnapshot


def format_ms(value: float | None) -> str:
    """Return *value* in milliseconds, "—" when there is none."""
    if value is None:
        return "—"
    return "<0.001" if 0.0 < value < 0.001 else f"{value:.3f}"


def timing_table(stages: tuple[TimingStage, ...]) -> QTableWidget:
    """Return an empty table with a row per stage in *stages*."""
    table = QTableWidget(len(stages), 6)
    table.setHorizontalHeaderLabels(["Measurement", "Last op ms", "Median ms", "p95 ms", "Max ms", "Samples"])
    table.setToolTip("— = no measurement. Distributions use actual operations in the recent window.")
    table.verticalHeader().hide()
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.horizontalHeader().setMinimumSectionSize(68)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for row, stage in enumerate(stages):
        for column in range(6):
            title = f"    {stage.label}" if stage in PAINT_STAGES[1:] else stage.label
            item = QTableWidgetItem(title if column == 0 else "—")
            item.setToolTip(stage.description)
            if column:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if stage.key == "paint":
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            table.setItem(row, column, item)
    show_all_rows(table)
    return table


def update_timing_table(
    table: QTableWidget, stages: tuple[TimingStage, ...], viewer: ViewerSnapshot | None, selection: PaintSelection
) -> None:
    """Show *viewer*'s distributions for *selection* in *table*, which was made for *stages*."""
    summaries = viewer.summaries(selection) if viewer else {}
    for row, stage in enumerate(stages):
        summary = summaries.get(stage.key)
        values = (
            summary.last_ms if summary else None,
            summary.median_ms if summary else None,
            summary.p95_ms if summary else None,
            summary.maximum_ms if summary else None,
        )
        for column, value in enumerate(values, start=1):
            cell = table.item(row, column)
            assert cell is not None
            cell.setText(format_ms(value))
        count = table.item(row, 5)
        assert count is not None
        count.setText(str(summary.count) if summary else "0")
