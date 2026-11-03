"""Read a section tree as the user sees it, and use its rows."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget

from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.workspace.ui.section_tree import SectionTree
from ax_devil.modules.workspace.ui.source_row import SourceRow


def _is_shown(item: QTreeWidgetItem) -> bool:
    """Return whether *item* is listed: neither it nor a row above it is hidden, and every row above is expanded."""
    if item.isHidden():
        return False
    parent = item.parent()
    while parent is not None:
        if parent.isHidden() or not parent.isExpanded():
            return False
        parent = parent.parent()
    return True


def _shown_items(tree: SectionTree) -> list[tuple[QTreeWidgetItem, SourceRow]]:
    """Return the items of the rows *tree* lists, top to bottom, with their widgets."""
    items = []
    iterator = QTreeWidgetItemIterator(tree)
    while (item := iterator.value()) is not None:
        if item.parent() is not None and _is_shown(item):
            widget = tree.itemWidget(item, 0)
            assert isinstance(widget, SourceRow), "a listed row has its widget"
            items.append((item, widget))
        iterator += 1
    return items


def shown_rows(tree: SectionTree) -> list[SourceRow]:
    """Return the rows *tree* lists, top to bottom."""
    return [widget for _item, widget in _shown_items(tree)]


def expand(tree: SectionTree, title: str) -> None:
    """Expand the listed row named *title*, as its branch arrow does."""
    [item] = [item for item, widget in _shown_items(tree) if row_texts(widget)[0] == title]
    item.setExpanded(True)


def titles(tree: SectionTree) -> list[str]:
    """Return the names of the rows *tree* lists, top to bottom."""
    return [row_texts(row)[0] for row in shown_rows(tree)]


def row_texts(row: ListRow) -> list[str]:
    """Return the name a list row shows, and its detail line when it has one."""
    return [label.full_text() for label in row.findChildren(ElidedLabel)]


def row_action(row: SourceRow) -> str:
    """Return the text of a row's action button, or its hover text for an icon-only one; empty without one."""
    return "".join(button.text() or button.toolTip() for button in row.findChildren(SmallButton))


def row(tree: SectionTree, title: str) -> SourceRow:
    """Return the listed row named *title*."""
    [found] = [row for row in shown_rows(tree) if row_texts(row)[0] == title]
    return found


def press(tree: SectionTree, title: str) -> None:
    """Press the action of the listed row named *title*."""
    [button] = row(tree, title).findChildren(SmallButton)
    button.click()


def double_click(tree: SectionTree, title: str) -> None:
    """Double-click the listed row named *title*, once the tree has placed the row widgets it just showed."""
    QApplication.processEvents()
    widget = row(tree, title)
    position = QPoint(widget.width() // 3, widget.height() // 2)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=position)


def _section(tree: SectionTree, title: str) -> QTreeWidgetItem:
    """Return the item of the section titled *title*."""
    headings = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    [found] = [
        heading
        for heading in headings
        if heading is not None and str(heading.data(0, Qt.ItemDataRole.AccessibleTextRole)).startswith(f"{title}, ")
    ]
    return found


def caption(tree: SectionTree, title: str) -> SectionCaption:
    """Return the caption shown over the section titled *title*."""
    widget = tree.itemWidget(_section(tree, title), 0)
    assert widget is not None, "a section has its caption"
    found = widget if isinstance(widget, SectionCaption) else widget.findChild(SectionCaption)
    assert found is not None
    return found


def click_caption(tree: SectionTree, title: str) -> None:
    """Click the caption of the section titled *title*, once the tree has placed it."""
    QApplication.processEvents()
    widget = caption(tree, title)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=widget.rect().center())


def is_folded(tree: SectionTree, title: str) -> bool:
    """Return whether the section titled *title* is folded, so none of its rows is listed."""
    return not _section(tree, title).isExpanded()


def captions(tree: SectionTree) -> list[str]:
    """Return what a screen reader says for each section *tree* shows: its title and row count."""
    headings = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    return [
        str(heading.data(0, Qt.ItemDataRole.AccessibleTextRole))
        for heading in headings
        if heading is not None and not heading.isHidden()
    ]


def texts(widget: QWidget) -> list[str]:
    """Return the text of every label shown in *widget*, captions' empty texts included."""
    return [label.text() for label in widget.findChildren(QLabel) if label.isVisibleTo(widget) and label.text()]
