"""Which fields the user pinned in the object inspector, kept across the objects a viewer shows."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldId:
    """A field by where it is, not whose it is: its group title and its path inside the group, as ``InspectionField``
    addresses it. The path stays a tuple, so a dictionary key containing a dot is not mistaken for a nested name.
    """

    group: str
    path: tuple[str, ...]

    @property
    def label(self) -> str:
        """Return ``group.path`` in lowercase group, as the Objects query names the field (``debug.latency``)."""
        return ".".join((self.group.lower(), *self.path))


class PinnedFields:
    """The pinned fields in the order they were pinned. A viewer's pages share one, so the choice follows the user from
    object to object and ends with the viewer.
    """

    def __init__(self) -> None:
        self._ids: list[FieldId] = []

    def __contains__(self, field_id: object) -> bool:
        return field_id in self._ids

    def __iter__(self) -> Iterator[FieldId]:
        return iter(self._ids)

    def __len__(self) -> int:
        return len(self._ids)

    def toggle(self, field_id: FieldId) -> None:
        """Pin *field_id* after the others, or unpin it if it is pinned."""
        if field_id in self._ids:
            self._ids.remove(field_id)
        else:
            self._ids.append(field_id)
