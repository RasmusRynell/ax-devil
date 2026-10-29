"""The sticky overlay settings of every open view, and the saved default new views start from."""

from __future__ import annotations

import copy
import itertools

from PySide6.QtCore import QObject, Signal

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistenceSettings

logger = get_logger(__name__)

_CONFIG_KEY = "sticky_overlays"


class OverlayPersistenceViews(QObject):
    """Track sticky overlay settings at three scopes.

    This view: its own controls. All open views: `apply_to_all` sets every view to one value, which is not
    remembered. New views: `use_as_default` saves the value in the config, where null means the built-in
    `OverlayPersistenceSettings.default_enabled()`.
    """

    changed = Signal()  # Any open view's settings or the saved default changed
    appliedToAll = Signal(object)  # The OverlayPersistenceSettings every open view should adopt

    def __init__(self, parent: QObject | None = None) -> None:
        """Read the saved default once."""
        super().__init__(parent)
        self._default = OverlayPersistenceSettings.from_config(ConfigManager().get("settings")[_CONFIG_KEY])
        self._in_use: dict[int, OverlayPersistenceSettings] = {}
        self._keys = itertools.count()

    def default(self) -> OverlayPersistenceSettings:
        """Return a copy of the settings new views start with."""
        return self._default.copy()

    def use_as_default(self, settings: OverlayPersistenceSettings) -> None:
        """Save *settings* as the default for new views; the built-in default is saved as null.

        Raises the save failure and leaves the config and the default unchanged.
        """
        config = ConfigManager()
        previous = copy.deepcopy(config.get_raw("settings"))
        root = copy.deepcopy(previous)
        built_in = settings == OverlayPersistenceSettings.default_enabled()
        root[_CONFIG_KEY] = None if built_in else settings.config_value()
        try:
            config.set("settings", root)
            config.save()
        except Exception:
            config.set("settings", previous)
            raise
        self._default = settings.copy()
        self.changed.emit()

    def apply_to_all(self, settings: OverlayPersistenceSettings) -> None:
        """Ask every open view to adopt *settings*."""
        self.appliedToAll.emit(settings.copy())

    def add(self, settings: OverlayPersistenceSettings) -> int:
        """Register an open view and return its key."""
        key = next(self._keys)
        self._in_use[key] = settings.copy()
        self.changed.emit()
        return key

    def set_settings(self, key: int, settings: OverlayPersistenceSettings) -> None:
        """Record the settings the view *key* shows now."""
        if self._in_use.get(key) == settings:
            return
        self._in_use[key] = settings.copy()
        self.changed.emit()

    def discard(self, key: int) -> None:
        """Forget the view *key*; forgetting a view twice does nothing."""
        if self._in_use.pop(key, None) is not None:
            self.changed.emit()

    def settings_in_use(self) -> list[OverlayPersistenceSettings]:
        """Return a copy of every open view's settings."""
        return [settings.copy() for settings in self._in_use.values()]


_views: OverlayPersistenceViews | None = None


def overlay_persistence_views() -> OverlayPersistenceViews:
    """Return the application's sticky overlay settings, created on first use; call it on the GUI thread."""
    global _views
    if _views is None:
        _views = OverlayPersistenceViews()
    return _views
