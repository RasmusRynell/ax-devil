"""Viewer widget base class and interface for ax-devil."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QContextMenuEvent, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Height, Radius, Space, TextRole
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

HEADER_ICON_SIZE_PX = 16

SHARED_ACTION_IDS = ("view.toggle_media_tools", "view.toggle_lane_fullscreen", "view.add_bookmark", "view.reset_zoom")
"""The window's shortcut actions a viewer's context menu lists, in menu order, as ``set_shared_actions`` takes them."""

if TYPE_CHECKING:
    from ax_devil.modules.video_player.engine.viewport_state import ZoomStep
    from ax_devil.modules.workspace.core.bookmarks import BookmarkPosition, BookmarkTarget
    from ax_devil.modules.workspace.core.content import ConsiderationItemRef, OnScreenWorkspaceItem


class ViewerWidget(QFrame):
    """Base class for all viewer widgets.

    Defines the interface that all viewer widgets must implement, ensuring consistent behavior and providing
    standardized functionality like widget identification, display names, and close operations.
    """

    # Signal emitted when the widget requests to be closed
    close_requested = Signal()
    on_screen_item_changed = Signal(object)
    context_menu_requested = Signal(QPoint)  # Global position of a right click on the pane outside its video lanes
    consideration_change_requested = Signal(object, bool)  # ConsiderationItemRef, considered: asked for from the pane

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._logger = logger
        self._pinned: bool = False
        self._shared_actions: Mapping[str, QAction] = {}

        # Set up the base widget structure
        self._setup_base_ui()

        # Allow subclasses to set up their specific UI
        self._setup_widget_ui()

        self._title_label.setText(self.get_display_name())

        widget_id = id(self)
        widget_class = self.__class__.__name__
        self.destroyed.connect(
            lambda _=None, widget_id=widget_id, widget_class=widget_class: self._logger.debug(
                f"ViewerWidget.destroyed id={widget_id} class={widget_class}"
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
        self._header_widget.setObjectName("ViewerWidgetHeader")
        self._header_layout = QHBoxLayout(self._header_widget)
        self._header_layout.setContentsMargins(Space.M, 0, Space.XS, 0)
        self._header_layout.setSpacing(Space.XS)

        # Title label on the left; italic while the pane is a replaceable preview
        self._title_label = QLabel("")
        self._header_layout.addWidget(self._title_label)

        # Muted facts about the shown content; hidden when the header is too narrow for them
        self._details_label = QLabel("")
        self._details_label.setStyleSheet("color: palette(placeholder-text);")
        self._details_label.setContentsMargins(Space.S, 0, 0, 0)
        # Adds nothing to the pane's minimum width; the details hide instead when the pane gets narrow.
        self._details_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        TextRole.SMALL.apply(self._details_label)
        self._header_layout.addWidget(self._details_label)

        # Spacer to push buttons to the right
        self._header_layout.addStretch()

        self._pin_button = self._create_header_button()
        self._pin_button.setCheckable(True)
        self._pin_button.toggled.connect(self.set_pinned)
        self._header_layout.addWidget(self._pin_button)

        self._close_button = self._create_header_button()
        self._close_button.setIcon(Icon.CLOSE.icon())
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
        follow_appearance(self, self._apply_appearance)

    def _create_header_button(self) -> QToolButton:
        """Create one header icon button with a comfortable click target."""
        button = QToolButton(self._header_widget)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(HEADER_ICON_SIZE_PX, HEADER_ICON_SIZE_PX))
        return button

    def _apply_base_styling(self) -> None:
        """Apply base styling to the widget."""
        # Scope styles to this widget and its header using object names
        self.setObjectName("ViewerWidgetRoot")
        self.setStyleSheet(
            f"""
            #ViewerWidgetRoot {{
                border: 0px solid palette(Mid);
                border-radius: 0px;
            }}

            /* Draggable header bar */
            #ViewerWidgetHeader {{
                border-bottom: 0px;
                border-radius: 0px;
            }}

            #ViewerWidgetHeader QToolButton {{
                border: none;
                border-radius: {Radius.CONTROL}px;
                background-color: transparent;
            }}
            #ViewerWidgetHeader QToolButton:hover {{
                background-color: palette(Midlight);
            }}
            #ViewerWidgetHeader QToolButton:pressed {{
                background-color: palette(Mid);
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

    def _apply_appearance(self) -> None:
        """Size the header and its buttons to the text."""
        self._header_widget.setFixedHeight(Height.PANE_HEADER.px)
        for button in (self._pin_button, self._close_button):
            button.setFixedSize(Height.CONTROL.px, Height.CONTROL.px)
        self._update_pin_state()

    def set_display_name(self, name: str) -> None:
        """Show *name* as the new display name, such as after the item is renamed.

        Subclasses that store the name extend this to update their own copy and anything else showing it.
        ``get_display_name()`` must return *name* afterwards.
        """
        self._title_label.setText(name)
        self._fit_header_details()

    def set_header_details(self, text: str) -> None:
        """Show *text*, such as the video's size, frame rate and length, muted next to the title."""
        self._details_label.setText(text)
        self._fit_header_details()

    def header_details(self) -> str:
        """Return the details shown next to the title, or an empty string when they are hidden or unset."""
        return self._details_label.text() if not self._details_label.isHidden() else ""

    def _fit_header_details(self) -> None:
        """Show the details only when they fit beside the title and buttons."""
        text = self._details_label.text()
        self._details_label.setVisible(bool(text))
        # The layout's preferred width leaves out the details, whose size policy ignores their own.
        if text and self._header_layout.sizeHint().width() + self._details_label.sizeHint().width() > self.width():
            self._details_label.setVisible(False)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        """Ask for the pane's menu at a right click on the header or other chrome; a video lane asks for its own."""
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    def set_shared_actions(self, actions: Mapping[str, QAction]) -> None:
        """Use the window's *actions*, by shortcut id (``SHARED_ACTION_IDS``), in this pane's menus.

        They are the window's own, so their names, keys and enabled state are the ones the menu bar shows.
        """
        self._shared_actions = actions

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Refit the header details to the new width."""
        super().resizeEvent(event)
        self._fit_header_details()

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
            self._pin_button.setIcon(Icon.PINNED.icon())
            self._pin_button.setToolTip("Unpin: let opened content replace this pane")
            self._title_label.setToolTip("Pinned: opened content will not replace this pane")
        else:
            self._pin_button.setIcon(Icon.PIN.icon())
            self._pin_button.setToolTip("Pin: keep this pane open when other content is opened")
            self._title_label.setToolTip("Preview (italic): the next opened content replaces this pane")
        title_font = (TextRole.STRONG if self._pinned else TextRole.BODY).font()
        title_font.setItalic(not self._pinned)
        self._title_label.setFont(title_font)
        self._fit_header_details()

    def current_on_screen_item(self) -> OnScreenWorkspaceItem | None:
        """Return the workspace item currently visible in the widget."""
        return None

    def bookmark_position(self) -> BookmarkPosition | None:
        """Return the frame on screen as a bookmark would mark it, or None when nothing on screen can be bookmarked."""
        return None

    def bookmark_target(self) -> BookmarkTarget | None:
        """Return the target of the entry shown, or None when nothing shown can be bookmarked."""
        return None

    def show_frame(self, frame: int) -> None:
        """Show *frame* of the entry shown, paused, when supported by the hosted tool."""

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

    def step_frames(self, delta: int) -> None:
        """Step playback by *delta* frames when supported by the hosted tool."""

    def step_next_entry(self) -> None:
        """Navigate to the next playlist entry when supported by the hosted tool."""

    def step_prev_entry(self) -> None:
        """Navigate to the previous playlist entry when supported by the hosted tool."""

    def increase_playback_speed(self) -> None:
        """Raise the playback speed by one step when supported by the hosted tool."""

    def decrease_playback_speed(self) -> None:
        """Lower the playback speed by one step when supported by the hosted tool."""

    def toggle_info_overlay(self) -> None:
        """Show or hide the info overlay when supported by the hosted tool."""

    def toggle_media_tools(self) -> None:
        """Show or hide the media tools panel when supported by the hosted tool."""

    def zoom(self, step: ZoomStep) -> None:
        """Apply one keyboard zoom step to the video when supported by the hosted tool."""

    def cleanup(self) -> None:
        """Clean any created resources."""
        return
