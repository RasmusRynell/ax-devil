"""The sections of the fields tree and the model that holds them, so new values repaint rows in place."""

from __future__ import annotations

import json
from collections.abc import Callable, Hashable, Sequence
from copy import copy
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.object_inspection.pinned_fields import FieldId, PinnedFields
from ax_devil.modules.object_inspection.rows import EventRow, Row, field_rows, unavailable_row
from ax_devil.modules.scene.inspection import FieldGroup, InspectionField, entity_fields
from ax_devil.modules.scene.model import Entity

EVENTS_SECTION = "Events"
PINNED_SECTION = "Pinned"

_ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@dataclass(frozen=True, slots=True)
class Section:
    """A captioned, foldable run of rows; *name* is the caption and the key its fold state is remembered by.

    A section with *copy* has a copy icon in its caption that copies what *copy* returns.
    """

    name: str
    rows: tuple[Row, ...]
    folded_by_default: bool = False
    copy: Callable[[], str] | None = None

    @property
    def count(self) -> int:
        """Return the number of the rows it counts, leaving out headings."""
        return sum(row.counted for row in self.rows)


def field_sections(entity: Entity) -> tuple[Section, ...]:
    """Return the entity's populated field groups as sections, in display order; Raw copies as JSON."""
    return tuple(
        Section(
            group.title,
            tuple(field_rows(items, group.title)),
            group.folded_by_default,
            (lambda items=items: _json_of(items)) if group is FieldGroup.RAW else None,
        )
        for group, items in entity_fields(entity)
    )


def pinned_section(sections: Sequence[Section], pins: PinnedFields) -> Section | None:
    """Return the pinned fields as a section, in the order they were pinned, or ``None`` if nothing is pinned.

    Each takes its value from the first row of *sections* with that field id, named by its full ``group.path``; a
    field the object does not have shows as unavailable, which is every field when the object is not in the frame.
    """
    if not len(pins):
        return None
    found: dict[FieldId, Row] = {}
    for section in sections:
        for row in section.rows:
            if row.field_id is not None:
                found.setdefault(row.field_id, row)
    rows: list[Row] = []
    for field_id in pins:
        found_row = found.get(field_id)
        if found_row is None:
            rows.append(unavailable_row(field_id))
        else:
            pinned = copy(found_row)
            pinned.label = field_id.label
            rows.append(pinned)
    return Section(PINNED_SECTION, tuple(rows))


def _json_of(items: Sequence[InspectionField]) -> str:
    """Return *items* as a JSON object keyed by their dotted paths."""
    return json.dumps({".".join(item.path): item.value for item in items}, indent=2)


def event_section(events: Sequence[FrameEvent]) -> Section:
    """Return *events* as the Events section."""
    return Section(EVENTS_SECTION, tuple(EventRow(event) for event in events))


