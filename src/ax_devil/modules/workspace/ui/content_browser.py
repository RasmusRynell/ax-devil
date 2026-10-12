"""The Workspace view's content browser: a search field and an excluded-items filter over the section tree.

What the tree's rows do, their right-click menu included, is the workspace controller's to carry out.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Radius, Space
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserSection
from ax_devil.modules.workspace.ui.section_tree import SectionTree

_PRESSED_ALPHA = 0.7


class ContentBrowserWidget(QWidget):
    """Tree view showing workspace contents with navigation and context menus."""

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
        """The rows, whose double-clicks, actions, eyes and menus the workspace controller carries out."""
        layout.addWidget(self.tree)

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
