"""Trees and playlist rows the section tree tests share."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    OfferedAction,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.section_tree import SectionTree
from ax_devil.modules.workspace.ui.source_row import SourceRow


def show_tree(qtbot: QtBot, *rows: WorkspaceBrowserRow, kind: BrowserSection = BrowserSection.PLAYLISTS) -> SectionTree:
    tree = SectionTree()
    qtbot.addWidget(tree)
    tree.resize(360, 600)
    tree.set_sections((WorkspaceBrowserSection(kind, rows),))
    tree.show()
    qtbot.waitExposed(tree)
    return tree


def item_at(tree: SectionTree, *path: int) -> QTreeWidgetItem:
    """Return the row item at *path* of child indexes below the first section."""
    item = tree.topLevelItem(0)
    for index in path:
        assert item is not None
        item = item.child(index)
    assert item is not None
    return item


def row_widget(tree: SectionTree, item: QTreeWidgetItem) -> SourceRow:
    widget = tree.itemWidget(item, 0)
    assert isinstance(widget, SourceRow)
    return widget


def playlist_row(entries: int, row_id: str = "playlist", *, expanded_at_first: bool = False) -> WorkspaceBrowserRow:
    lane = WorkspaceBrowserRow(row_id=f"{row_id}/lane", label="tracks", icon_kind="overlay")
    return WorkspaceBrowserRow(
        row_id=row_id,
        label="Night shift",
        icon_kind="playlist",
        actions=(OfferedAction(RowAction.ADD),),
        expanded_at_first=expanded_at_first,
        children=tuple(
            WorkspaceBrowserRow(
                row_id=f"{row_id}/entry/{index}",
                label=f"run_{index:03d}",
                icon_kind="video",
                children=(replace(lane, row_id=f"{row_id}/entry/{index}/lane"),),
            )
            for index in range(entries)
        ),
    )
