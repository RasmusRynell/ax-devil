"""The Workspace Item base class and the JSON reading helpers every item kind shares."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, TypeVar
from uuid import uuid4

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.source_paths import SourcePaths
from ax_devil.modules.workspace.core.content import Content
from ax_devil.modules.workspace.core.resolution import ItemResolutionError, ResolutionContext

_ItemT = TypeVar("_ItemT", bound="WorkspaceItem")
_ValueT = TypeVar("_ValueT")
_REQUIRED: Any = object()


def read_field(data: Mapping[str, Any], key: str, expected: type[_ValueT], default: _ValueT = _REQUIRED) -> _ValueT:
    """Return ``data[key]`` as an *expected* JSON value, or *default* when absent; raise ``ValueError`` otherwise."""
    if key not in data:
        if default is _REQUIRED:
            raise ValueError(f"missing '{key}'")
        return default
    value = data[key]
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ValueError(f"'{key}' must be a {expected.__name__}")
    return value


def read_optional_str(data: Mapping[str, Any], key: str) -> str | None:
    """Return ``data[key]`` as a string or None when absent or null; raise ``ValueError`` for anything else."""
    return None if data.get(key) is None else read_field(data, key, str)


def path_to_json(path: Path, base_dir: Path | None) -> str:
    """Return *path* relative to *base_dir* with POSIX separators when inside it, absolute otherwise or without one.

    Both are normalized lexically first, so ``..`` segments cannot escape the folder; symlinks are not resolved.
    """
    absolute = Path(os.path.normpath(path.absolute()))
    if base_dir is None:
        return str(absolute)
    folder = Path(os.path.normpath(base_dir.absolute()))
    try:
        return absolute.relative_to(folder).as_posix()
    except ValueError:
        return str(absolute)


def path_from_json(data: Mapping[str, Any], key: str, base_dir: Path | None) -> Path:
    """Return the path in ``data[key]``; a relative one is read against *base_dir* when given.

    The path is normalized lexically, as ``path_to_json`` writes it, so reading what was written compares equal.
    """
    path = Path(read_field(data, key, str))
    return Path(os.path.normpath(path if base_dir is None else base_dir / path))


def new_item_id() -> str:
    """Return a fresh item id; an item keeps its id for as long as it exists."""
    return uuid4().hex


@dataclass(frozen=True, kw_only=True)
class WorkspaceItem(ABC):
    """A recipe for one thing to work with: enough to rebuild its Content, never the rebuilt result.

    A kind is one subclass with a unique ``kind`` registered in ``ITEM_KINDS``; it implements ``_build_contents`` and
    the JSON hooks ``_fields_to_json`` and ``_fields_from_json`` for its own fields, and ``default_name``. Callers use
    ``resolve``, which also gives every Content its identity, and ``to_json`` / ``from_json``, which add the common
    fields.

    An empty ``label`` means the item is shown by its ``default_name``, which is derived when displayed and never saved.
    """

    kind: ClassVar[str]
    editable: ClassVar[bool] = False
    """Whether the saved recipe can be edited with an existing content form."""
    renamable: ClassVar[bool] = True
    """Whether a new label survives saving; an item written back unchanged cannot be renamed."""
    bookmarkable: ClassVar[bool] = False
    """Whether new bookmarks and review marks may be made on the item's videos; live streams never take them."""
    label: str = ""
    id: str = field(default_factory=new_item_id)

    @property
    @abstractmethod
    def default_name(self) -> str:
        """Return the name shown when the item has no label, such as the file name or the expanded host."""

    @property
    def display_name(self) -> str:
        """Return the label, or the default name when the label is empty."""
        return self.label or self.default_name

    @property
    def hosts(self) -> tuple[str, ...]:
        """Return the network hosts the item connects to, as entered and expanded; none for items that do not."""
        return ()

    def source_paths(self, context: ResolutionContext, token: CancelToken) -> SourcePaths:
        """Return the files and folders this item's Content is read from, so a change to them makes it stale; none by
        default.

        Runs on the worker thread that resolves the item, before it reads anything.
        """
        return SourcePaths()

    def with_label(self: _ItemT, label: str) -> _ItemT:
        """Return this item renamed to *label*, keeping its id.

        An empty label, or the default name itself, returns the item to its default name, so the item never stores a
        derived value such as an expanded host.
        """
        return replace(self, label="" if label == self.default_name else label)

    def resolve(self, context: ResolutionContext, token: CancelToken) -> tuple[Content, ...]:
        """Rebuild this item's Content named after the item, or raise ``ItemResolutionError`` with a reason."""
        return self.name_contents(self._resolve_by_default_name(context, token))

    def _resolve_by_default_name(self, context: ResolutionContext, token: CancelToken) -> tuple[Content, ...]:
        """Rebuild this item's Content as named without a label, or raise ``ItemResolutionError``.

        Content ids derive from the item id and, unless the item built one, the Content's position, so they are the
        same on every resolution.
        """
        try:
            token.raise_if_cancelled()
            contents = self._build_contents(context, token)
            token.raise_if_cancelled()
        except (ValueError, OSError) as exc:
            raise ItemResolutionError(str(exc)) from exc
        if not contents:
            raise ItemResolutionError(f"{self.display_name} has nothing to open.")
        return tuple(
            replace(content, content_id=content.content_id or f"{self.id}/{index}", item_id=self.id)
            for index, content in enumerate(contents)
        )

    def name_contents(self, contents: Sequence[Content]) -> tuple[Content, ...]:
        """Return *contents*, as named without a label, renamed after this item's label when it has one."""
        if not self.label:
            return tuple(contents)
        return tuple(replace(content, display_name=self.label) for content in contents)

    @abstractmethod
    def _build_contents(self, context: ResolutionContext, token: CancelToken) -> Sequence[Content]:
        """Return this item's Content as named without a label; raise ``ItemResolutionError``, ``ValueError``, or
        ``OSError`` when it cannot."""

    def to_json(self, base_dir: Path | None) -> Any:
        """Return this item as a JSON object; paths inside *base_dir* are written relative to it, others absolute.

        Typed ``Any`` because an unreadable item returns whatever JSON value the file held.
        """
        return {"kind": self.kind, "id": self.id, "label": self.label, **self._fields_to_json(base_dir)}

    @classmethod
    def from_json(cls: type[_ItemT], data: Mapping[str, Any], base_dir: Path | None) -> _ItemT:
        """Return the item *data* describes; raise ``ValueError`` saying what is malformed.

        Relative paths are read against *base_dir* when given.
        """
        try:
            return cls(
                id=read_field(data, "id", str),
                label=read_field(data, "label", str),
                **cls._fields_from_json(data, base_dir),
            )
        except ValueError as exc:
            raise ValueError(f"Malformed {cls.kind} item: {exc}") from exc

    @abstractmethod
    def _fields_to_json(self, base_dir: Path | None) -> dict[str, Any]:
        """Return this kind's own JSON fields."""

    @classmethod
    @abstractmethod
    def _fields_from_json(cls, data: Mapping[str, Any], base_dir: Path | None) -> dict[str, Any]:
        """Return the constructor arguments for this kind's own fields; raise ``ValueError`` when malformed."""
