"""Hints that tell sibling browser rows with the same label apart."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePath

from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserRow


def _distinguishing_hints(rows: Sequence[WorkspaceBrowserRow], file_paths: dict[str, PurePath]) -> list[str]:
    """Return a hint per location; repeated locations are numbered in order of appearance."""
    locations = [row.location or "" for row in rows]
    hint_by_location = {location: location for location in locations}
    files = list(dict.fromkeys(str(file_paths[row.row_id]) for row in rows if row.row_id in file_paths))
    if files:
        hint_by_location.update(zip(files, _shortest_distinct_tails(files)))
    distinct = list(dict.fromkeys(locations))
    if len(distinct) == len(locations):
        return [hint_by_location[location] for location in locations]
    totals = Counter(locations)
    seen: Counter[str] = Counter()
    hints = []
    for location in locations:
        seen[location] += 1
        number = f" ({seen[location]})" if totals[location] > 1 else ""
        hints.append(f"{hint_by_location[location]}{number}".strip())
    return hints


def _shortest_distinct_tails(locations: Sequence[str]) -> list[str]:
    """Return the shortest trailing folder path, or else source path, that tells distinct locations apart."""
    if len(locations) == 1:
        return [PurePath(locations[0]).parent.name]
    folders = [PurePath(location).parent.parts for location in locations]
    sources = [PurePath(location).parts for location in locations]
    for parts_list in (folders, sources):
        for depth in range(1, max(len(parts) for parts in parts_list) + 1):
            tails = [str(PurePath(*parts[-depth:])) if parts else "" for parts in parts_list]
            if len(set(tails)) == len(tails):
                return tails
    return list(locations)


def with_details(
    rows: tuple[WorkspaceBrowserRow, ...], file_paths: dict[str, PurePath]
) -> tuple[WorkspaceBrowserRow, ...]:
    """Add location hints to sibling rows that share a label, recursing into children.

    Rows without a location, or with the same location, are told apart by occurrence number.
    """
    same_label: defaultdict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        resolved = row.information_factory is not None and row.unavailable_reason is None
        if resolved:  # Rows still loading or failed have no location to tell apart.
            same_label[row.label].append(index)
    hints: dict[int, str] = {}
    for indexes in same_label.values():
        if len(indexes) > 1:
            hints.update(zip(indexes, _distinguishing_hints([rows[index] for index in indexes], file_paths)))
    return tuple(
        replace(row, detail=hints.get(index, row.detail), children=with_details(row.children, file_paths))
        for index, row in enumerate(rows)
    )
