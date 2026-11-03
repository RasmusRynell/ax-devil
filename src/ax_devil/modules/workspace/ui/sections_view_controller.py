"""Feed a sections view its part of the workspace, and whatever its concept adds to it."""

from __future__ import annotations

from collections.abc import Callable, Sequence, Set

from PySide6.QtCore import QObject

from ax_devil.modules.workspace.ui.browser_rows import (
    BY_NAME,
    WORKSPACE_ORDER,
    BrowserSection,
    RowOrder,
    WorkspaceBrowserSection,
)
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from ax_devil.modules.workspace.ui.workspace_controller import WorkspaceController

Sections = tuple[WorkspaceBrowserSection, ...]


class SectionsViewController(QObject):
    """Show in *view* the workspace's sections of *kinds*, passed through *compose*, and *summary* in its header.

    *compose* may change the rows of those sections and add sections of its own, such as things to add; *summary*,
    when given, says what the header shows of what is listed. *orders* are the orders the view's Sort offers, the
    first applying at first; the view re-lists the sections itself when the user chooses another. The view follows
    ``WorkspaceController.sections_changed`` and is brought up to date whenever ``refresh`` is called, so what
    *compose* adds may change on its own. *workspace* carries out what the rows ask for. The controller lives and
    dies with *view*.
    """

    def __init__(
        self,
        view: SectionsView,
        workspace: WorkspaceController,
        kinds: Set[BrowserSection],
        *,
        compose: Callable[[Sections], Sections] = lambda held: held,
        summary: Callable[[Sections], str] | None = None,
        orders: Sequence[RowOrder] = (WORKSPACE_ORDER, BY_NAME),
    ) -> None:
        super().__init__(view)
        self._view = view
        self._workspace = workspace
        self._kinds = kinds
        self._compose = compose
        self._summary = summary
        view.set_orders(orders)
        workspace.connect_tree(view.tree)
        workspace.sections_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args: object) -> None:
        """List the view's sections again."""
        listed = self._compose(tuple(section for section in self._workspace.sections() if section.kind in self._kinds))
        self._view.set_sections(listed)
        if self._summary is not None:
            self._view.set_summary(self._summary(listed))
