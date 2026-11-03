"""The settings every open view has, and the saved defaults new views start from.

Each setting is a `SettingKind`; `view_settings(kind)` is the application's `ViewSettings` for it.
"""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.filtering.filter_choice import FilterChoice
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.synchronization.timestamp_matching import TimestampFallbackMode, TimestampFallbackPolicy
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class SettingKind(Generic[T]):
    """One setting views share: its key under ``settings.view_defaults``, its built-in value, and its saved form.

    *parse* returns the value saved as the JSON it is given and raises ValueError for anything malformed; *dump* is
    its inverse. Values are immutable.
    """

    key: str
    built_in: T
    parse: Callable[[object], T]
    dump: Callable[[T], object]


class ViewSettings(QObject, Generic[T]):
    """Track one setting at three scopes.

    This view: its own controls. All open views: `apply_to_all` sets every view to one value, which is not
    remembered. New views: `use_as_default` saves the value in the config, where null means the kind's built-in value.
    """

    changed = Signal()  # Any open view's value or the saved default changed
    appliedToAll = Signal(object)  # The value every open view should adopt

    def __init__(self, kind: SettingKind[T], parent: QObject | None = None) -> None:
        """Read the saved default once; a malformed one is logged and replaced by the built-in value."""
        super().__init__(parent)
        self._kind = kind
        self._default = kind.built_in
        saved = ConfigManager().get("settings")["view_defaults"][kind.key]
        if saved is not None:
            try:
                self._default = kind.parse(saved)
            except ValueError as error:
                logger.warning(f"Invalid saved default for {kind.key}: {error}; using the built-in value.")
        self._in_use: dict[int, T] = {}
        self._keys = itertools.count()

    def default(self) -> T:
        """Return the value new views start with."""
        return self._default

    def use_as_default(self, value: T) -> None:
        """Save *value* as the default for new views; the built-in value is saved as null.

        Raises the save failure and leaves the config and the default unchanged.
        """
        config = ConfigManager()
        previous = copy.deepcopy(config.get_raw("settings"))
        root = copy.deepcopy(previous)
        root["view_defaults"][self._kind.key] = None if value == self._kind.built_in else self._kind.dump(value)
        try:
            config.set("settings", root)
            config.save()
        except Exception:
            config.set("settings", previous)
            raise
        self._default = value
        self.changed.emit()

    def apply_to_all(self, value: T) -> None:
        """Ask every open view to adopt *value*."""
        self.appliedToAll.emit(value)

    def add(self, value: T) -> int:
        """Register an open view and return its key."""
        key = next(self._keys)
        self._in_use[key] = value
        self.changed.emit()
        return key

    def set_value(self, key: int, value: T) -> None:
        """Record the value the view *key* shows now."""
        if self._in_use.get(key) == value:
            return
        self._in_use[key] = value
        self.changed.emit()

    def discard(self, key: int) -> None:
        """Forget the view *key*; forgetting a view twice does nothing."""
        if self._in_use.pop(key, None) is not None:
            self.changed.emit()

    def values_in_use(self) -> list[T]:
        """Return every open view's value."""
        return list(self._in_use.values())


def _parse_overlay_sync(value: object) -> TimestampFallbackPolicy:
    if not isinstance(value, dict) or set(value) != {"mode"}:
        raise ValueError(f"Invalid overlay sync {value!r}")
    return TimestampFallbackPolicy(mode=TimestampFallbackMode(value["mode"]))


STICKY_OVERLAYS = SettingKind(
    "sticky_overlays",
    OverlayPersistenceSettings.default_enabled(),
    OverlayPersistenceSettings.from_config,
    OverlayPersistenceSettings.config_value,
)
OVERLAY_SYNC = SettingKind(
    "overlay_sync",
    TimestampFallbackPolicy(),
    _parse_overlay_sync,
    lambda policy: {"mode": policy.mode.value},
)
OBJECT_FILTER = SettingKind("object_filter", FilterChoice(), FilterChoice.from_config, FilterChoice.config_value)

_hubs: dict[str, ViewSettings[Any]] = {}


def view_settings(kind: SettingKind[T]) -> ViewSettings[T]:
    """Return the application's settings of *kind*, created on first use; call it on the GUI thread."""
    if kind.key not in _hubs:
        _hubs[kind.key] = ViewSettings(kind)
    return cast("ViewSettings[T]", _hubs[kind.key])


@dataclass(frozen=True, slots=True)
class ViewDefaults:
    """What a new view starts with; the built-in values unless the user saved others."""

    sticky: OverlayPersistenceSettings = field(default_factory=OverlayPersistenceSettings.default_enabled)
    overlay_sync: TimestampFallbackPolicy = field(default_factory=TimestampFallbackPolicy)
    object_filter: FilterChoice = field(default_factory=FilterChoice)


def view_defaults() -> ViewDefaults:
    """Return the saved defaults of every setting; call it on the GUI thread."""
    return ViewDefaults(
        sticky=view_settings(STICKY_OVERLAYS).default(),
        overlay_sync=view_settings(OVERLAY_SYNC).default(),
        object_filter=view_settings(OBJECT_FILTER).default(),
    )
