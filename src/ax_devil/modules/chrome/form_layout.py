"""Shared content-driven layout for labeled input fields."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QWidget


class FormLayout(QFormLayout):
    """Keep rows compact, grow fields, and wrap labels on narrow screens."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Containers keep Qt's style-provided padding; nested layouts add none.
        if parent is None:
            self.setContentsMargins(0, 0, 0, 0)
        self.setHorizontalSpacing(12)
        self.setVerticalSpacing(3)
        self.setFormAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)


def align_label_columns(*forms: QFormLayout) -> None:
    """Give separate forms one label column, so stacked option pages line up with the rows around them."""
    items = [form.itemAt(row, QFormLayout.ItemRole.LabelRole) for form in forms for row in range(form.rowCount())]
    labels = [label for item in items if item is not None and (label := item.widget()) is not None]
    width = max((label.sizeHint().width() for label in labels), default=0)
    for label in labels:
        label.setMinimumWidth(width)
