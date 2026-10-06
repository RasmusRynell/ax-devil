"""Workspace widget base classes and interfaces for ax-devil."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.workspace.icons import load_resource_icon

logger = get_logger(__name__)

HEADER_HEIGHT_PX = 28
HEADER_BUTTON_SIZE_PX = 24
HEADER_ICON_SIZE_PX = 16

if TYPE_CHECKING:
    from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
    from ax_devil.modules.workspace.content import ConsiderationItemRef
    from ax_devil.modules.workspace.workspace_manager import OnScreenWorkspaceItem


class WorkspaceWidget(QFrame):
    """Base class for all workspace widgets.

    Defines the interface that all workspace widgets must implement, ensuring consistent behavior and providing
    standardized functionality like widget identification, display names, and close operations.
    """

    # Signal emitted when the widget requests to be closed
    close_requested = Signal()
    on_screen_item_changed = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._logger = logger
        self._pinned: bool = False

        # Set up the base widget structure
        self._setup_base_ui()

        # Allow subclasses to set up their specific UI
        self._setup_widget_ui()

        self._title_label.setText(self.get_display_name())

        widget_id = id(self)
        widget_class = self.__class__.__name__
        self.destroyed.connect(
            lambda _=None, widget_id=widget_id, widget_class=widget_class: self._logger.debug(
                f"WorkspaceWidget.destroyed id={widget_id} class={widget_class}"
            )
        )

    def _setup_base_ui(self) -> None:
        """Set up the base UI structure with header, pin, and close controls."""
        # Main layout for the entire widget
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # Header section container (used as drag handle by SplitView)
        self._header_widget = QFrame()
        self._header_widget.setObjectName("WorkspaceWidgetHeader")
        self._header_layout = QHBoxLayout(self._header_widget)
        self._header_layout.setContentsMargins(8, 0, 2, 0)
        self._header_layout.setSpacing(2)

        # Title label on the left; italic while the pane is a replaceable preview
        self._title_label = QLabel("")
        self._title_label.setObjectName("WorkspaceWidgetTitle")
        self._header_layout.addWidget(self._title_label)

        # Spacer to push buttons to the right
        self._header_layout.addStretch()

        self._pin_button = self._create_header_button()
        self._pin_button.setCheckable(True)
        self._pin_button.toggled.connect(self.set_pinned)
        self._header_layout.addWidget(self._pin_button)

        self._close_button = self._create_header_button()
        self._close_button.setToolTip("Close this pane")
        self._close_button.clicked.connect(self._on_close_button_clicked)
        self._header_layout.addWidget(self._close_button)

        # Add header widget to main layout
        self._main_layout.addWidget(self._header_widget)

        # Content area for subclass widgets
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._main_layout.addLayout(self._content_layout)

        self._apply_base_styling()
        self._refresh_header_icons()
        self._update_pin_state()

    def _create_header_button(self) -> QToolButton:
        """Create one header icon button with a comfortable click target."""
        button = QToolButton(self._header_widget)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedSize(HEADER_BUTTON_SIZE_PX, HEADER_BUTTON_SIZE_PX)
        button.setIconSize(QSize(HEADER_ICON_SIZE_PX, HEADER_ICON_SIZE_PX))
        return button

    def _apply_base_styling(self) -> None:
        """Apply base styling to the widget."""
        # Scope styles to this widget and its header using object names
        self.setObjectName("WorkspaceWidgetRoot")
        self.setStyleSheet(
            f"""
            #WorkspaceWidgetRoot {{
                border: 0px solid palette(Mid);
                border-radius: 0px;
            }}

            /* Draggable header bar */
            #WorkspaceWidgetHeader {{
                border-bottom: 0px;
                border-radius: 0px;
                min-height: {HEADER_HEIGHT_PX}px;
                max-height: {HEADER_HEIGHT_PX}px;
            }}

            #WorkspaceWidgetHeader QToolButton {{
                border: none;
                border-radius: 4px;
                background-color: transparent;
            }}
            #WorkspaceWidgetHeader QToolButton:hover {{
                background-color: palette(Midlight);
            }}
            #WorkspaceWidgetHeader QToolButton:pressed {{
                background-color: palette(Mid);
            }}

            #WorkspaceWidgetTitle {{
                font-weight: 600;
            }}
            #WorkspaceWidgetTitle[preview="true"] {{
                font-style: italic;
                font-weight: 400;
            }}
            """
        )

    def _on_close_button_clicked(self) -> None:
        """Handle close button click: request close via signal; cleanup happens in container."""
        self._logger.debug(f"Close button clicked for {self.get_display_name()}")
        self.close_requested.emit()

    def event(self, e: QEvent) -> bool:
        # Ensure that any programmatic close will route through SplitView removal path
        # by emitting close_requested, while letting the container handle cleanup & deletion.
        if e.type() == QEvent.Type.Close:
            self.close_requested.emit()
        return bool(super().event(e))

    def changeEvent(self, event: QEvent) -> None:
        """Retint header icons when the application theme changes."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._refresh_header_icons()
            self._update_pin_state()

    def _refresh_header_icons(self) -> None:
        """Build header icons from the current palette."""
        color = self.palette().color(QPalette.ColorRole.WindowText)
        self._unpinned_icon = load_resource_icon("pin-icon.png", color)
        self._pinned_icon = load_resource_icon("pinned-icon.png", color)
        self._close_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))

    def get_content_layout(self) -> QVBoxLayout:
        """Get the content layout for subclasses to add their widgets.

        Returns:
            Content layout where subclasses should add their UI elements
        """
        return self._content_layout

    def get_header_widget(self) -> QWidget:
        """Expose the header widget used for dragging in split view."""
        return self._header_widget

    def on_workspace_attached(self) -> None:
        """Run after this widget has been attached to the workspace split view."""

    def get_display_name(self) -> str:
        """Get the human-readable display name of this widget.

        Returns:
            Display name shown in the widget header
        """
        raise NotImplementedError("Subclasses must implement get_display_name()")

    def _setup_widget_ui(self) -> None:
        """Set up the widget-specific UI.

        Subclasses must implement this method to create their specific user interface elements in the content area.
        """
        raise NotImplementedError("Subclasses must implement _setup_widget_ui()")

    def is_pinned(self) -> bool:
        """Return whether this pane is pinned; unpinned panes are previews that new content replaces."""
        return self._pinned

    def set_pinned(self, pinned: bool) -> None:
        """Pin or unpin this pane, updating its header to show whether it is a preview."""
        if self._pinned != pinned:
            self._pinned = pinned
            self._update_pin_state()

    def _update_pin_state(self) -> None:
        """Show the pinned or preview state in the title and pin button."""
        self._pin_button.blockSignals(True)
        self._pin_button.setChecked(self._pinned)
        self._pin_button.blockSignals(False)
        if self._pinned:
            self._pin_button.setIcon(self._pinned_icon)
            self._pin_button.setToolTip("Unpin: let opened content replace this pane")
            self._title_label.setToolTip("Pinned: opened content will not replace this pane")
        else:
            self._pin_button.setIcon(self._unpinned_icon)
            self._pin_button.setToolTip("Pin: keep this pane open when other content is opened")
            self._title_label.setToolTip("Preview (italic): the next opened content replaces this pane")
        self._title_label.setProperty("preview", not self._pinned)
        self._title_label.style().unpolish(self._title_label)
        self._title_label.style().polish(self._title_label)

    def current_on_screen_item(self) -> OnScreenWorkspaceItem | None:
        """Return the workspace item currently visible in the widget."""
        return None

    def refresh_item_consideration(self, item_ref: ConsiderationItemRef, considered: bool) -> None:
        """Refresh viewer state after an item consideration change.

        Subclasses that display content affected by consideration state should override this.
        """

    def toggle_playback(self) -> None:
        """Toggle between playing and paused states.

        Subclasses that support playback should override this.
        """

    def pause_playback(self) -> None:
        """Pause playback when supported by the hosted tool."""

    def export_video(self) -> None:
        """Export the on-screen item when supported by the hosted tool."""

    def toggle_media_tools(self) -> None:
        """Show or hide the media tools panel when supported by the hosted tool."""

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step to the video when supported by the hosted tool."""

    def cleanup(self) -> None:
        """Clean any created resources."""
        return