class FieldsModel(QAbstractItemModel):
    """Sections as top-level rows and their rows as children, in one column; the lead rows come first at the top."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lead: list[Row] = []
        self._sections: list[Section] = []
        self.structure = 0  # Changes whenever the set of rows does, so what is measured from them can be reused.

    def sections(self) -> tuple[Section, ...]:
        """Return the sections."""
        return tuple(self._sections)

    def section_index(self, position: int) -> QModelIndex:
        """Return the index of the caption of the section at *position*."""
        return self.index(position + len(self._lead), 0)

    def rows(self) -> list[Row]:
        """Return every row, the lead rows first."""
        return [*self._lead, *(row for section in self._sections for row in section.rows)]

    def set_sections(self, sections: Sequence[Section], lead: Sequence[Row] = ()) -> bool:
        """Show *sections* under the *lead* rows; return whether the model reset, which collapses every section in a
        view.

        The same sections with the same row keys only repaint. A section whose rows changed swaps just its own rows.
        """
        new = list(sections)
        if [section.name for section in new] != [section.name for section in self._sections] or [
            row.key for row in lead
        ] != [row.key for row in self._lead]:
            self.beginResetModel()
            self._lead, self._sections = list(lead), new
            self.structure += 1
            self.endResetModel()
            return True
        if self._lead:
            self._lead = list(lead)
            self.dataChanged.emit(self.index(0, 0), self.index(len(self._lead) - 1, 0))
        for position, section in enumerate(new):
            self._update_section(position, section)
        return False

    def _update_section(self, position: int, section: Section) -> None:
        old = self._sections[position]
        parent = self.section_index(position)
        if [row.key for row in old.rows] == [row.key for row in section.rows]:
            self._sections[position] = section
            if section.rows:
                self.dataChanged.emit(self.index(0, 0, parent), self.index(len(section.rows) - 1, 0, parent))
            return
        self.structure += 1
        if old.rows:
            self.beginRemoveRows(parent, 0, len(old.rows) - 1)
            self._sections[position] = Section(old.name, (), old.folded_by_default, old.copy)
            self.endRemoveRows()
        if section.rows:
            self.beginInsertRows(parent, 0, len(section.rows) - 1)
            self._sections[position] = section
            self.endInsertRows()
        else:
            self._sections[position] = section
        self.dataChanged.emit(parent, parent)

    def section_at(self, index: QModelIndex | QPersistentModelIndex) -> Section | None:
        """Return the section whose caption is at *index*, or ``None`` if *index* is not a caption."""
        if not index.isValid() or index.internalId() != 0 or index.row() < len(self._lead):
            return None
        return self._sections[index.row() - len(self._lead)]

    def row_at(self, index: QModelIndex | QPersistentModelIndex) -> Row | None:
        """Return the row at *index*, or ``None`` if *index* is not a row."""
        if not index.isValid():
            return None
        if index.internalId() == 0:
            return self._lead[index.row()] if index.row() < len(self._lead) else None
        return self._sections[index.internalId() - 1].rows[index.row()]

    def has_copy_at(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        """Return whether the row or caption at *index* has a copy icon, without making the text it would copy."""
        section = self.section_at(index)
        row = self.row_at(index)
        return (row is not None and row.copyable) or (section is not None and section.copy is not None)

    def copy_text_at(self, index: QModelIndex | QPersistentModelIndex) -> str | None:
        """Return what copying the row or caption at *index* puts on the clipboard, or ``None`` if it has no copy."""
        row, section = self.row_at(index), self.section_at(index)
        if row is not None:
            return row.copy_text() if row.copyable else None
        return section.copy() if section is not None and section.copy is not None else None

    def copy_key_at(self, index: QModelIndex | QPersistentModelIndex) -> Hashable:
        """Return what tells the row or caption at *index* from the others, such as across the objects of a page, and
        what copying it would put on the clipboard, so a copy mark ends when the value it copied changes. A caption's
        content is that of its rows, which is cheaper than building what it copies.
        """
        row, section = self.row_at(index), self.section_at(index)
        if row is not None:
            parent = self.section_at(index.parent())
            return (parent.name if parent is not None else "", row.key, row.copy_text())
        if section is None:
            return None
        return (section.name, None, tuple(row.copy_text() for row in section.rows) if section.copy else None)

    def index(  # noqa: D102
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        if parent is None or not parent.isValid():
            return self.createIndex(row, 0, 0) if row < len(self._lead) + len(self._sections) else QModelIndex()
        section = parent.row() - len(self._lead)
        if parent.internalId() != 0 or section < 0 or row >= len(self._sections[section].rows):
            return QModelIndex()
        return self.createIndex(row, 0, section + 1)

    def parent(self, child: QModelIndex | QPersistentModelIndex | None = None) -> Any:  # noqa: D102
        if child is None:
            return super().parent()
        if not child.isValid() or child.internalId() == 0:
            return QModelIndex()
        return self.createIndex(child.internalId() - 1 + len(self._lead), 0, 0)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        if parent is None or not parent.isValid():
            return len(self._lead) + len(self._sections)
        section = self.section_at(parent)
        return len(section.rows) if section is not None else 0

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802, D102
        return 1

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: D102
        flags = Qt.ItemFlag.ItemIsEnabled
        return flags | Qt.ItemFlag.ItemIsSelectable if self.row_at(index) is not None else flags

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:  # noqa: D102
        section, row = self.section_at(index), self.row_at(index)
        if role == _ITEM_ROLE:
            return section or row
        if role == int(Qt.ItemDataRole.DisplayRole):
            return section.name if section is not None else row.copy_text() if row is not None else None
        if role == int(Qt.ItemDataRole.ToolTipRole) and row is not None:
            return row.copy_text()  # Rows elide long keys, values and labels; the tooltip has them whole.
        return None
