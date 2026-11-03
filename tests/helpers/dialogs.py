"""Dialog lookups shared by chrome tests."""

from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea


def content_scroll(dialog: BaseDialog) -> ContentScrollArea:
    """Return the area that scrolls a dialog's content while its actions stay put."""
    area = dialog.findChild(ContentScrollArea)
    assert area is not None
    return area


def visible_texts(dialog: QWidget) -> list[str]:
    """Return the text of every label the user can currently see in *dialog*."""
    return [label.text() for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog)]
