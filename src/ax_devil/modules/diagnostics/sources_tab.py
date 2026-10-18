"""The Sources and caches tab: every source's latest observations and the video caches, as one tree."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ax_devil.modules.diagnostics.dashboard_snapshot import DashboardSnapshot
from ax_devil.modules.diagnostics.detail_widgets import selectable_label
from ax_devil.modules.diagnostics.render_metrics import ViewerSnapshot


class SourcesTab(QWidget):
    """A tree of source observations and video caches, optionally limited to the selected viewer's sources."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: dict[str, QTreeWidgetItem] = {}
        layout = QVBoxLayout(self)
        note = selectable_label(
            "Snapshot-wide source state · selected-paint context is in Inspect paint · cache capacity is reserved bytes"
        )
        note.setToolTip(
            "Application-wide state. Source values may belong to different frames; "
            "cache reservations exclude decoder and display memory."
        )
        layout.addWidget(note)
        self.scope = QCheckBox("Only sources for the selected viewer")
        self.scope.setToolTip(
            "Uses explicit source ownership. Shared video caches remain in the application-wide view."
        )
        layout.addWidget(self.scope)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Source / observation", "Value", "Observed"])
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setColumnWidth(0, 320)
        layout.addWidget(self.tree)

    def show_snapshot(self, snapshot: DashboardSnapshot, snapshot_at: float, viewer: ViewerSnapshot | None) -> None:
        """Show *snapshot*, taken at *snapshot_at*; with the scope box checked, only the sources *viewer* owns."""
        selected_sources = set(viewer.source_ids) if viewer else set()
        groups: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {}
        for source_id, source in snapshot.sources.items():
            if self.scope.isChecked() and source_id not in selected_sources:
                continue
            rows = {
                key: (
                    "—" if item.value is None else str(item.value),
                    f"{max(0.0, snapshot_at - item.observed_at):.1f} s ago",
                )
                for key, item in source.observations.items()
            }
            owners = [item.label for item in snapshot.viewers if source_id in item.source_ids]
            rows["Used by"] = (", ".join(owners) or "Application-wide", "")
            groups[source_id] = (source.label, rows)
        if not self.scope.isChecked():
            for cache_id, cache in snapshot.caches.items():
                ranges = cache["ranges"]
                summary = ", ".join(f"{start}–{end}" for start, end in ranges[:8]) or "empty"
                if len(ranges) > 8:
                    summary = f"{summary} · {len(ranges) - 8} more ranges in export"
                groups[f"cache:{cache_id}"] = (
                    f"Video cache · {cache['label']}",
                    {
                        "Frames": (str(cache["size"]), "Snapshot"),
                        "Reserved / budget (MiB)": (
                            f"{cache['reserved_bytes'] / 1024**2:.1f} / {cache['budget_bytes'] / 1024**2:g}",
                            "Snapshot",
                        ),
                        "Occupancy": (
                            f"{100 * cache['reserved_bytes'] / cache['budget_bytes']:.1f}%"
                            if cache["budget_bytes"]
                            else "—",
                            "Snapshot",
                        ),
                        "Frame ranges": (summary, "Snapshot"),
                    },
                )
        for name in list(self._items):
            if name not in groups:
                item = self._items.pop(name)
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        for name, (label, values) in sorted(groups.items(), key=lambda item: item[1][0].casefold()):
            root = self._items.get(name)
            if root is None:
                root = QTreeWidgetItem(self.tree)
                root.setToolTip(0, name)
                self._items[name] = root
            root.setText(0, label)
            observation_count = len(values) - int("Used by" in values)
            root.setText(1, f"{observation_count} observations" if observation_count else "Waiting for observations")
            rows_list = sorted(values.items())
            while root.childCount() > len(rows_list):
                root.takeChild(root.childCount() - 1)
            for index, (key, (value, age)) in enumerate(rows_list):
                existing = root.child(index)
                item = existing if existing is not None else QTreeWidgetItem(root)
                item.setText(0, key)
                item.setText(1, value)
                item.setToolTip(1, value)
                item.setText(2, age)
