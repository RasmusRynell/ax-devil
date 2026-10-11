"""What the Plugins view lists: plugins that failed to load, installed plugin packages, and the built-in plugins."""

from __future__ import annotations

import importlib.metadata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from packaging.utils import canonicalize_name

from ax_devil.launcher import plugin_root
from ax_devil.modules.plugin_installation.project import selected_plugins
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_BUILTIN_ORIGIN = "builtin"
_PACKAGE_ORIGIN = "package:"


class PluginKind(Enum):
    """What a plugin provides, named for one and for several."""

    DECODER = (DECODER_PLUGIN_TYPE, "Decoder", "Decoders")
    PLAYLIST_RESOLVER = (PLAYLIST_RESOLVER_PLUGIN_TYPE, "Playlist resolver", "Playlist resolvers")

    def __init__(self, plugin_type: str, singular: str, plural: str) -> None:
        self.plugin_type = plugin_type
        self.singular = singular
        self.plural = plural

    def counted(self, count: int) -> str:
        """Return *count* plugins of this kind as a short phrase, such as ``Decoder`` or ``2 Decoders``."""
        return self.singular if count == 1 else f"{count} {self.plural}"


_KINDS = {kind.plugin_type: kind for kind in PluginKind}


class Section(Enum):
    """A group of rows in the Plugins view, in the order they are listed."""

    FAILED = ("Failed", True)
    INSTALLED = ("Installed", True)
    BUILT_IN = ("Built in", False)

    def __init__(self, title: str, counted: bool) -> None:
        self.title = title
        self.counted = counted
        """Whether the caption shows how many rows the section has."""


@dataclass(frozen=True)
class PluginEntry:
    """One row: a plugin package or a failed plugin, or one kind of built-in plugin with how many there are.

    *requirement* is what the installation selected the package with: a requirement, a wheel or an editable source
    folder. Only a package with one can be updated or removed.
    """

    section: Section
    name: str
    subtitle: str = ""
    detail: str = ""
    requirement: str | None = None


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
    is listed as installed.
    """
    failed: dict[str, PluginEntry] = {}
    loaded: dict[str, Counter[PluginKind]] = {name: Counter() for name in selection}
    built_in: Counter[PluginKind] = Counter()
    for record in records:
        distribution = _distribution(record)
        if record.status is PluginStatus.FAILED:
            name = distribution or record.definition.plugin_id
            failed.setdefault(name, _failed_entry(name, record, selection))
        elif distribution is not None:
            loaded.setdefault(distribution, Counter())[_KINDS[record.definition.plugin_type]] += 1
        elif record.origin == _BUILTIN_ORIGIN:
            built_in[_KINDS[record.definition.plugin_type]] += 1

    installed = [
        _installed_entry(name, kinds, selection, version_of) for name, kinds in loaded.items() if name not in failed
    ]
    groups = (
        PluginGroup(Section.FAILED, _sorted(failed.values())),
        PluginGroup(Section.INSTALLED, _sorted(installed)),
        PluginGroup(
            Section.BUILT_IN,
            tuple(PluginEntry(Section.BUILT_IN, kind.plural, detail=f"{built_in[kind]}") for kind in PluginKind),
        ),
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


def _installed_entry(name: str, kinds: Counter[PluginKind], selection: Selection, version_of: VersionOf) -> PluginEntry:
    requirement = selection.get(name)
    provides = [kind.counted(kinds[kind]) for kind in PluginKind if kinds[kind]] or ["Not loaded"]
    version = version_of(name)
    editable = requirement is not None and Path(requirement).is_absolute()
    parts = [*([version] if version else []), *provides, *(["Editable"] if editable else [])]
    return PluginEntry(Section.INSTALLED, name, subtitle=" · ".join(parts), requirement=requirement)


def _sorted(entries: Iterable[PluginEntry]) -> tuple[PluginEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: entry.name.casefold()))
