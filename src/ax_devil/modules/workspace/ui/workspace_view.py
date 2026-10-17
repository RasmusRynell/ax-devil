"""The Workspace view: everything the workspace holds, or the start panel while it holds nothing."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.sidebar_view import ViewGroup
from ax_devil.modules.chrome.small_button import ButtonKind
from ax_devil.modules.workspace.ui.sections_view import SectionsView
from ax_devil.modules.workspace.ui.start_panel import StartPanel

WORKSPACE_VIEW_ID = "workspace"


class WorkspaceView(SectionsView):
    """Show every section the workspace holds, or the start panel while there is none.

    The header carries the workspace's name, which a click edits, and a mark while it has unsaved changes. A banner
    says when the unsaved changes were restored from the last run, and offers to keep or discard them.
    """

    discard_requested = Signal()

    def __init__(self, start_panel: StartPanel, parent: QWidget | None = None) -> None:
        super().__init__(
            WORKSPACE_VIEW_ID,
            "Workspace",
            Icon.WORKSPACE,
            group=ViewGroup.WORKSPACE,
            search_placeholder="Search items",
            editable_title=True,
            empty_page=start_panel,
            parent=parent,
        )
        self._restored = Banner(
            "Restored unsaved changes", "Discard", dismiss_text="Keep", action_kind=ButtonKind.OUTLINE
        )
        self._restored.action.clicked.connect(self.discard_requested)
        self._restored.hide()
        self.set_footer_panel(self._restored)

    def set_restored(self, shown: bool) -> None:
        """Show or hide the banner that says the unsaved changes were restored."""
        self._restored.setVisible(shown)

    def set_workspace(self, name: str, path: Path | None, modified: bool) -> None:
        """Show the workspace called *name*, saved at *path* (None before the first save), and whether it has unsaved
        changes."""
        self.set_title(name, "" if path is None else str(path))  # The file, rather than the name again.
        self.set_summary("●" if modified else "", "Unsaved changes")
