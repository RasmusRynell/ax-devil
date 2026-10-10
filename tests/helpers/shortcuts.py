"""Shortcut manager setup shared by window and dialog tests."""

from ax_devil.modules.shortcuts.shortcuts import ShortcutManager


def make_shortcut_manager() -> ShortcutManager:
    """Return a shortcut manager with the default shortcuts registered."""
    manager = ShortcutManager()
    manager.register_defaults()
    return manager
