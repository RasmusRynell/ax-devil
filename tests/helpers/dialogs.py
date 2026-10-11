"""Dialog lookups shared by chrome tests."""

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea


def content_scroll(dialog: BaseDialog) -> ContentScrollArea:
    """Return the area that scrolls a dialog's content while its actions stay put."""
    area = dialog.findChild(ContentScrollArea)
    assert area is not None
    return area
