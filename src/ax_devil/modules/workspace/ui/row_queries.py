"""Questions about trees of browser rows that the section tree asks when sections change or a filter applies."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow, WorkspaceBrowserSection

RowPredicate = Callable[[WorkspaceBrowserRow], bool]


def sections_updatable(old: Sequence[WorkspaceBrowserSection], new: Sequence[WorkspaceBrowserSection]) -> bool:
    """Return whether a tree showing *old* can become *new* by adding and removing rows alone: the same section kinds in
    the same order, and under every section and every kept row, unique ids with the ids of both in the same order."""
    return [section.kind for section in old] == [section.kind for section in new] and all(
        _rows_updatable(before.rows, after.rows) for before, after in zip(old, new, strict=True)
    )


def _rows_updatable(old: Sequence[WorkspaceBrowserRow], new: Sequence[WorkspaceBrowserRow]) -> bool:
    """Return whether *new* follows *old* as ``sections_updatable`` asks, here and under each row they share."""
    ids = [row.row_id for row in new]
    shown = set(ids)
    before = {row.row_id: row for row in old}
    if len(shown) != len(ids) or [row.row_id for row in old if row.row_id in shown] != [i for i in ids if i in before]:
        return False
    return all(_rows_updatable(before[row.row_id].children, row.children) for row in new if row.row_id in before)


def holds_open_row(rows: Sequence[WorkspaceBrowserRow]) -> bool:
    """Return whether any of *rows*, or a row under them, is open in a viewer."""
    return any(row.is_open or holds_open_row(row.children) for row in rows)


def count_shown(
    rows: Sequence[WorkspaceBrowserRow],
    allowed: RowPredicate,
    matches: RowPredicate | None,
    *,
    ancestor_matches: bool = False,
) -> int:
    """Return how many of *rows* and the rows under them a filter of *allowed* and *matches* shows, as ``set_filter``
    does: a row is shown when *allowed* passes it and its parent is shown, and, with *matches*, when it matches, sits
    under a match, or holds one."""
    count = 0
    for row in rows:
        row_matches = matches is None or matches(row)
        below = count_shown(row.children, allowed, matches, ancestor_matches=ancestor_matches or row_matches)
        if allowed(row) and (matches is None or ancestor_matches or row_matches or below > 0):
            count += 1 + below
    return count
