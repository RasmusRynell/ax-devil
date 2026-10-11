"""What the Plugins view lists: plugins that failed to load, installed plugin packages, and the built-in plugins."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import cast

from packaging.utils import canonicalize_name

from ax_devil.launcher import plugin_root
from ax_devil.modules.plugin_installation.project import selected_plugins
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    PluginDefinitionBase,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_BUILTIN_ORIGIN = "builtin"
_PACKAGE_ORIGIN = "package:"


class Reads(Enum):
    """Where a decoder gets the data it decodes, worded for a tooltip."""

    FILE = "Reads overlay files"
    LIVE = "Reads live messages"


@dataclass(frozen=True)
class PluginPart:
    """One thing a plugin gives the user, listed under its row: a decoder, or a playlist resolver.

    A decoder is listed by the name the data-handler dropdowns show; *reads* says where it gets its data. A resolver
    reads nothing of its own.
    """

    name: str
    description: str = ""
    reads: tuple[Reads, ...] = ()
    resolver: bool = False


class Section(Enum):
    """A group of rows in the Plugins view, in the order they are listed."""

    FAILED = "Failed"
    INSTALLED = "Installed"
    BUILT_IN = "Built in"

    @property
    def title(self) -> str:
        """Return the caption of the section."""
        return self.value


@dataclass(frozen=True)
class PluginEntry:
    """One row: a plugin package, a built-in plugin or a failed plugin.

    *parts* is what a row with several parts lists under it. A row with one part is that part: *reads* and *resolver*
    are its, and its *subtitle* is the part's name when that differs from the row's, otherwise its description.
    *detail* is the installed version. *requirement* is what the installation selected the package with: a requirement,
    a wheel or an editable source folder. Only a package with one can be updated or removed.
    """

    section: Section
    name: str
    subtitle: str = ""
    detail: str = ""
    requirement: str | None = None
    reads: tuple[Reads, ...] = ()
    resolver: bool = False
    parts: tuple[PluginPart, ...] = ()


@dataclass(frozen=True)
class PluginGroup:
    """The rows of one section."""

    section: Section
    entries: tuple[PluginEntry, ...]


Selection = Mapping[str, str]
"""Selected plugin distributions by normalized name, with the requirement or source folder each was selected with."""
VersionOf = Callable[[str], str | None]
"""Return the installed version of a distribution, or None when it is not installed."""


def plugin_catalog(
    records: Iterable[PluginRecord], selection: Selection, version_of: VersionOf
) -> tuple[PluginGroup, ...]:
    """Return the non-empty sections for *records*, the installation's *selection*, and the installed versions.

    Packages go by their normalized name. A package with any plugin that failed to load is listed as failed, with the
    first failure's reason. A selected package with no plugins loaded, such as one installed since the app started,
    is listed as installed. Each built-in plugin is a row of its own.
    """
    failed: dict[str, PluginEntry] = {}
    loaded: dict[str, list[PluginPart]] = {name: [] for name in selection}
    built_in: list[PluginEntry] = []
    for record in records:
        distribution = _distribution(record)
        if record.status is PluginStatus.FAILED:
            name = distribution or record.definition.plugin_id
            failed.setdefault(name, _failed_entry(name, record, selection))
        elif distribution is not None:
            loaded.setdefault(distribution, []).extend(_parts(record))
        elif record.origin == _BUILTIN_ORIGIN:
            built_in.append(_entry(Section.BUILT_IN, record.definition.display_name, _parts(record)))

    installed = [
        _installed_entry(name, parts, selection, version_of) for name, parts in loaded.items() if name not in failed
    ]
    groups = (
        PluginGroup(Section.FAILED, _sorted(failed.values())),
        PluginGroup(Section.INSTALLED, _sorted(installed)),
        PluginGroup(Section.BUILT_IN, _sorted(built_in)),
    )
    return tuple(group for group in groups if group.entries)


def current_catalog() -> tuple[PluginGroup, ...]:
    """Return the sections for the plugins this app loaded and the plugin selection it keeps."""
    return plugin_catalog(RuntimePluginRegistry.list_plugins(), current_selection(), installed_version)


def current_selection() -> dict[str, str]:
    """Return the plugin selection of this app installation; none when it cannot be read."""
    try:
        return selected_plugins(plugin_root() / "current")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(f"Could not read the plugin selection: {exc}")
        return {}


def installed_version(distribution: str) -> str | None:
    """Return the version of *distribution* installed in this environment, or None."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _distribution(record: PluginRecord) -> str | None:
    """Return the normalized name of the package that installed *record*'s plugin; None when it is not a package."""
    if record.origin.startswith(_PACKAGE_ORIGIN):
        return canonicalize_name(record.origin.removeprefix(_PACKAGE_ORIGIN))
    return None


def _failed_entry(name: str, record: PluginRecord, selection: Selection) -> PluginEntry:
    reason = record.error or "Failed to load"
    return PluginEntry(
        Section.FAILED,
        name,
        subtitle=f"{reason[:1].upper()}{reason[1:]}",
        requirement=selection.get(name),
    )


def _installed_entry(name: str, parts: list[PluginPart], selection: Selection, version_of: VersionOf) -> PluginEntry:
    entry = _entry(
        Section.INSTALLED, name, tuple(parts), detail=version_of(name) or "", requirement=selection.get(name)
    )
    return entry if parts else replace(entry, subtitle="Not loaded")


def _entry(
    section: Section, name: str, parts: tuple[PluginPart, ...], *, detail: str = "", requirement: str | None = None
) -> PluginEntry:
    """Return the row for a plugin called *name* that gives *parts*; a single part is folded into the row itself."""
    if len(parts) == 1:
        (part,) = parts
        subtitle = part.name if part.name != name else part.description
        return PluginEntry(section, name, subtitle, detail, requirement, reads=part.reads, resolver=part.resolver)
    return PluginEntry(section, name, detail=detail, requirement=requirement, parts=parts)


def _parts(record: PluginRecord) -> tuple[PluginPart, ...]:
    return _PARTS[record.definition.plugin_type](record.definition)


def _decoder_parts(definition: PluginDefinitionBase) -> tuple[PluginPart, ...]:
    """Return a part for each handler type the decoder plugin reads, with where it reads it from."""
    decoder = cast(DecoderPluginDefinition, definition)
    parts: dict[str, PluginPart] = {}
    for reads, handlers in (
        (Reads.FILE, decoder.file_to_scene_decoders),
        (Reads.LIVE, decoder.payload_to_scene_decoders),
    ):
        for handler in handlers:
            part = parts.get(handler.handler_type) or PluginPart(handler.display_name, handler.description or "")
            parts[handler.handler_type] = replace(part, reads=(*part.reads, reads))
    return tuple(parts.values())


def _resolver_parts(definition: PluginDefinitionBase) -> tuple[PluginPart, ...]:
    return (PluginPart(definition.display_name, definition.description or "", resolver=True),)


_PARTS: dict[str, Callable[[PluginDefinitionBase], tuple[PluginPart, ...]]] = {
    DECODER_PLUGIN_TYPE: _decoder_parts,
    PLAYLIST_RESOLVER_PLUGIN_TYPE: _resolver_parts,
}


def _sorted(entries: Iterable[PluginEntry]) -> tuple[PluginEntry, ...]:
    """Order rows with decoders before playlist resolvers, each by name."""
    return tuple(sorted(entries, key=lambda entry: (entry.resolver, entry.name.casefold())))
