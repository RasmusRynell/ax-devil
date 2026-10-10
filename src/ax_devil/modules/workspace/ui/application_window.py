"""Core application workspace for ax-devil."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QFontMetrics
from PySide6.QtWidgets import QApplication, QHBoxLayout, QSplitter, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.core.intake import is_video_file
from ax_devil.modules.workspace.ui.activity_bar import ActivityBar
from ax_devil.modules.workspace.ui.sidebar_panel import SidebarPanel
from ax_devil.modules.workspace.ui.split_view import SplitView

_SIDEBAR_CHARS = 34  # Default sidebar width in average characters of body text.


class ApplicationWindow(QWidget):
    """Main application workspace widget.

    Coordinates the main application components:
    - Activity bar along the left edge, whose Workspace button shows and hides the sidebar
    - Sidebar with the start panel or the content browser
    - Center area for viewer widgets (video viewers, analysis tools)

    Files dragged in from the desktop that include a video are offered through ``files_dropped``.
    Pane drags between viewers stay with the split view, which accepts them before they reach this widget.
    """

    files_dropped = Signal(list)

    def __init__(
        self,
        sidebar: SidebarPanel,
        center_area: SplitView,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._logger = get_logger(__name__)
        self._sidebar = sidebar
        self._center_area = center_area
        self._sidebar_wanted = True  # The user's choice.
        self._activity_bar = ActivityBar(self)
        self._activity_bar.add_view(Icon.BROWSE, "Workspace", self.toggle_sidebar)
        self._sidebar_width: int | None = None  # Until the user drags it, from the text size when it appears.

        self._setup_layout()
        self.setAcceptDrops(True)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._apply_sidebar()
        follow_appearance(self, self._follow_text_size)

    def toggle_sidebar(self) -> None:
        """Show or hide the sidebar."""
        self._sidebar_wanted = not self.is_sidebar_shown()
        self._apply_sidebar()

    def is_sidebar_shown(self) -> bool:
        """Return whether the sidebar is on screen."""
        return not self._sidebar.isHidden()

    def activity_bar(self) -> ActivityBar:
        """Return the activity bar, for adding app-wide action buttons."""
        return self._activity_bar

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        """Remember the width the user drags the sidebar to; dragging it closed hides it until toggled back."""
        width = self._splitter.sizes()[0]
        if width > 0:
            self._sidebar_width = width
        else:
            self._sidebar_wanted = False
            self._sidebar.hide()
            self._activity_bar.set_view_shown(False)

    def _apply_sidebar(self) -> None:
        """Show the sidebar when the user wants it, at its remembered width.

        Setting both splitter sizes when the sidebar appears keeps the splitter from first laying it out at another
        width and then moving the panes again.
        """
        show = self._sidebar_wanted
        self._activity_bar.set_view_shown(show)
        if show == self.is_sidebar_shown():
            return
        if not show:
            self._sidebar.hide()
            return
        self._sidebar.show()
        self._set_sidebar_width(self._sidebar_width or _default_sidebar_width())

    def _follow_text_size(self) -> None:
        """Keep a sidebar the user never dragged at its default width for the current text size."""
        if self._sidebar_width is None and self.is_sidebar_shown():
            self._set_sidebar_width(_default_sidebar_width())

    def _set_sidebar_width(self, width: int) -> None:
        total = sum(self._splitter.sizes())
        self._splitter.setSizes([width, max(0, total - width)])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """Accept desktop file drags that contain at least one video file."""
        _accept_video_drag(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        """Keep accepting video file drags while they move over the workspace."""
        _accept_video_drag(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """Emit the dropped local files when they include a video."""
        if not _accept_video_drag(event):
            return
        paths = _local_files(event.mimeData())
        self._logger.info(f"Dropped {len(paths)} file(s) onto the workspace")
        self.files_dropped.emit(paths)

    def _setup_layout(self) -> None:
        """Set up the main layout structure."""
        self._logger.debug("Setting up application window layout")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._activity_bar)

        # Horizontal splitter: sidebar (left) + split view (center)
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setObjectName("WorkspaceSidebarSplitter")
        self._splitter.setHandleWidth(1)
        self._splitter.setStyleSheet(
            """
            QSplitter#WorkspaceSidebarSplitter {
                border: none;
                margin: 0px;
                padding: 0px;
            }
            QSplitter#WorkspaceSidebarSplitter::handle {
                width: 1px;
                margin: 0px;
                padding: 0px;
                border: none;
                image: none;
            }
            """
        )
        self._splitter.addWidget(self._sidebar)
        self._splitter.addWidget(self._center_area)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setChildrenCollapsible(True)

        main_layout.addWidget(self._splitter, 1)


def _default_sidebar_width() -> int:
    """Return the sidebar's width before the user drags it: a number of average body-text characters."""
    return QFontMetrics(QApplication.font()).averageCharWidth() * _SIDEBAR_CHARS


def _local_files(mime_data: QMimeData) -> list[Path]:
    """Return the local file paths carried by a drag."""
    return [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]


def _accept_video_drag(event: QDropEvent) -> bool:
    """Accept *event* when it carries at least one local video file, and ignore it otherwise."""
    accepted = any(is_video_file(path) for path in _local_files(event.mimeData()))
    if accepted:
        event.acceptProposedAction()
    else:
        event.ignore()
    return accepted
