"""The Bookmarks view: the moments marked in the workspace's videos and playlists, grouped by what they mark."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.chip import Chip, ChipKind, ChoiceChips
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.list_row import ListRow, muted_label
from ax_devil.modules.chrome.sidebar_view import SidebarView, ViewGroup
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.chrome.view_panel import ViewPanel
from ax_devil.modules.workspace.core import Bookmark, BookmarkKind
from ax_devil.modules.workspace.ui.bookmark_rows import BookmarkGroup
from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog

BOOKMARKS_VIEW_ID = "bookmarks"
KIND_FILTERS: tuple[BookmarkKind | None, ...] = (
    None,
    BookmarkKind.ISSUE,
    BookmarkKind.EXAMPLE,
    BookmarkKind.QUESTION,
    BookmarkKind.NOTE,
)
"""The filter chips in order: all kinds, then each kind."""
_ICON_PX = 16


class BookmarksView(ViewPanel):
    """Show the bookmark groups it is given and report what the user asks for; it decides nothing itself."""

    search_changed = Signal(str)
    kind_filter_changed = Signal(object)  # BookmarkKind | None
    bookmark_activated = Signal(str)  # bookmark id
    reviewed_change_requested = Signal(object, bool)  # BookmarkTarget, whether it is now reviewed
    rename_requested = Signal(str, str)  # bookmark id, the entered name
    kind_change_requested = Signal(str, object)  # bookmark id, BookmarkKind
    remove_requested = Signal(str)  # bookmark id
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._count = muted_label("")
        super().__init__("Bookmarks", header_widget=self._count, parent=parent)
        self._groups: tuple[BookmarkGroup, ...] = ()
        self._active_id: str | None = None
        self._empty_text = "No bookmarks"

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search bookmarks")
        self._search.setClearButtonEnabled(True)
        self._search_icon = self._search.addAction(Icon.SEARCH.icon(), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self.search_changed)
        self._kind_chips = ChoiceChips(["All", *(kind.label for kind in KIND_FILTERS if kind is not None)], self)
        self._kind_chips.choice_changed.connect(lambda index: self.kind_filter_changed.emit(KIND_FILTERS[index]))
        self._filters = QWidget(self)
        filters = QVBoxLayout(self._filters)
        filters.setContentsMargins(Space.M, Space.M, Space.M, Space.S)
        filters.setSpacing(Space.S)
        filters.addWidget(self._search)
        filters.addWidget(self._kind_chips)
        self.add(self._filters)

        self._rows = QWidget()
        self._rows_layout = QVBoxLayout(self._rows)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._rows)
        # The rows sit on the panel's own background, from edge to edge.
        self._scroll.setObjectName("AxDevilBookmarkList")
        self._rows.setObjectName("AxDevilBookmarkRows")
        self._scroll.setStyleSheet(
            "#AxDevilBookmarkList { background: transparent; border: none; margin: 0; }"
            " #AxDevilBookmarkRows { background: transparent; }"
        )
        self._scroll.viewport().setAutoFillBackground(False)
        self.add(self._scroll)
        self.body.setStretchFactor(self._scroll, 1)

        self._info_icon = QLabel(self)
        info_text = muted_label("For videos and playlists from files. Live streams can't be bookmarked.")
        info_text.setWordWrap(True)
        info = QWidget(self)
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(Space.M)
        info_layout.addWidget(self._info_icon, 0, Qt.AlignmentFlag.AlignTop)
        info_layout.addWidget(info_text, 1)
        self.add_padded(info, top=Space.S, bottom=Space.S)
        self._export = SmallButton("Export CSV", Icon.EXPORT)
        self._export.clicked.connect(self.export_requested)
        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(self._export)
        footer_layout.addStretch(1)
        self.add_padded(footer, top=0, bottom=Space.M)
        follow_appearance(self, self._show_rows)

    def sidebar_view(self) -> SidebarView:
        """Return this view as a sidebar view of the review group."""
        return SidebarView(BOOKMARKS_VIEW_ID, "Bookmarks", Icon.BOOKMARK, ViewGroup.REVIEW, self)

    def set_groups(self, groups: Sequence[BookmarkGroup], count: int, active_id: str | None) -> None:
        """Show *groups*, out of *count* bookmarks in all, with the bookmark *active_id* marked as the one opened.

        Without any bookmarks, the search and the filters are hidden.
        """
        self._groups = tuple(groups)
        self._active_id = active_id
        self._count.setText(str(count) if count else "")
        self._filters.setVisible(count > 0)
        self._export.setEnabled(count > 0)
        self._empty_text = "No bookmarks" if count == 0 else "No matches"
        self._show_rows()

    def choose_export_path(self, suggested: Path) -> Path | None:
        """Ask where to save the CSV file, starting from *suggested*; return None when the user cancels."""
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(suggested), "CSV files (*.csv)")
        return Path(path) if path else None

    def _show_rows(self) -> None:
        palette = self.palette()
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        self._search_icon.setIcon(Icon.SEARCH.icon(muted))
        self._info_icon.setPixmap(Icon.INFO.icon(muted).pixmap(_ICON_PX, _ICON_PX))
        while (child := self._rows_layout.takeAt(0)) is not None:
            if (widget := child.widget()) is not None:
                # Deleted later: the click that changed the rows may still be running in one of them.
                widget.hide()
                widget.deleteLater()
        for group in self._groups:
            self._rows_layout.addWidget(self._group_row(group))
            for row in group.rows:
                bookmark_row = ListRow(
                    row.bookmark.label,
                    icon=Icon.BOOKMARK.icon(row.mark.color(palette)),
                    subtitle=row.subtitle,
                    active=row.bookmark.id == self._active_id,
                    indent=Space.XL,
                )
                if not group.missing:
                    bookmark_row.activated.connect(
                        lambda bookmark_id=row.bookmark.id: self.bookmark_activated.emit(bookmark_id)
                    )
                bookmark_row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                bookmark_row.customContextMenuRequested.connect(
                    lambda position, widget=bookmark_row, bookmark=row.bookmark: self._show_menu(
                        widget, bookmark, position
                    )
                )
                self._rows_layout.addWidget(bookmark_row)
        if not self._groups:
            empty = muted_label(self._empty_text)
            empty.setContentsMargins(Space.L, Space.M, Space.M, Space.M)
            self._rows_layout.addWidget(empty)
        self._rows_layout.addStretch(1)

    def _group_row(self, group: BookmarkGroup) -> ListRow:
        trailing: list[QWidget] = []
        if not group.missing:
            chip = Chip(
                "Reviewed" if group.reviewed else "To review",
                ChipKind.OK if group.reviewed else ChipKind.NEUTRAL,
                clickable=True,
            )
            chip.clicked.connect(lambda: self.reviewed_change_requested.emit(group.target, not group.reviewed))
            trailing.append(chip)
        return ListRow(
            group.title,
            icon=group.icon.icon(group.mark.color(self.palette())),
            trailing=trailing,
            dim=group.missing,
        )

    def _show_menu(self, row: QWidget, bookmark: Bookmark, position: QPoint) -> None:
        menu = QMenu(self)
        try:
            rename = menu.addAction("Rename")
            rename.triggered.connect(lambda: self._ask_name(bookmark))
            kinds = menu.addMenu("Kind")
            for kind in BookmarkKind:
                action = QAction(kind.label, kinds)
                action.setCheckable(True)
                action.setChecked(kind is bookmark.kind)
                action.triggered.connect(lambda _=False, kind=kind: self.kind_change_requested.emit(bookmark.id, kind))
                kinds.addAction(action)
            menu.addSeparator()
            remove = menu.addAction("Remove")
            remove.triggered.connect(lambda: self.remove_requested.emit(bookmark.id))
            menu.exec(row.mapToGlobal(position))
        finally:
            menu.deleteLater()

    def _ask_name(self, bookmark: Bookmark) -> None:
        with RenameDialog(bookmark.label, self) as dialog:
            if dialog.exec() == RenameDialog.DialogCode.Accepted:
                self.rename_requested.emit(bookmark.id, dialog.name())
