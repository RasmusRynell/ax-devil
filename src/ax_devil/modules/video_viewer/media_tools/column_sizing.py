"""How wide the columns of a ``RowTableView`` are: measuring, hiding what does not fit, and the user's own widths."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtGui import QFontMetrics

from ax_devil.modules.chrome.copy_cell import copy_column_px
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.video_viewer.media_tools.row_columns import (
    COMFORTABLE_CHARS,
    LEAST_CHARS,
    TEXT_ROLES,
    CellKind,
    cell_pad,
)
from ax_devil.modules.video_viewer.media_tools.row_model import RowTableModel
from ax_devil.modules.video_viewer.media_tools.row_painting import RowPainter

# The widths columns were given by dragging, per table key and column title, for the rest of the session.
_REMEMBERED_WIDTHS: dict[str, dict[str, int]] = {}


@dataclass(frozen=True, slots=True)
class _ColumnFit:
    """What a column's width depends on besides its values, measured for the current fonts."""

    metrics: QFontMetrics | None  # The face of its text; ``None`` for a kind without a single text.
    chrome: int  # The width of a cell spent on anything but its text.
    title: int  # The width of the whole header title.
    comfortable: int  # The width of its text before other columns are hidden, if the values are long enough.
    minimum: int  # The width of its fewest characters.


