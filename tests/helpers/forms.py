"""Find form controls by the label the user sees next to them."""

from typing import TypeVar

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

_W = TypeVar("_W", bound=QWidget)


def form_field(parent: QWidget, label: str, kind: type[_W]) -> _W:
    """Return the *kind* widget in the first form row inside *parent* that the user sees labeled *label*."""
    for form in parent.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            label_widget = label_item.widget() if label_item is not None else None
            if not isinstance(label_widget, QLabel) or label_widget.text() != label or field_item is None:
                continue
            widget = field_item.widget()
            assert widget is not None
            found = widget if isinstance(widget, kind) else widget.findChild(kind)
            assert found is not None, f"{label!r} has no {kind.__name__}"
            return found
    raise LookupError(f"No form row labeled {label!r}")
