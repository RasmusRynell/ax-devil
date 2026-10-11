"""The Workspace view's content browser: a search field and an excluded-items filter over the section tree.

Right-clicking a row offers its menu; what the tree's rows do otherwise is the workspace controller's to carry out.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPalette
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QMenu, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.workspace.core.content import Content
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo
from ax_devil.modules.workspace.core.items import WorkspaceItem
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.item_info_dialog import WorkspaceItemInfoDialog
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog
from ax_devil.modules.workspace.ui.section_tree import SectionTree

_PRESSED_ALPHA = 0.7


class ContentBrowserWidget(QWidget):
    """Tree view showing workspace contents with navigation and context menus."""

    content_activated = Signal(object, int)
    content_open_to_side_requested = Signal(object, int)
    item_remove_requested = Signal(str)  # item id
    item_rename_requested = Signal(str, str)  # item id, new label; empty for the default name
    export_requested = Signal(object)  # OnScreenWorkspaceItem
    rows_changed = Signal()  # After set_browser_sections; see has_rows

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilContentBrowser")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._show_excluded = False
        self._search_query = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(Space.S, Space.XS, Space.S, Space.XS)
        toolbar.setSpacing(Space.S)
        self._search_edit = self._create_search_edit()
        self._show_excluded_toggle = self._create_show_excluded_toggle()
        toolbar.addWidget(self._search_edit, 1)
        toolbar.addWidget(self._show_excluded_toggle)
        layout.addLayout(toolbar)

        self.tree = SectionTree()
        """The rows, whose double-clicks, actions, and eyes the workspace controller carries out."""
        layout.addWidget(self.tree)

        self.tree.context_menu_requested.connect(self._show_context_menu)
        self._apply_visibility_filters()
        self.setStyleSheet("#AxDevilContentBrowser { background: palette(alternate-base); }")

    def set_browser_sections(self, sections: tuple[WorkspaceBrowserSection, ...]) -> None:
        """Render section headings and their explicit workspace item rows."""
        self.tree.set_sections(sections)
        self.rows_changed.emit()

    def has_rows(self) -> bool:
        """Return whether the browser lists any workspace content."""
        return self.tree.has_rows()

    def _create_search_edit(self) -> QLineEdit:
        """Create the content-name search field."""
        search = QLineEdit(self)
        search.setPlaceholderText("Search items")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._on_search_text_changed)
        return search

    def _create_show_excluded_toggle(self) -> QToolButton:
        """Create the global toggle for showing or hiding excluded items."""
        toggle = QToolButton(self)
        toggle.setAutoRaise(True)
        toggle.setCheckable(True)
        toggle.setChecked(self._show_excluded)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        # The crossed eye an excluded row shows; the button is tinted while excluded items are shown.
        toggle.setIcon(Icon.HIDDEN.icon())
        follow_appearance(toggle, lambda: self._style_show_excluded_toggle(toggle))
        self._update_show_excluded_tooltip(toggle, self._show_excluded)
        toggle.toggled.connect(self._on_show_excluded_toggled)
        return toggle

    def _style_show_excluded_toggle(self, toggle: QToolButton) -> None:
        """Style the show-excluded button bare, with an accent background while it is pressed."""
        pressed = palette_color_css(QGuiApplication.palette(), QPalette.ColorRole.Highlight, alpha=_PRESSED_ALPHA)
        toggle.setStyleSheet(
            f"""
            QToolButton {{ border: none; padding: 0px; margin: 0px; background: transparent; }}
            QToolButton:hover {{ background: transparent; }}
            QToolButton:checked {{ background: {pressed}; border-radius: {Radius.CONTROL}px; }}
            """
        )

    def _update_show_excluded_tooltip(self, toggle: QToolButton, show: bool) -> None:
        """Update the tooltip of the show-excluded browser filter."""
        if show:
            toggle.setToolTip("Showing excluded items. Click to hide them from this list.")
        else:
            toggle.setToolTip("Excluded items are hidden from this list. Click to show them.")

    def _on_show_excluded_toggled(self, show: bool) -> None:
        """Handle the global show-excluded toggle."""
        self._show_excluded = show
        self._update_show_excluded_tooltip(self._show_excluded_toggle, show)
        self._apply_visibility_filters()

    def _on_search_text_changed(self, text: str) -> None:
        """Filter workspace rows by their displayed names."""
        self._search_query = text.strip().casefold()
        self._apply_visibility_filters()

    def _apply_visibility_filters(self) -> None:
        """Hide excluded rows unless they are to be shown, and while searching, rows that do not match the search."""
        query = self._search_query
        self.tree.set_filter(
            lambda row: row.consideration_ref is None or row.is_considered or self._show_excluded,
            (lambda row: query in row.search_text.casefold()) if query else None,
        )

    def _show_context_menu(self, row: WorkspaceBrowserRow, position: QPoint) -> None:
        """Show the menu of *row* at the screen *position*."""
        menu = self._build_context_menu(row)
        try:
            if menu.actions():
                menu.exec(position)
        finally:
            menu.deleteLater()

    def _build_context_menu(self, row: WorkspaceBrowserRow) -> QMenu:
        """Build the context menu for one row."""
        menu = QMenu(self)
        if row.activation_target is not None:
            self._add_open_actions(menu, *row.activation_target)
        information_factory = row.information_factory
        if information_factory is not None:
            info_action = QAction("Information", menu)
            info_action.triggered.connect(
                lambda _checked=False, factory=information_factory: self._show_item_information(factory())
            )
            menu.addAction(info_action)

        if row.export_target is not None:
            export_action = QAction("Export video" if row.is_open else "Export video (open it first)", menu)
            export_action.setEnabled(row.is_open)
            export_action.triggered.connect(
                lambda _checked=False, target=row.export_target: self.export_requested.emit(target)
            )
            menu.addAction(export_action)

        workspace_item = row.item
        if workspace_item is not None:
            rename_action = QAction("Rename", menu)
            rename_action.setEnabled(workspace_item.renamable)
            rename_action.triggered.connect(
                lambda _checked=False, value=workspace_item, shown=row.label: self._rename_item(value, shown)
            )
            menu.addAction(rename_action)
            remove_action = QAction("Remove", menu)
            remove_action.triggered.connect(
                lambda _checked=False, item_id=workspace_item.id: self.item_remove_requested.emit(item_id)
            )
            menu.addAction(remove_action)

        return menu

    def _rename_item(self, item: WorkspaceItem, shown_name: str) -> None:
        """Ask for a new name for *item* and request the rename when the user confirms it.

        *shown_name* is the label of the row the user clicked. An unlabeled item shows its default name there, which
        can differ from ``item.display_name`` when several playlists share the row. Keeping that name means no label.
        """
        with RenameDialog(item.label or shown_name, self) as dialog:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.name()
        if not item.label and name == shown_name:
            name = ""
        self.item_rename_requested.emit(item.id, name)

    def _add_open_actions(self, menu: QMenu, content: Content, start_index: int) -> None:
        """Add actions that open the row in the preview pane or in a new split."""
        open_action = QAction("Open", menu)
        open_action.setToolTip("Replace the preview (unpinned) pane")
        open_action.triggered.connect(lambda _checked=False: self.content_activated.emit(content, start_index))
        side_action = QAction("Open to the Side", menu)
        side_action.setToolTip("Open pinned in a new split (Ctrl+double-click)")
        side_action.triggered.connect(
            lambda _checked=False: self.content_open_to_side_requested.emit(content, start_index)
        )
        menu.addAction(open_action)
        menu.addAction(side_action)
        menu.addSeparator()

    def _show_item_information(self, info: WorkspaceItemInfo) -> None:
        """Display a simple modal summary for one workspace tree item."""
        with WorkspaceItemInfoDialog(info, self) as dialog:
            dialog.exec()