class ColumnSizer:
    """Decides which columns of a table are shown and how wide, from the model, the fonts and the user's drags.

    ``measure_columns`` and ``measure_rows`` take the measurements; ``widths`` turns them into the width of every
    column that fits. With a *table_key*, widths the user gave are remembered for the session by every sizer of that
    key.
    """

    def __init__(self, model: RowTableModel, painter: RowPainter, table_key: str | None) -> None:
        self._model = model
        self._painter = painter
        self._fits: list[_ColumnFit] = []  # Per column: what its width depends on besides its values.
        self._minimum: list[int] = []  # Per column: the narrowest it is for the minimum size; independent of the rows.
        self._natural: list[int] = []  # Per column: its width for its longest visible value.
        self._floor: list[int] = []  # Per column: the narrowest it is before other columns are hidden.
        self._least: list[int] = []  # Per column: the narrowest it is shrunk to for its rows.
        self._empty: list[bool] = []  # Per column: whether it shows nothing in any visible row.
        self._tail = -1  # The last shown column; wider by the painter's tail_px, to keep its text off the right edge.
        self._user_widths = _REMEMBERED_WIDTHS.setdefault(table_key, {}) if table_key is not None else {}

    @property
    def measured(self) -> bool:
        """Whether the columns have been measured."""
        return bool(self._minimum)

    def minimum_width(self) -> int:
        """Return the width of the columns that are never hidden, at their narrowest, with the painter's tail.

        It depends on the columns and the text size only, never on the rows.
        """
        never_hidden = [
            self._minimum[section]
            for section, column in enumerate(self._model.columns)
            if column.priority == 0 or column.stretch or column.cell is CellKind.COPY
        ]
        return sum(never_hidden) + self._painter.tail_px

    def measure_columns(self) -> None:
        """Measure what a column's width depends on besides its values: the width of its title, and its narrowest
        width for the minimum size, which comes from the column definitions and fonts alone: *width_chars* characters
        or the title, whichever is wider. Values never enter it, so it does not change as rows come and go.
        """
        caption = QFontMetrics(TextRole.CAPTION.font())
        self._fits, self._minimum = [], []
        for section, column in enumerate(self._model.columns):
            if column.cell is CellKind.COPY:
                self._fits.append(_ColumnFit(None, 0, 0, 0, copy_column_px()))
                self._minimum.append(copy_column_px())
                continue
            chrome = self._painter.chrome_px(column.cell)
            # The whole title, and the arrow of a sortable column, with a pixel of slack against eliding.
            title = caption.horizontalAdvance(column.title.upper()) + 1
            if column.sort_key is not None:
                title += TextRole.CAPTION.px + Space.XS
            least_chars = column.width_chars if column.width_chars is not None else LEAST_CHARS[column.cell]
            role = TEXT_ROLES.get(column.cell, TextRole.MONO)
            metrics = QFontMetrics(role.font())
            char = metrics.horizontalAdvance("0") if role is TextRole.MONO else metrics.averageCharWidth()
            # One pixel of slack: Qt elides text whose advance is exactly the width it is given.
            least = math.ceil(least_chars * char) + chrome + 1
            comfortable = math.ceil(max(COMFORTABLE_CHARS[column.cell], least_chars) * char) + chrome + 1
            self._fits.append(
                _ColumnFit(metrics if column.cell in TEXT_ROLES else None, chrome, title, comfortable, least)
            )
            self._minimum.append(max(least, title) + cell_pad(section))

    def measure_rows(self, rows: range) -> bool:
        """Measure the columns for *rows*: their width for the longest value, the width below which other columns are
        hidden, the narrowest each is shrunk to, and whether it shows nothing at all. Return whether any of that
        changed, so the columns are fitted again only then and do not move while rows scroll past at the same widths.
        """
        if not self._fits:
            return False
        before = (self._natural, self._floor, self._least, self._empty)
        self._natural, self._floor, self._least, self._empty = [], [], [], []
        for section, (column, fit) in enumerate(zip(self._model.columns, self._fits)):
            if column.cell is CellKind.COPY:
                for sizes in (self._natural, self._floor, self._least):
                    sizes.append(fit.minimum)
                self._empty.append(False)
                continue
            comfortable = fit.comfortable
            if fit.metrics is None:
                longest = comfortable
            else:
                texts = self._model.column_texts(section, rows)
                longest = max((fit.metrics.horizontalAdvance(text) + 1 for text in texts), default=0) + fit.chrome
                if column.priority > 0 and not column.stretch:  # Shown whole, or hidden.
                    comfortable = longest
            pad = cell_pad(section)
            self._natural.append(max(longest, fit.title) + pad)
            self._floor.append(max(min(comfortable, longest), fit.title) + pad)
            self._least.append(min(self._minimum[section], self._floor[-1]))
            self._empty.append(self._model.is_blank(section, rows))
        return (self._natural, self._floor, self._least, self._empty) != before

    def blanks_changed(self, rows: range) -> bool:
        """Return whether a column has started or stopped showing nothing in *rows* since they were measured."""
        return any(self._model.is_blank(section, rows) != blank for section, blank in enumerate(self._empty))

    def real(self, shown: Sequence[int]) -> list[int]:
        """Return the sections among *shown* that are not the copy column."""
        return [section for section in shown if self._model.columns[section].cell is not CellKind.COPY]

    def widths(self, available: int) -> dict[int, int]:
        """Return the width of every column that is shown in *available* pixels; the others are hidden.

        Columns that show nothing go first, then those with the highest priority number, until the rest fit at their
        comfortable width. A column the user gave a width keeps it as long as it fits; when it does not, columns
        without one shrink first, then the others. The saved width is only a preference and returns with room.
        """
        columns = self._model.columns
        copy = self._model.copy_section
        shown = list(range(len(columns)))

        def wanted() -> int:
            absorber = self._absorber_of(shown)
            return sum(self._floor_of(s) if s in (copy, absorber) else self._pref(s) for s in shown)

        def point_tail() -> None:
            self._tail = self.real(shown)[-1]

        def hide(candidates: list[int]) -> None:
            shown.remove(max(candidates, key=lambda section: (columns[section].priority, section)))
            point_tail()

        def hideable() -> list[int]:
            return [s for s in shown if columns[s].priority > 0 and not columns[s].stretch and s != copy]

        point_tail()
        while wanted() > available and any(self._empty[s] for s in hideable()):
            hide([s for s in hideable() if self._empty[s]])
        while sum(self._floor_of(s) for s in shown) > available and hideable():
            hide(hideable())
        copy_px = copy_column_px() if copy in shown else 0
        absorber = self._absorber_of(shown)
        real = self.real(shown)
        floors = {section: self._floor_of(section) for section in real}
        if sum(floors.values()) > available - copy_px:  # Nothing is left to hide.
            least = {section: self._least_of(section) for section in real}
            widths = self._fit(floors, available - copy_px, least)
        else:
            others = [section for section in real if section != absorber]
            own = {section: self._pref(section) for section in others if columns[section].title in self._user_widths}
            spare = available - copy_px - floors[absorber]
            auto = self._fit(
                {section: self._pref(section) for section in others if section not in own},
                spare - sum(own.values()),
                floors,
            )
            widths = {**auto, **self._fit(own, spare - sum(auto.values()), floors), absorber: floors[absorber]}
        widths[absorber] += max(0, available - copy_px - sum(widths.values()))
        if copy in shown:
            widths[copy] = copy_column_px()
        return widths

    def drag(self, section: int, old: int, new: int, shown: list[int], available: int) -> None:
        """Remember the width the user dragged *section* to, as far as the other *shown* columns leave room in
        *available* pixels: only the width that could be applied, and nothing if there was no room to change it.
        """
        columns = self._model.columns
        title = columns[section].title
        saved = self._user_widths.get(title)
        self._user_widths[title] = new  # Provisionally, to know which column then takes the leftover.
        copy_px = copy_column_px() if self._model.copy_section in shown else 0
        reserved = copy_px + sum(self._floor_of(other) for other in self.real(shown) if other != section)
        applied = max(self._least[section], min(new, available - reserved))
        if applied == old and saved is None:
            del self._user_widths[title]
        else:
            self._user_widths[title] = applied

    def fit_to_content(self, section: int) -> None:
        """Make *section* as wide as its content."""
        self._user_widths[self._model.columns[section].title] = self._natural[section]

    def _absorber_of(self, shown: Sequence[int]) -> int:
        """Return the shown column that takes the room the others leave: the first stretch column that has no width
        of its own, else the last column.
        """
        columns = self._model.columns
        real = self.real(shown)
        free = (
            section for section in real if columns[section].stretch and columns[section].title not in self._user_widths
        )
        return next(free, real[-1])

    def _pref(self, section: int) -> int:
        """Return the width *section* would like: the one the user gave it, else the width of its content."""
        tail = self._painter.tail_px if section == self._tail else 0
        saved = self._user_widths.get(self._model.columns[section].title)
        # A width saved at another text size may be below what the column now needs at the very least.
        return self._natural[section] + tail if saved is None else max(saved, self._least[section] + tail)

    def _floor_of(self, section: int) -> int:
        """Return the width below which *section* is not shrunk while other columns can be hidden."""
        return min(self._floor[section] + (self._painter.tail_px if section == self._tail else 0), self._pref(section))

    def _least_of(self, section: int) -> int:
        """Return the narrowest *section* is ever shrunk to."""
        return min(self._least[section] + (self._painter.tail_px if section == self._tail else 0), self._pref(section))

    def _fit(self, preferred: dict[int, int], room: int, floors: dict[int, int]) -> dict[int, int]:
        """Return *preferred* widths, or all shrunk toward their *floors* in proportion to what they can give up if
        they do not fit in *room*.
        """
        wanted = sum(preferred.values())
        if wanted <= room:
            return preferred
        bottom = sum(floors[section] for section in preferred)
        slack = max(0, room - bottom)
        return {
            section: floors[section] + (width - floors[section]) * slack // max(1, wanted - bottom)
            for section, width in preferred.items()
        }
