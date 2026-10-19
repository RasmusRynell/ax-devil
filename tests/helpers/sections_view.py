"""A sections view and its toolbar, as the sections view tests drive them."""

from __future__ import annotations

from PySide6.QtWidgets import QLineEdit, QTreeWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.workspace.ui.browser_rows import BrowserSection, WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.sections_view import SectionsView


def new_view() -> SectionsView:
    return SectionsView("test", "Test", Icon.WORKSPACE)


def view_with(qtbot: QtBot, *rows: WorkspaceBrowserRow) -> SectionsView:
    view = new_view()
    qtbot.addWidget(view)
    set_rows(view, rows)
    return view


def set_rows(view: SectionsView, rows: tuple[WorkspaceBrowserRow, ...]) -> None:
    view.set_sections((WorkspaceBrowserSection(BrowserSection.VIDEOS, rows),))


def view_tree(view: SectionsView) -> QTreeWidget:
    tree: QTreeWidget | None = view.findChild(QTreeWidget)
    assert tree is not None
    return tree


def search_box(view: SectionsView) -> QLineEdit:
    search: QLineEdit | None = view.findChild(QLineEdit)
    assert search is not None
    return search


def toolbar_button(view: SectionsView, text: str) -> MenuButton | None:
    """Return the toolbar button *text* if the view shows it, else None."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == text]
    return button if button.isVisibleTo(view) else None


def filter_entries(view: SectionsView) -> dict[str, bool]:
    """Return what the Filter offers, each entry with whether its rows are shown; empty while it is absent."""
    [button] = [button for button in view.findChildren(MenuButton) if button.text() == "Filter"]
    menu = button.menu()
    assert menu is not None
    if not button.isVisibleTo(view):
        return {}
    return {action.text(): action.isChecked() for action in menu.actions() if not action.isSeparator()}
