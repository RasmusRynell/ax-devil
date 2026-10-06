"""Centralized keyboard shortcut management."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget

from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)

_WINDOW_SHORTCUT = Qt.ShortcutContext.WindowShortcut


@dataclass(frozen=True, slots=True)
class ShortcutDefinition:
    """Immutable definition of a keyboard shortcut action."""

    action_id: str
    display_name: str
    category: str
    default_key_sequence: QKeySequence | None = None
    show_on_welcome: bool = False
    welcome_group: str = ""
    acts_on_viewer: bool = False
    """Whether the action targets the focused viewer, so it also works from lane fullscreen."""


# ---------------------------------------------------------------------------
# Default shortcut definitions
# ---------------------------------------------------------------------------

DEFAULT_SHORTCUTS: tuple[ShortcutDefinition, ...] = (
    # Playback
    ShortcutDefinition("playback.play_pause", "Play / Pause", "Playback", QKeySequence("Space"), acts_on_viewer=True),
    ShortcutDefinition(
        "playback.step_forward", "Step Forward 1 Frame", "Playback", QKeySequence("."), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "playback.step_backward", "Step Backward 1 Frame", "Playback", QKeySequence(","), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "playback.step_forward_10", "Step Forward 10 Frames", "Playback", QKeySequence("Right"), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "playback.step_backward_10", "Step Backward 10 Frames", "Playback", QKeySequence("Left"), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "playback.speed_down", "Playback Speed Down", "Playback", QKeySequence("["), acts_on_viewer=True
    ),
    ShortcutDefinition("playback.speed_up", "Playback Speed Up", "Playback", QKeySequence("]"), acts_on_viewer=True),
    # Navigation
    ShortcutDefinition(
        "navigation.next_entry", "Next Playlist Entry", "Navigation", QKeySequence("Ctrl+Right"), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "navigation.prev_entry", "Previous Playlist Entry", "Navigation", QKeySequence("Ctrl+Left"), acts_on_viewer=True
    ),
    # View
    ShortcutDefinition("view.toggle_info", "Toggle Info Overlay", "View", QKeySequence("I"), acts_on_viewer=True),
    ShortcutDefinition(
        "view.toggle_media_tools", "Media Tools Panel", "View", QKeySequence("Ctrl+B"), acts_on_viewer=True
    ),
    ShortcutDefinition(
        "view.toggle_lane_fullscreen", "Toggle Lane Fullscreen", "View", QKeySequence("F"), acts_on_viewer=True
    ),
    ShortcutDefinition("view.toggle_fullscreen", "Toggle Fullscreen", "View", QKeySequence("F11")),
    ShortcutDefinition("view.zoom_in", "Zoom In", "View", QKeySequence("Ctrl++"), acts_on_viewer=True),
    ShortcutDefinition("view.zoom_out", "Zoom Out", "View", QKeySequence("Ctrl+-"), acts_on_viewer=True),
    ShortcutDefinition("view.reset_zoom", "Reset Zoom", "View", QKeySequence("Ctrl+0"), acts_on_viewer=True),
    ShortcutDefinition("view.render_catalogs", "Render Catalogs", "View", QKeySequence("Ctrl+R")),
    # Application
    ShortcutDefinition(
        "app.add_video",
        "Add Video",
        "Application",
        QKeySequence("Ctrl+N"),
        show_on_welcome=True,
        welcome_group="Open",
    ),
    ShortcutDefinition(
        "app.add_live_stream",
        "Add Live Stream",
        "Application",
        QKeySequence("Ctrl+L"),
        show_on_welcome=True,
        welcome_group="Open",
    ),
    ShortcutDefinition(
        "app.add_playlist",
        "Add Playlist",
        "Application",
        QKeySequence("Ctrl+Shift+N"),
        show_on_welcome=True,
        welcome_group="Open",
    ),
    ShortcutDefinition("app.exit", "Exit", "Application", QKeySequence("Ctrl+Q")),
    ShortcutDefinition("app.debug_metrics", "Debug Metrics", "Application", QKeySequence("Ctrl+D")),
    ShortcutDefinition(
        "app.keyboard_shortcuts",
        "Keyboard Shortcuts",
        "Application",
        QKeySequence("Ctrl+K"),
        show_on_welcome=True,
        welcome_group="Configure",
    ),
    ShortcutDefinition(
        "app.settings",
        "Settings",
        "Application",
        QKeySequence("Ctrl+,"),
        show_on_welcome=True,
        welcome_group="Configure",
    ),
)


class ShortcutManager(QObject):
    """Central registry and manager for keyboard shortcuts.

    Owns a ``QAction`` per registered shortcut definition.  Actions are parented to
    the target window (via :meth:`install`) so they participate in Qt's automatic
    lifecycle cleanup.  User overrides are persisted through a simple config dict
    (action_id → key-sequence string); only non-default bindings are stored.
    """

    def __init__(self, config_overrides: dict[str, str] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._definitions: dict[str, ShortcutDefinition] = {}
        self._actions: dict[str, QAction] = {}
        self._overrides: dict[str, str] = dict(config_overrides) if config_overrides else {}
        self._installed = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, definition: ShortcutDefinition) -> None:
        """Register a shortcut definition.  Must be called before :meth:`install`."""
        if self._installed:
            raise RuntimeError(f"Cannot register shortcut '{definition.action_id}' after install()")
        if definition.action_id in self._definitions:
            logger.warning(f"Shortcut '{definition.action_id}' already registered — skipping duplicate")
            return
        self._definitions[definition.action_id] = definition

    def register_defaults(self) -> None:
        """Register all built-in shortcut definitions."""
        for defn in DEFAULT_SHORTCUTS:
            self.register(defn)

    # ------------------------------------------------------------------
    # Install — creates QActions parented to the target window
    # ------------------------------------------------------------------

    def install(self, window: QWidget) -> None:
        """Create ``QAction`` objects parented to *window* for every registered definition.

        Applies user overrides from config.  Stale overrides (action_ids not in the
        registry) are logged and discarded.
        """
        if self._installed:
            raise RuntimeError("ShortcutManager.install() called twice")

        # Prune stale overrides
        stale_keys = set(self._overrides) - set(self._definitions)
        for key in stale_keys:
            logger.debug(f"Pruning stale shortcut override: {key}")
        for key in stale_keys:
            del self._overrides[key]

        for action_id, defn in self._definitions.items():
            action = QAction(defn.display_name, window)
            action.setShortcutContext(_WINDOW_SHORTCUT)

            key_seq = self._resolve_key_sequence(action_id)
            if key_seq is not None:
                action.setShortcut(key_seq)

            window.addAction(action)
            self._actions[action_id] = action

        self._installed = True
        logger.info(f"Installed {len(self._actions)} keyboard shortcuts")

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_action(self, action_id: str) -> QAction:
        """Return the ``QAction`` for *action_id*.  Raises ``KeyError`` if not installed."""
        return self._actions[action_id]

    def get_definition(self, action_id: str) -> ShortcutDefinition:
        """Return the definition for *action_id*."""
        return self._definitions[action_id]

    def definitions(self) -> list[ShortcutDefinition]:
        """Return all registered definitions, ordered by category then display name."""
        return sorted(self._definitions.values(), key=lambda d: (d.category, d.display_name))

    def current_key_sequence(self, action_id: str) -> QKeySequence | None:
        """Return the currently active key sequence for *action_id*."""
        if action_id in self._actions:
            seq = self._actions[action_id].shortcut()
            return seq if not seq.isEmpty() else None
        return self._resolve_key_sequence(action_id)

    # ------------------------------------------------------------------
    # Rebinding
    # ------------------------------------------------------------------

    def has_conflict(self, key_sequence: QKeySequence, exclude_action_id: str | None = None) -> str | None:
        """Return the action_id that already uses *key_sequence*, or ``None``."""
        target = key_sequence.toString()
        if not target:
            return None
        for action_id, action in self._actions.items():
            if action_id == exclude_action_id:
                continue
            if action.shortcut().toString() == target:
                return action_id
        return None

    def set_binding(self, action_id: str, key_sequence: QKeySequence | None) -> None:
        """Rebind *action_id* to *key_sequence* (or clear if ``None``).

        Updates the live ``QAction`` and the overrides dict.
        """
        if action_id not in self._definitions:
            raise KeyError(f"Unknown shortcut action: {action_id}")

        defn = self._definitions[action_id]
        default_str = defn.default_key_sequence.toString() if defn.default_key_sequence else ""
        new_str = key_sequence.toString() if key_sequence else ""

        if new_str == default_str:
            self._overrides.pop(action_id, None)
        else:
            self._overrides[action_id] = new_str

        if action_id in self._actions:
            self._actions[action_id].setShortcut(key_sequence or QKeySequence())

        logger.debug(f"Shortcut '{action_id}' rebound to '{new_str or '(none)'}'")

    def reset_to_defaults(self) -> None:
        """Clear all user overrides and revert every binding to its default."""
        self._overrides.clear()
        for action_id, action in self._actions.items():
            defn = self._definitions[action_id]
            action.setShortcut(defn.default_key_sequence or QKeySequence())
        logger.info("All shortcuts reset to defaults")

    # ------------------------------------------------------------------
    # Config persistence helpers
    # ------------------------------------------------------------------

    def get_config_overrides(self) -> dict[str, str]:
        """Return the current overrides dict suitable for config persistence."""
        return dict(self._overrides)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_key_sequence(self, action_id: str) -> QKeySequence | None:
        """Determine the effective key sequence: override first, then default."""
        if action_id in self._overrides:
            override_str = self._overrides[action_id]
            if not override_str:
                return None
            seq = QKeySequence(override_str)
            if seq.isEmpty():
                logger.warning(f"Invalid key sequence override for '{action_id}': '{override_str}'")
                return self._definitions[action_id].default_key_sequence
            return seq
        default = self._definitions[action_id].default_key_sequence
        # A key the user bound to another action wins over a default, e.g. one added in a later release.
        user_keys = {QKeySequence(key).toString() for key in self._overrides.values()}
        if default is not None and default.toString() in user_keys:
            logger.info(f"Default shortcut '{default.toString()}' for '{action_id}' is rebound by the user; left unset")
            return None
        return default
