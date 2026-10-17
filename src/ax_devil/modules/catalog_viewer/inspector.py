"""A read-only panel with the generated attributes of the sheet object the user clicked."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.scene.inspection import entity_summary
from ax_devil.modules.scene.model import Entity
from ax_devil.modules.video_viewer.media_tools.fields_view import FieldsView, field_sections
from ax_devil.modules.video_viewer.media_tools.object_page import TitleRow

_WIDTH_IN_BODY_TEXT = 24
_HINT = "Click an object to read its attributes. Select a row and press Ctrl+C to copy it."


class ObjectInspector(QFrame):
    """The title row and grouped fields of one example object, or a hint while none is selected.

    The panel keeps its width whether or not an object is selected, so a click never moves the sheet under the pointer.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the panel with nothing selected."""
        super().__init__(parent)
        self.setObjectName("catalogViewerInspector")
        self.setStyleSheet("#catalogViewerInspector { border-left: 1px solid palette(mid); }")
        self._title = TitleRow("", self)
        self._fields = FieldsView({}, self)
        self._hint = QLabel(_HINT, self)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(placeholder-text);")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._hint.setContentsMargins(Space.M, Space.M, Space.M, 0)
        TextRole.SMALL.apply(self._hint)
        self.setFocusProxy(self._fields)  # Ctrl+C copies the selected field once the panel has focus.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._title)
        layout.addWidget(self._fields, 1)
        layout.addWidget(self._hint, 1)
        follow_appearance(self, lambda: self.setFixedWidth(TextRole.BODY.px * _WIDTH_IN_BODY_TEXT))
        self.show_entity(None)

    def show_entity(self, entity: Entity | None) -> None:
        """Show *entity*'s fields, or the hint for None."""
        self._title.setVisible(entity is not None)
        self._fields.setVisible(entity is not None)
        self._hint.setVisible(entity is None)
        if entity is None:
            self._fields.set_sections(())
            return
        summary = entity_summary(entity)
        self._title.set_entity_id(summary.entity_id)
        self._title.set_content(summary.class_name, summary.state or "", summary.state, dimmed=False)
        self._fields.set_sections(field_sections(entity))
