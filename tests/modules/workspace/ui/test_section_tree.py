"""The section tree shows browser sections with the shared source row, and reports what the user does with rows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.workspace.core import ConsiderationItemRef, FileVideoSourceSpec, SeekableVideoContent
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    RowAction,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.section_tree import SectionTree
from ax_devil.modules.workspace.ui.source_row import SourceRow


def _tree(qtbot: QtBot, *rows: WorkspaceBrowserRow, kind: BrowserSection = BrowserSection.PLAYLISTS) -> SectionTree:
    tree = SectionTree()
    qtbot.addWidget(tree)
    tree.resize(360, 600)
    tree.set_sections((WorkspaceBrowserSection(kind, rows),))
    tree.show()
    qtbot.waitExposed(tree)
    return tree


def _item(tree: SectionTree, *path: int) -> QTreeWidgetItem:
    """Return the row item at *path* of child indexes below the first section."""
    item = tree.topLevelItem(0)
    for index in path:
        assert item is not None
        item = item.child(index)
    assert item is not None
    return item


def _widget(tree: SectionTree, item: QTreeWidgetItem) -> SourceRow:
    widget = tree.itemWidget(item, 0)
    assert isinstance(widget, SourceRow)
    return widget


def _playlist(entries: int, row_id: str = "playlist") -> WorkspaceBrowserRow:
    lane = WorkspaceBrowserRow(row_id=f"{row_id}/lane", label="tracks", icon_kind="overlay")
    return WorkspaceBrowserRow(
        row_id=row_id,
        label="Night shift",
        icon_kind="playlist",
        detail=f"{entries} entries",
        action=RowAction.RESCAN,
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


def test_entries_get_row_widgets_only_once_their_playlist_is_expanded(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(300))
    playlist = _item(tree, 0)
    entries = [_item(tree, 0, index) for index in range(playlist.childCount())]

    assert _widget(tree, playlist).row.label == "Night shift"
    assert all(tree.itemWidget(entry, 0) is None for entry in entries)

    playlist.setExpanded(True)

    assert [_widget(tree, entry).row.label for entry in entries[:2]] == ["run_000", "run_001"]
    assert all(tree.itemWidget(entry, 0) is not None for entry in entries)
    assert tree.itemWidget(_item(tree, 0, 0, 0), 0) is None, "lanes wait for their own entry"


def test_search_finds_rows_whose_widgets_are_not_built_yet(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(300))

    tree.set_filter(lambda _row: True, lambda row: row.label == "run_250")

    assert _item(tree, 0).isExpanded()
    assert not _item(tree, 0, 250).isHidden()
    assert _item(tree, 0, 249).isHidden()
    assert _widget(tree, _item(tree, 0, 250)).row.label == "run_250"


def test_expanded_rows_stay_expanded_with_their_rows_across_updates(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(3, "first"), _playlist(3, "second"))
    _item(tree, 1).setExpanded(True)

    reordered = (_playlist(4, "second"), _playlist(3, "first"))
    tree.set_sections((WorkspaceBrowserSection(BrowserSection.PLAYLISTS, reordered),))

    assert _item(tree, 0).isExpanded() and not _item(tree, 1).isExpanded()
    assert [_widget(tree, _item(tree, 0, index)).row.label for index in range(4)] == [
        "run_000",
        "run_001",
        "run_002",
        "run_003",
    ]


def test_the_list_keeps_its_scroll_position_when_the_rows_change(qtbot: QtBot) -> None:
    """Opening an entry far down the list refreshes the rows; the list must not jump back to the top."""
    playlists = tuple(_playlist(1, f"playlist-{index}") for index in range(40))
    tree = _tree(qtbot, *playlists)
    tree.resize(300, 300)
    scroll_bar = tree.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    position = scroll_bar.maximum() // 2
    scroll_bar.setValue(position)

    tree.set_sections((WorkspaceBrowserSection(BrowserSection.PLAYLISTS, playlists),))
    qtbot.wait(10)

    assert scroll_bar.value() == position


def test_a_section_caption_heads_its_rows_and_never_collapses(qtbot: QtBot) -> None:
    tree = _tree(qtbot, _playlist(1))
    section = _item(tree)

    section.setExpanded(False)

    assert section.isExpanded()
    assert tree.itemWidget(section, 0) is not None
    assert not isinstance(tree.itemWidget(section, 0), SourceRow)


def test_a_double_click_on_a_row_reports_it_to_the_side_with_ctrl(qtbot: QtBot) -> None:
    video = SeekableVideoContent(display_name="gate.mp4", source_spec=FileVideoSourceSpec(path=Path("/tmp/gate.mp4")))
    row = WorkspaceBrowserRow(row_id="gate", label="gate.mp4", icon_kind="video", activation_target=(video, 0))
    tree = _tree(qtbot, row, kind=BrowserSection.VIDEOS)
    widget = _widget(tree, _item(tree, 0))
    activations: list[tuple[WorkspaceBrowserRow, bool]] = []
    tree.row_activated.connect(lambda activated, to_side: activations.append((activated, to_side)))

    for modifier in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier):
        position = QPoint(widget.width() // 3, widget.height() // 2)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)
        QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, modifier, pos=position)

    assert activations == [(row, False), (row, True)]


def test_a_rows_action_and_eye_report_the_row_and_its_consideration_item(qtbot: QtBot) -> None:
    ref = ConsiderationItemRef.playlist_entry("playlist", 0)
    playlist = replace(
        _playlist(1),
        children=(WorkspaceBrowserRow(row_id="entry", label="run", icon_kind="video", consideration_ref=ref),),
    )
    tree = _tree(qtbot, playlist)
    _item(tree, 0).setExpanded(True)
    actions: list[WorkspaceBrowserRow] = []
    changes: list[tuple[ConsiderationItemRef, bool]] = []
    tree.row_action_requested.connect(actions.append)
    tree.row_consideration_changed.connect(lambda changed, considered: changes.append((changed, considered)))

    [rescan] = _widget(tree, _item(tree, 0)).findChildren(SmallButton)
    rescan.click()
    toggle = _widget(tree, _item(tree, 0, 0)).findChild(QToolButton)
    assert toggle is not None
    toggle.click()

    assert actions == [playlist]
    assert changes == [(ref, False)]


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("text_size", [13, 21])
def test_two_line_rows_keep_both_lines_and_their_action_inside_a_narrow_tree(qtbot: QtBot, text_size: int) -> None:
    apply_text_size(text_size)
    camera = WorkspaceBrowserRow(
        row_id="camera",
        label="Loading dock entrance, north side",
        icon_kind="live_video",
        detail="192.0.2.10 · head 2 (1)",
        action=RowAction.DISCONNECT,
        is_open=True,
    )
    tree = _tree(qtbot, camera, replace(camera, row_id="other", action=RowAction.CONNECT), kind=BrowserSection.LIVE)
    tree.resize(220, 400)
    qtbot.wait(10)

    for index in range(2):
        item = _item(tree, index)
        widget = _widget(tree, item)
        assert tree.visualItemRect(item).height() >= widget.sizeHint().height()
        name, detail = widget.findChildren(ElidedLabel)
        [button] = widget.findChildren(SmallButton)
        assert all(label.height() >= label.sizeHint().height() for label in (name, detail))
        assert detail.geometry().top() >= name.geometry().bottom(), "the detail is the second line"
        assert widget.rect().contains(button.geometry()), "the action stays inside the row"
        assert button.width() >= button.sizeHint().width()
