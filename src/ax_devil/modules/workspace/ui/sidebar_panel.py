"""Sidebar panel: the workspace name over the start panel or the content browser."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.start_panel import StartPanel


class SidebarPanel(QWidget):
    """Show the workspace name over the start panel while the workspace is empty, or the content browser once it
    lists anything."""

    def __init__(
        self, content_browser: ContentBrowserWidget, start_panel: StartPanel, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AxDevilSidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content_browser = content_browser
        self._start_panel = start_panel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget(self)
        self._header.setObjectName("AxDevilSidebarHeader")
        self._header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(Space.L, 0, Space.M, 0)
        header_layout.setSpacing(Space.M)
        self._name_label = ElidedLabel("", Qt.TextElideMode.ElideRight, self._header)
        self._name_label.setObjectName("AxDevilWorkspaceName")
        TextRole.STRONG.apply(self._name_label)
        header_layout.addWidget(self._name_label, 1)
        self._modified_label = QLabel("●", self._header)
        self._modified_label.setObjectName("AxDevilWorkspaceModified")
        self._modified_label.setToolTip("Unsaved changes")
        self._modified_label.hide()
        header_layout.addWidget(self._modified_label)
        layout.addWidget(self._header)

        self._pages = QStackedWidget(self)
        self._pages.addWidget(start_panel)
        self._pages.addWidget(content_browser)
        layout.addWidget(self._pages, 1)

        content_browser.rows_changed.connect(self._show_page)
        self._show_page()
        follow_appearance(self, self._apply_appearance)

    def set_workspace(self, name: str, path: Path | None, modified: bool) -> None:
        """Show the workspace called *name*, saved at *path* (None before the first save), and whether it has unsaved
        changes."""
        self._name_label.set_full_text(name)
        self._name_label.setToolTip("" if path is None else str(path))  # The file, rather than the name again.
        self._modified_label.setVisible(modified)

    def showing_start_panel(self) -> bool:
        """Return whether the start panel is the page on screen."""
        return self._pages.currentWidget() is self._start_panel

    def _show_page(self) -> None:
        self._pages.setCurrentWidget(self._content_browser if self._content_browser.has_rows() else self._start_panel)

    def _apply_appearance(self) -> None:
        self._header.setFixedHeight(Height.PANE_HEADER.px)
        palette = self.palette()
        border = palette_color_css(palette, palette.ColorRole.Mid, alpha=0.5)
        muted = palette_color_css(palette, palette.ColorRole.PlaceholderText)
        self.setStyleSheet(
            f"""
            #AxDevilSidebar {{ background: palette(alternate-base); }}
            #AxDevilSidebarHeader {{ border-bottom: 1px solid {border}; background: transparent; }}
            #AxDevilWorkspaceModified {{ color: {muted}; }}
            """
        )
