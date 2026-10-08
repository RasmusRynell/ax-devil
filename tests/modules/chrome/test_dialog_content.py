"""Regression checks for readable content in floating forms and inspection views."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QScrollArea
from pytestqt.qtbot import QtBot

from ax_devil.modules.application_shell.settings_dialog import SettingsDialog
from ax_devil.modules.catalog_viewer.window import NewCatalogDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog
from ax_devil.modules.workspace.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.item_info_dialog import WorkspaceItemInfoDialog


@pytest.mark.parametrize("point_size", [9, 18])
def test_shortcut_list_uses_window_space_and_preserves_search(qtbot: QtBot, point_size: int) -> None:
    """The list fills its viewport while search and actions remain reachable."""
    manager = ShortcutManager()
    manager.register_defaults()
    dialog = ShortcutsDialog(manager)
    qtbot.addWidget(dialog)
    font = dialog.font()
    font.setPointSize(point_size)
    dialog.setFont(font)
    dialog.show()
    QApplication.processEvents()
    areas = dialog.findChildren(QScrollArea)
    assert areas
    scroll = min(areas, key=lambda area: area.viewport().height())
    assert scroll.viewport().height() > dialog.height() // 2
    previous_height = scroll.viewport().height()
    dialog.resize(dialog.width(), dialog.height() + 100)
    QApplication.processEvents()
    assert scroll.viewport().height() > previous_height
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    assert dialog.rect().contains(dialog._search.mapTo(dialog, dialog._search.rect().bottomRight()))
    size = dialog.size()
    dialog._search.setText("Play / Pause")
    QApplication.processEvents()
    assert dialog.size() == size
    assert any(row.isVisible() for row in dialog._all_rows)


@pytest.mark.parametrize("point_size", [9, 18])
@pytest.mark.parametrize("custom_frame", [False, True])
def test_catalog_name_form_fits_font_and_wrapped_source(qtbot: QtBot, point_size: int, custom_frame: bool) -> None:
    """A simple catalog form and a wrapped source note avoid unnecessary scrolling."""
    parent = ChromeWindow(use_custom_frame=custom_frame)
    qtbot.addWidget(parent)
    for source in ("Source", "Very long source name " * 8):
        dialog = NewCatalogDialog(source, parent)
        qtbot.addWidget(dialog)
        font = dialog.font()
        font.setPointSize(point_size)
        dialog.setFont(font)
        dialog.show()
        QApplication.processEvents()
        assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
        assert dialog._scroll_area.verticalScrollBar().maximum() == 0
        screen = dialog.screen()
        assert screen is not None
        assert screen.availableGeometry().contains(dialog.frameGeometry())
        dialog.close()


def test_oversized_catalog_note_scrolls_with_actions_visible(qtbot: QtBot) -> None:
    """A source note exceeding the screen stays readable by scrolling without hiding Create and Cancel."""
    dialog = NewCatalogDialog("Long catalog source name " * 200)
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    screen = dialog.screen()
    assert screen is not None
    assert screen.availableGeometry().contains(dialog.frameGeometry())
    assert dialog._scroll_area.verticalScrollBar().maximum() > 0
    for index in range(dialog._button_layout.count()):
        item = dialog._button_layout.itemAt(index)
        assert item is not None
        button = item.widget()
        if button is not None:
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))


def test_information_tree_sizes_to_rows_and_scrolls_large_payload(qtbot: QtBot) -> None:
    """Small payloads fit fully; large payloads get a larger bounded, scrollable viewport."""
    heights: list[int] = []
    for count in (1, 12, 100):
        dialog = WorkspaceItemInfoDialog(
            WorkspaceItemInfo("Information", tuple((f"Field {index}", f"Value {index}") for index in range(count)))
        )
        qtbot.addWidget(dialog)
        dialog.show()
        QApplication.processEvents()
        heights.append(dialog.height())
        assert (dialog._tree.verticalScrollBar().maximum() > 0) is (count == 100)
        screen = dialog.screen()
        assert screen is not None
        assert screen.availableGeometry().contains(dialog.frameGeometry())
        dialog.close()
    assert heights[0] < heights[1] < heights[2]


@pytest.mark.parametrize("value", ["Long descriptive metadata field " * 30, f"/videos/{'recordings/' * 100}video.mp4"])
def test_information_wrapped_value_remains_reachable_after_resize(qtbot: QtBot, value: str) -> None:
    """Long metadata and paths wrap fully and can scroll even when one row exceeds the viewport."""
    dialog = WorkspaceItemInfoDialog(WorkspaceItemInfo("Video", (("path", value),)))
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    label = next(label for label in dialog.findChildren(QLabel) if label.text() == value)
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    opening_size = dialog.size()
    for width, height in ((opening_size.width(), opening_size.height()), (320, 260), (700, 500)):
        dialog.resize(width, height)
        QApplication.processEvents()
        qtbot.waitUntil(lambda: label.height() >= label.heightForWidth(label.width()))
        tree = dialog._tree
        bar = tree.verticalScrollBar()
        if label.height() > tree.viewport().height():
            assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        QApplication.processEvents()
        assert tree.viewport().rect().contains(label.mapTo(tree.viewport(), label.rect().bottomRight()))


def test_settings_pages_scroll_without_moving_actions(qtbot: QtBot) -> None:
    """Every settings page remains usable at a small size, with one scroll owner per page."""
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.resize(420, 300)
    for index in range(dialog._tabs.count()):
        dialog._tabs.setCurrentIndex(index)
        QApplication.processEvents()
        page = dialog._tabs.currentWidget()
        assert page is not None
        for button_index in range(dialog._button_layout.count()):
            item = dialog._button_layout.itemAt(button_index)
            assert item is not None
            button = item.widget()
            if button is not None:
                assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    dialog._tabs.setCurrentIndex(1)
    QApplication.processEvents()
    stream_page = next(page for page in dialog.findChildren(ContentScrollArea) if dialog._tabs.indexOf(page) == 1)
    assert stream_page.verticalScrollBar().maximum() > 0


def test_narrow_settings_page_scrolls_instead_of_overlapping_rows(qtbot: QtBot) -> None:
    """When wrapped descriptions need more height than the page has, the page scrolls and no row is cut off."""
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.resize(380, 400)
    QApplication.processEvents()
    page = dialog._tabs.currentWidget()
    assert page is not None
    for group in page.findChildren(QGroupBox):
        for label in group.findChildren(QLabel):
            if label.isVisible():
                assert group.rect().contains(label.mapTo(group, label.rect().bottomRight())), label.text()
                assert label.height() >= label.heightForWidth(label.width()), label.text()
