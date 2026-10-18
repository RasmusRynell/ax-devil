"""Small widgets the rendering diagnostics pages are built from."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from ax_devil.modules.chrome.tokens import Space


def selectable_label(text: str = "") -> QLabel:
    """Return a wrapping plain-text label whose text can be selected with the mouse."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def detail_rows(parent: QWidget, names: tuple[str, ...]) -> dict[str, QLabel]:
    """Lay out a name and a value label per *names* in *parent*; return the value labels by name."""
    layout = QFormLayout(parent)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(Space.XL)
    layout.setVerticalSpacing(Space.S)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    labels = {}
    for name in names:
        value = selectable_label("—")
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addRow(selectable_label(name), value)
        labels[name] = value
    return labels


def add_disclosure(layout: QVBoxLayout, title: str, content: QWidget) -> None:
    """Add a button to *layout* that shows and hides *content*, which starts hidden."""
    button = QToolButton()
    button.setText(title)
    button.setCheckable(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setArrowType(Qt.ArrowType.RightArrow)
    content.hide()

    def toggle(expanded: bool) -> None:
        content.setVisible(expanded)
        button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    button.toggled.connect(toggle)
    layout.addWidget(button)
    layout.addWidget(content)
