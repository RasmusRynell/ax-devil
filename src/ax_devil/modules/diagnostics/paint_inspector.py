"""Qt presentation for the evidence behind a selected paint."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.diagnostics.paint_inspection import PaintInspection


def _text(value: str = "") -> QLabel:
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _duration(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.horizontalHeader().setMinimumSectionSize(70)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.verticalHeader().setDefaultSectionSize(27)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return table


def _fill(table: QTableWidget, rows: list[tuple[str, ...]]) -> None:
    table.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            table.setItem(row, column, item)
    scrollbar = table.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
    table.setFixedHeight(table.horizontalHeader().height() + table.verticalHeader().length() + scrollbar + 4)


class PaintInspector(QWidget):
    """Display a frozen paint comparison, historical context and investigation actions."""

    stepRequested = Signal(int)
    recordingRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inspection: PaintInspection | None = None
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.empty = _text("Click a point in the graph or Inspect largest to preserve and investigate a sample.")
        layout.addWidget(self.empty)
        self.detail = QWidget()
        content = QVBoxLayout(self.detail)
        content.setContentsMargins(0, 0, 0, 0)
        navigation = QHBoxLayout()
        self.previous = QPushButton("Previous paint")
        self.previous.clicked.connect(lambda: self.stepRequested.emit(-1))
        self.next = QPushButton("Next paint")
        self.next.clicked.connect(lambda: self.stepRequested.emit(1))
        self.copy = QPushButton("Copy report")
        self.copy.clicked.connect(self._copy_report)
        navigation.addWidget(self.previous)
        navigation.addWidget(self.next)
        navigation.addStretch()
        navigation.addWidget(self.copy)
        content.addLayout(navigation)
        self.identity = _text()
        content.addWidget(self.identity)
        self.findings = _text()
        content.addWidget(self.findings)
        self.baseline = _text()
        content.addWidget(self.baseline)
        self.timings = _table(["Measurement", "Selected ms", "Prior median ms", "Change ms", "Prior samples"])
        content.addWidget(self.timings)
        content.addWidget(
            _text("Build times overlap preparation or precede paint; do not add all rows. — means unmeasured.")
        )
        self.workload = _table(["Workload", "Selected paint", "Previous comparable paint"])
        content.addWidget(self.workload)
        self.source_note = _text()
        content.addWidget(self.source_note)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Captured source observation", "Value", "Relative to paint"])
        self.sources.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.sources.header().setStretchLastSection(True)
        self.sources.setMaximumHeight(200)
        content.addWidget(self.sources)
        record = QPushButton("Investigate with recording…")
        record.clicked.connect(self.recordingRequested.emit)
        content.addWidget(record)
        content.addWidget(
            _text(
                "For function-level evidence, start a recording and reproduce the problem. "
                "Recording cannot recover past stacks; native Qt/FFmpeg work and short calls may be absent."
            )
        )
        layout.addWidget(self.detail)
        self.detail.hide()

    def show_inspection(self, inspection: PaintInspection | None) -> None:
        """Replace the selected evidence without reading any current source values."""
        self._inspection = inspection
        self.empty.setVisible(inspection is None)
        self.detail.setVisible(inspection is not None)
        if inspection is None:
            return
        item = inspection.observation
        self.previous.setEnabled(inspection.index > 0)
        self.next.setEnabled(inspection.index + 1 < len(inspection.viewer.history))
        kind = "New frame" if item.new_frame else "Repaint"
        self.identity.setText(
            f"Paint {inspection.index + 1} / {len(inspection.viewer.history)} · {kind} · {item.sample.frame.label}"
        )
        self.findings.setText("\n\n".join(inspection.findings))
        self.baseline.setText(
            f"Baseline: {len(inspection.baseline)} preceding {kind.lower()} paints (up to 60). "
            "Each row excludes unmeasured work; workload compares the preceding paint of the same kind."
        )
        _fill(
            self.timings,
            [
                (
                    entry.stage.label,
                    _duration(entry.current_ms),
                    _duration(entry.baseline.median_ms if entry.baseline else None),
                    _duration(entry.delta_ms, signed=True),
                    str(entry.baseline.count) if entry.baseline else "0",
                )
                for entry in inspection.comparisons
            ],
        )
        _fill(self.workload, list(inspection.workload))
        self.sources.clear()
        self.sources.setVisible(bool(item.sources))
        if item.source_context_at is None:
            self.source_note.setText("No source observations were captured for this paint.")
        else:
            offset = (item.source_context_at - item.sample.completed_at) * 1000
            self.source_note.setText(
                f"Historical source context captured {offset:+.3f} ms from paint completion. "
                "Fields can belong to different frames; correlation is not a proven cause."
            )
        for source in item.sources.values():
            root = QTreeWidgetItem(self.sources, [source.label])
            root.setExpanded(True)
            for key, observation in sorted(source.observations.items()):
                age = (observation.observed_at - item.sample.completed_at) * 1000
                value = "—" if observation.value is None else str(observation.value)
                QTreeWidgetItem(root, [key, value, f"{age:+.3f} ms"])

    def _copy_report(self) -> None:
        inspection = self._inspection
        if inspection is None:
            return
        lines = [inspection.viewer.label, self.identity.text(), *inspection.findings, self.baseline.text()]
        for entry in inspection.comparisons:
            lines.append(
                f"{entry.stage.label}: {_duration(entry.current_ms)} ms; "
                f"prior median {_duration(entry.baseline.median_ms if entry.baseline else None)} ms; "
                f"change {_duration(entry.delta_ms, signed=True)} ms"
            )
        for label, current, previous in inspection.workload:
            lines.append(f"{label}: {current}; previous comparable: {previous}")
        lines.append(self.source_note.text())
        for source in inspection.observation.sources.values():
            for key, value in sorted(source.observations.items()):
                relative_ms = (value.observed_at - inspection.observation.sample.completed_at) * 1000
                lines.append(f"{source.label} / {key}: {value.value} ({relative_ms:+.3f} ms from paint)")
        QApplication.clipboard().setText("\n".join(lines))

    def cleanup(self) -> None:
        """Release the retained snapshot when the owning diagnostics window closes."""
        self._inspection = None
