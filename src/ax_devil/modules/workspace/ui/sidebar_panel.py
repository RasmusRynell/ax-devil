"""Sidebar panel: a titled header over the start panel or the content browser."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.workspace.ui.content_browser import ContentBrowserWidget
from ax_devil.modules.workspace.ui.start_panel import StartPanel


class SidebarPanel(QWidget):
    """Show the start panel while the workspace is empty and the content browser once it lists anything."""

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
        title = QLabel("Workspace", self._header)
        TextRole.CAPTION.apply(title)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        layout.addWidget(self._header)

        self._pages = QStackedWidget(self)
        self._pages.addWidget(start_panel)
        self._pages.addWidget(content_browser)
        layout.addWidget(self._pages, 1)

        content_browser.rows_changed.connect(self._show_page)
        self._show_page()
        follow_appearance(self, self._apply_appearance)

    def showing_start_panel(self) -> bool:
        """Return whether the start panel is the page on screen."""
        return self._pages.currentWidget() is self._start_panel

    def _show_page(self) -> None:
        self._pages.setCurrentWidget(self._content_browser if self._content_browser.has_rows() else self._start_panel)

    def _apply_appearance(self) -> None:
        self._header.setFixedHeight(Height.PANE_HEADER.px)
        palette = self.palette()
        border = palette_color_css(palette, palette.ColorRole.Mid, alpha=0.5)
        muted = palette_color_css(palette, palette.ColorRole.Text, alpha=0.6)
        self.setStyleSheet(
            f"""
            #AxDevilSidebar {{ background: palette(alternate-base); }}
            #AxDevilSidebarHeader {{ border-bottom: 1px solid {border}; background: transparent; }}
            #AxDevilSidebarHeader QLabel {{ color: {muted}; }}
            """
        )
