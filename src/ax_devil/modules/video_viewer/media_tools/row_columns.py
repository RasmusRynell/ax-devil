"""What a row table column is: its cell kind, how to read it, and the widths and text roles per kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from PySide6.QtCore import (
    Qt,
)

from ax_devil.modules.chrome.tokens import Space, TextRole

# The fewest characters of an id left when ids are shortened, before one is left out for "+n": the ellipsis and four
# visible characters on each side.
MIN_ID_CHARS = 9


class CellKind(Enum):
    """How a column's cells are painted, and so what its ``value`` returns.

    ``TEXT`` and ``MONO`` take a ``str``, ``NUMBER`` a ``str`` shown right-aligned in the monospace face, ``ID_DOT`` and
    ``TAG`` a ``Labeled``, ``ID_CHIPS`` a sequence of ``Labeled`` shown as plain ids, ``PRESENCE`` a ``Presence``.
    ``COPY`` is the copy icon the table adds itself (see ``RowTableModel``).
    """

    ID_DOT = auto()
    TEXT = auto()
    MONO = auto()
    NUMBER = auto()
    PRESENCE = auto()
    TAG = auto()
    ID_CHIPS = auto()
    COPY = auto()

    def text_of(self, value: Any) -> str | None:
        """Return the text of a cell of this kind holding *value*, or ``None`` if the cell shows no single text."""
        if self in (CellKind.ID_DOT, CellKind.TAG):
            return str(value.text)
        if self is CellKind.ID_CHIPS:
            return ", ".join(label.text for label in value)
        return str(value) if self in (CellKind.TEXT, CellKind.MONO, CellKind.NUMBER) else None


@dataclass(frozen=True, slots=True)
class Labeled:
    """A *text* colored by the *category* it belongs to, such as an object's id by its class or an event's kind."""

    text: str
    category: str


@dataclass(frozen=True, slots=True)
class Column:
    """One column: its header *title*, how its cells are painted, and how to read them from a row.

    *value* returns what *cell* paints. *sort_key* makes the column sortable by the header, and returns values that
    compare with each other across rows; ``None`` leaves it unsortable.

    A column is as wide as its longest visible value. A *stretch* column (the last one if none is) takes the room the
    others leave. When there is not enough room, text is elided at the end, or in the middle for ids and when *elide*
    says so, but only down to a comfortable width (20 characters for an id) while columns with a *priority* number above
    0 are still shown: those are hidden first, the highest number first, and a column that is empty in every visible row
    before that. Only when nothing is left to hide do columns shrink further, down to *width_chars* monospace characters
    (a default for their cell kind if ``None``).
    """

    title: str
    cell: CellKind
    value: Callable[[Any], Any]
    sort_key: Callable[[Any], Any] | None = None
    stretch: bool = False
    width_chars: int | None = None
    priority: int = 0
    elide: Qt.TextElideMode | None = None


# How many monospace characters of a cell are shown before columns are hidden, and at the very least (less if the
# values are shorter). A header title is never elided.
COMFORTABLE_CHARS = {
    CellKind.ID_DOT: 20,
    CellKind.TEXT: 10,
    CellKind.MONO: 10,
    CellKind.NUMBER: 6,
    CellKind.PRESENCE: 12,
    CellKind.TAG: 10,
    CellKind.ID_CHIPS: 14,
}
LEAST_CHARS = {
    CellKind.ID_DOT: 10,
    CellKind.TEXT: 8,
    CellKind.MONO: 6,
    CellKind.NUMBER: 4,
    CellKind.PRESENCE: 4,
    CellKind.TAG: 6,
    CellKind.ID_CHIPS: MIN_ID_CHARS + 4,  # An id, then room for ", +n".
}
# The text role of the kinds that show a single text.
TEXT_ROLES = {
    CellKind.TEXT: TextRole.BODY,
    CellKind.MONO: TextRole.MONO,
    CellKind.NUMBER: TextRole.MONO,
    CellKind.ID_DOT: TextRole.MONO,
    CellKind.ID_CHIPS: TextRole.MONO,
    CellKind.TAG: TextRole.SMALL,
}
RIGHT_ALIGNED = frozenset({CellKind.NUMBER})


def elide_mode(column: Column) -> Qt.TextElideMode:
    """Return where *column*'s text is cut: in the middle for ids, unless the column says otherwise."""
    if column.elide is not None:
        return column.elide
    return Qt.TextElideMode.ElideMiddle if column.cell is CellKind.ID_DOT else Qt.TextElideMode.ElideRight


def cell_pad(section: int) -> int:
    """Return the horizontal padding of a cell in *section*, both sides together."""
    return (Space.M if section == 0 else Space.S) + Space.S
