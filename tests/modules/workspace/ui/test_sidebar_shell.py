"""Live, Offline and Workspace share one sidebar layout: Search, Sort and Filter over the tree, then the footer."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QSplitter, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.chrome.theme import apply_text_size
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.core import RecentWorkspaces
from ax_devil.modules.workspace.ui.browser_rows import (
    BrowserSection,
    Facet,
    WorkspaceBrowserRow,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from tests.helpers.clipping import clipped_text_widgets
from tests.helpers.workspace import FakePrompts, FakeResolutionContext

_FIRST_FOOTER_BUTTON = {"live": "Add manually", "offline": None, "workspace": None}


def _row(name: str, kind: str) -> WorkspaceBrowserRow:
    return WorkspaceBrowserRow(name, name, "video", facets=frozenset({Facet("Kind", kind)}))


@pytest.fixture
def session(qtbot: QtBot, tmp_path: Path, render_catalog_manager: SceneRenderCatalogManager) -> WorkspaceSession:
    session = WorkspaceSession(
        render_catalog_manager=render_catalog_manager,
        resolver=ItemResolver(FakeResolutionContext(), in_background=False),
        recent_workspaces=RecentWorkspaces(lambda: tmp_path / "recent-workspaces.json"),
        prompts=FakePrompts(),
    )
    qtbot.addWidget(session.widget())
    session.widget().resize(1000, 900)
    session.widget().show()
    return session


def _show(session: WorkspaceSession, view_id: str) -> SectionsView:
    """Show the sidebar view *view_id* listing two rows of different kinds, so every toolbar button is offered."""
    session.show_sidebar_view(view_id)
    [view] = [view for view in session.widget().findChildren(SectionsView) if view.sidebar_view().view_id == view_id]
    view.set_sections((WorkspaceBrowserSection(BrowserSection.VIDEOS, (_row("first", "Box"), _row("second", "Dome"))),))
    QApplication.processEvents()
    return view


def _x(widget: QWidget, view: QWidget) -> int:
    return widget.mapTo(view, QPoint(0, 0)).x()


def _y(widget: QWidget, view: QWidget) -> int:
    return widget.mapTo(view, QPoint(0, 0)).y()


def _footer_buttons(view: SectionsView) -> list[QPushButton]:
    """Return the shown buttons outside the tree, from top to bottom."""
    buttons = [
        button
        for button in view.findChildren(QPushButton)
        if button.isVisibleTo(view) and not view.tree.isAncestorOf(button)
    ]
    return sorted(buttons, key=lambda button: _y(button, view))


@pytest.mark.parametrize("view_id", ["workspace", "live", "offline"])
def test_every_view_has_search_sort_and_filter_in_that_order_over_the_tree(
    session: WorkspaceSession, view_id: str
) -> None:
    view = _show(session, view_id)

    [search] = [edit for edit in view.findChildren(QLineEdit) if edit.placeholderText().startswith("Search")]
    menus = {button.text(): button for button in view.findChildren(MenuButton)}
    sort, filter_ = menus["Sort"], menus["Filter"]

    assert all(widget.isVisibleTo(view) for widget in (search, sort, filter_))
    assert _x(search, view) < _x(sort, view) < _x(filter_, view)
    assert _y(search, view) < _y(view.tree, view)


@pytest.mark.parametrize("view_id", ["workspace", "live", "offline"])
def test_the_footer_buttons_sit_directly_under_the_tree(session: WorkspaceSession, view_id: str) -> None:
    view = _show(session, view_id)

    buttons = _footer_buttons(view)

    expected = _FIRST_FOOTER_BUTTON[view_id]
    if expected is None:
        assert buttons == []
        return
    first = buttons[0]
    assert first.text() == expected
    gap = _y(first, view) - (_y(view.tree, view) + view.tree.height())
    assert 0 <= gap < first.height(), "nothing sits between the tree and the footer buttons"


@pytest.mark.usefixtures("restore_app_appearance")
@pytest.mark.parametrize("size", [13, 21])
@pytest.mark.parametrize("view_id", ["workspace", "live", "offline"])
def test_the_view_shows_its_text_in_full_at_small_and_large_text(
    session: WorkspaceSession, view_id: str, size: int
) -> None:
    apply_text_size(size)
    view = _show(session, view_id)

    assert clipped_text_widgets(view) == []


def test_the_sidebar_narrows_to_each_views_minimum_and_leaves_the_viewers_half(session: WorkspaceSession) -> None:
    """Each view can be dragged to its own minimum with its text in full; dragged wide, the viewers keep half."""
    splitter = session.widget().findChild(QSplitter, "WorkspaceSidebarSplitter")
    assert splitter is not None
    widths = {}
    for view_id in ("workspace", "live", "offline"):
        view = _show(session, view_id)
        splitter.moveSplitter(view.minimumSizeHint().width(), 1)
        QApplication.processEvents()
        widths[view_id] = splitter.sizes()[0]
        assert clipped_text_widgets(view) == []
    assert widths["workspace"] < widths["live"]
    splitter.moveSplitter(splitter.width(), 1)
    assert splitter.sizes()[1] >= splitter.sizes()[0] > widths["live"]
