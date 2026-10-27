"""The rows and columns the Objects page lists, and what a query searches in them."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from ax_devil.modules.data_sources.scene_history import ObjectHistory
from ax_devil.modules.filtering.measure_range import Measure
from ax_devil.modules.scene.inspection import (
    EntitySummary,
    entity_record,
    entity_summary,
    latest_primary_classification,
)
from ax_devil.modules.scene.model import Classification, Entity
from ax_devil.modules.scene.query import Query, QueryRecord
from ax_devil.modules.video_viewer.media_tools.object_rows import id_order, seen_range
from ax_devil.modules.video_viewer.media_tools.page_stack import ObjectPageKey
from ax_devil.modules.video_viewer.media_tools.row_columns import CellKind, Column, Labeled

# Paths every record has, whatever the scope, so a query that names only these never needs the entity's full fields.
_ID = "id"


_CLASS = "class"


_EVENT = "event"


LIGHT_PATHS = frozenset({_ID, _CLASS})


FILE_PATHS = LIGHT_PATHS | {_EVENT}


def row_key(row: Any) -> ObjectPageKey:
    """Identify a row by the page it opens."""
    return ObjectPageKey(row.entity_id)


def query_paths(query: Query) -> frozenset[str]:
    """Return the lower-cased field paths the query names."""
    return frozenset(term.path.lower() for group in query.groups for term in group if term.path is not None)


class FrameRow:
    """An object of the displayed frame; its searchable record is built when a query first asks for it."""

    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.entity_id = str(entity.id)

    @cached_property
    def summary(self) -> EntitySummary:
        """Build visible-row display details only when requested."""
        return entity_summary(self.entity)

    @cached_property
    def primary(self) -> Classification | None:
        """Read what score and confidence ordering needs without formatting a row."""
        return latest_primary_classification(self.entity)

    def record(self, *, full: bool) -> QueryRecord:
        """Return what a query searches: id, class and state, plus every field of the entity when *full*."""
        return self._full_record if full else self._light_record

    @cached_property
    def _light_record(self) -> QueryRecord:
        summary = self.summary
        fields = {_ID: (summary.entity_id,), _CLASS: (summary.class_name,)}
        state = () if summary.state is None else (summary.state,)
        return QueryRecord(fields=fields, text=(summary.entity_id, summary.class_name, *state))

    @cached_property
    def _full_record(self) -> QueryRecord:
        record = entity_record(self.entity)
        return QueryRecord(fields={**record.fields, **self._light_record.fields}, text=record.text)


class FileRow:
    """An object of the whole file, searchable by id, class and the kinds of events it is in."""

    def __init__(self, history: ObjectHistory, event_kinds: tuple[str, ...]) -> None:
        self.history = history
        self.entity_id = history.entity_id
        self.record = QueryRecord(
            fields={_ID: (history.entity_id,), _CLASS: history.types, _EVENT: event_kinds},
            text=(history.entity_id, *history.types),
        )

    @property
    def category(self) -> str:
        """Return the class that colors the object."""
        return self.history.types[0] if self.history.types else ""

    @cached_property
    def seen(self) -> str:
        """Return when the object is first and last shown."""
        return seen_range(self.history)


def _order(measure: Measure, row: FrameRow) -> tuple[bool, float]:
    """Sort a missing value lowest."""
    value = measure.of(row.primary) if row.primary is not None else None
    return (value is not None, value or 0.0)


def frame_columns() -> tuple[Column, ...]:
    return (
        Column(
            "ID",
            CellKind.ID_DOT,
            lambda row: Labeled(row.entity_id, row.summary.class_name),
            lambda row: id_order(row.entity_id),
        ),
        Column(
            "Class",
            CellKind.TEXT,
            lambda row: row.summary.class_name,
            lambda row: row.summary.class_name.casefold(),
            stretch=True,
        ),
        Column(
            "Score",
            CellKind.NUMBER,
            lambda row: row.summary.score.text if row.summary.score is not None else "",
            lambda row: _order(Measure.SCORE, row),
        ),
        Column(
            "Conf",
            CellKind.NUMBER,
            lambda row: row.summary.confidence.text if row.summary.confidence is not None else "",
            lambda row: _order(Measure.CONFIDENCE, row),
        ),
        Column(
            "Motion",
            CellKind.TEXT,
            lambda row: row.summary.state or "",
            lambda row: row.summary.state or "",
            priority=1,
        ),
    )
