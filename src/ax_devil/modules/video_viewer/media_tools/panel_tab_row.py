"""The row above the media tools pages: a tab per root page, the Appearance popover and the overflow menu."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QMenu, QTabBar, QToolButton, QWidget

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.tokens import Space

from .appearance_popover import AppearancePopover
from .media_tools_section import MediaToolsSection, add_section_menu
from .overlay_persistence_controls import OverlayPersistenceControls


class PanelTabRow(QWidget):
    """Tabs for the root pages of the panel, an Appearance button, and a ⋯ button with a submenu per control group.

    ``tabs`` has a tab per *sections*, in order; ``appearance_button`` opens *appearance* under the row, and
    ``more_button`` opens ``more_menu``, which is parented to *parent*, the panel.
    """

    def __init__(
        self,
        sections: Sequence[MediaToolsSection],
        *,
        appearance: AppearancePopover,
        overlay_controls: OverlayPersistenceControls,
        timing_controls: QWidget | None,
        export_action: QAction | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._sections = list(sections)
        self.tabs = QTabBar(self)
        self.tabs.setObjectName("mediaToolsTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        # Show every tab: the panel widens to fit them instead of hiding some behind scroll arrows.
        self.tabs.setUsesScrollButtons(False)
        for section in self._sections:
            self.tabs.setTabToolTip(self.tabs.addTab(section.title), section.help_html)

        self.appearance_button = QToolButton(self)
        self.appearance_button.setObjectName("mediaToolsAppearanceButton")
        self.appearance_button.setIcon(Icon.CATALOGS.icon())
        self.appearance_button.setAutoRaise(True)
        self.appearance_button.setToolTip("Appearance")
        self.appearance_button.setAccessibleName("Appearance")
        self.appearance_button.clicked.connect(lambda: appearance.open_below(self, self.width()))

        self.more_menu = QMenu(parent)
        self.more_menu.setToolTipsVisible(True)
        self.more_button = QToolButton(self)
        self.more_button.setObjectName("mediaToolsMoreButton")
        self.more_button.setIcon(Icon.MORE.icon())
        self.more_button.setAutoRaise(True)
        self.more_button.setToolTip("Overlay settings and export")
        self.more_button.setAccessibleName("More")
        self.more_button.setMenu(self.more_menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")

        # One submenu per control group, then the viewer's export command.
        add_section_menu(self.more_menu, MediaToolsSection.OVERLAY_PERSISTENCE, overlay_controls)
        if timing_controls is not None:
            add_section_menu(self.more_menu, MediaToolsSection.OVERLAY_SYNC, timing_controls)
        if export_action is not None:
            # Export is a one-shot command, so it sits beside the submenus. The lane owns the action, so this entry
            # and the lane's context menu are one command.
            self.more_menu.addSeparator()
            self.more_menu.addAction(export_action)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, Space.S, 0)
        layout.setSpacing(Space.S)
        layout.addWidget(self.tabs)
        layout.addStretch(1)
        layout.addWidget(self.appearance_button)
        layout.addWidget(self.more_button)

    def set_count(self, index: int, count: int) -> None:
        """Show *count* in the label of tab *index*."""
        self.tabs.setTabText(index, f"{self._sections[index].title} {count}")
