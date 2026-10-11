"""Resizable dialog for displaying workspace item information."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QHeaderView, QLabel, QTreeWidget, QTreeWidgetItem, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.core.item_info import WorkspaceItemInfo


class _InformationTree(QTreeWidget):
    """Measure selectable value widgets at the width available in their column."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._values: list[tuple[QTreeWidgetItem, QLabel]] = []
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.header().sectionResized.connect(self._update_value_heights, Qt.ConnectionType.QueuedConnection)

    def add_value(self, item: QTreeWidgetItem, value: str) -> None:
        """Install a selectable value whose row can grow as the column narrows."""
        item.setText(1, "")
        label = QLabel(value)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        label.setWordWrap(True)
        label.setContentsMargins(Space.XS, 0, Space.XS, 0)
        self.setItemWidget(item, 1, label)
        self._values.append((item, label))
        item.setSizeHint(1, label.sizeHint())

    def sizeHint(self) -> QSize:
        """Include the preferred width of values rather than Qt's default viewport width."""
        size = super().sizeHint()
        value_width = max((label.sizeHint().width() for _, label in self._values), default=0)
        size.setWidth(max(size.width(), self.columnWidth(0) + value_width + 2 * self.frameWidth()))
        return size

    def heightForWidth(self, width: int) -> int:
        """Account for wrapping when shared window bounds reduce the preferred width."""
        value_width = max(1, width - self.columnWidth(0) - 2 * self.frameWidth())
        extra_height = sum(
            max(0, label.heightForWidth(value_width) - item.sizeHint(1).height()) for item, label in self._values
        )
        return super().sizeHint().height() + extra_height

    def _update_value_heights(self) -> None:
        value_width = max(1, self.columnWidth(1))
        for item, label in self._values:
            height = label.heightForWidth(value_width)
            hint = QSize(0, height)
            if item.sizeHint(1) != hint:
                item.setSizeHint(1, hint)
        self.doItemsLayout()


class WorkspaceItemInfoDialog(BaseDialog):
    """Display workspace item information in a resizable tree view."""

    def __init__(self, info: WorkspaceItemInfo, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            title=info.title,
            scroll_content=False,
        )
        self._info = info
        self._tree = _InformationTree(self)
        self._configure_tree()
        self._populate_tree()
        self._tree.resizeColumnToContents(0)
        self._tree.resizeColumnToContents(1)
        self.add_content_widget(self._tree, stretch=1)
        self.add_button("Close", self.accept, is_default=True)

    def _configure_tree(self) -> None:
        """Configure the information tree widget."""
        self._tree.setColumnCount(2)
        self._tree.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._tree.setHeaderLabels(["Item", "Value"])
        self._tree.setRootIsDecorated(True)
        self._tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self._tree.setUniformRowHeights(False)
        self._tree.setAlternatingRowColors(False)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tree.setStyleSheet("QTreeWidget::item { padding: 0px; }")
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _populate_tree(self) -> None:
        """Populate the tree from the provided information payload."""
        root = self._build_item(self._info)
        self._tree.addTopLevelItem(root)
        self._tree.expandAll()
        self._install_value_widgets(root)

    def _install_value_widgets(self, item: QTreeWidgetItem) -> None:
        """Recursively replace value-column text with selectable QLabel widgets."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child is None:
                continue
            value = child.text(1)
            if value:
                self._tree.add_value(child, value)
            self._install_value_widgets(child)

    def _build_item(self, info: WorkspaceItemInfo) -> QTreeWidgetItem:
        """Build one tree item and its descendants."""
        item = QTreeWidgetItem([info.title, ""])
        for label, value in info.fields:
            child = QTreeWidgetItem([label, value])
            item.addChild(child)
        for child_info in info.children:
            item.addChild(self._build_item(child_info))
        return item
