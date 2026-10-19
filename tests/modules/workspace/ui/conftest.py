"""Fixtures shared by the tests in this folder."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.live_view_controller import LiveViewController
from ax_devil.modules.workspace.ui.session import WorkspaceSession
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.live_view import LIVE_VIEWER, Checker, HeldResolver, Live, Scanner
from tests.helpers.workspace import DummyViewer
from tests.helpers.workspace_session import new_session
from tests.helpers.workspace_store import BlockingResolver


@pytest.fixture()
def blocking() -> Iterator[BlockingResolver]:
    resolver = BlockingResolver()
    yield resolver
    resolver.release.set()


@pytest.fixture()
def workspace_session(
    qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager, tmp_path: Path
) -> WorkspaceSession:
    session = new_session(render_catalog_manager, tmp_path)
    widget = session.widget()
    qtbot.addWidget(widget)
    widget.show()
    return session


@pytest.fixture
def live(qtbot: QtBot, render_catalog_manager: SceneRenderCatalogManager) -> Generator[Live, None, None]:
    resolutions = HeldResolver()
    jobs = BackgroundJobs(max_workers=1)  # One worker runs jobs in the order they were submitted.
    store = WorkspaceStore(resolutions)
    center = SplitView()
    workspace = WorkspaceController(store, center, render_catalog_manager)
    host = QWidget()  # Holds the view, so a test can delete the view alone.
    qtbot.addWidget(host)
    view = LiveView(host)
    qtbot.addWidget(center)
    scanner = Scanner()
    checker = Checker()
    controller = LiveViewController(view, workspace, store, jobs, scan=scanner, parent=workspace, check=checker)
    with patch(LIVE_VIEWER, DummyViewer):
        yield Live(qtbot, view, store, center, scanner, jobs, resolutions, controller, checker)
    scanner.gate.set()
    checker.gate.set()
