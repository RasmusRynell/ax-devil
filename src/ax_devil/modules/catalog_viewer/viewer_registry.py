"""The one catalog viewer window of each catalog manager: showing it again raises it, closing the manager closes it."""

from __future__ import annotations

from pathlib import Path
from weakref import ref

from PySide6.QtWidgets import QWidget

from ax_devil.modules.catalog_viewer.window import CatalogViewerWindow
from ax_devil.modules.chrome.chrome_window import window_uses_custom_frame
from ax_devil.modules.scene.rendering import SceneRenderCatalogManager

_OPEN_WINDOWS: dict[int, CatalogViewerWindow] = {}


def close_catalog_viewer(manager: SceneRenderCatalogManager) -> None:
    """Close the catalog viewer of *manager*, if open, so it cleans up before the window that owns it goes away."""
    window = _OPEN_WINDOWS.pop(id(manager), None)
    if window is not None:
        window.close()


def show_catalog_viewer(
    manager: SceneRenderCatalogManager,
    parent: QWidget | None = None,
    *,
    catalog_path: Path | None = None,
) -> CatalogViewerWindow:
    """Show the application's catalog viewer on *catalog_path*, or on the default catalog when it opens.

    There is one viewer per catalog manager; showing it again raises the open window.
    """
    window = _OPEN_WINDOWS.get(id(manager))
    if window is None:
        owner = parent.window() if parent is not None else None
        window = CatalogViewerWindow(
            manager, owner, use_custom_frame=owner is not None and window_uses_custom_frame(owner)
        )
        _OPEN_WINDOWS[id(manager)] = window
        key, window_ref = id(manager), ref(window)

        def forget_window() -> None:
            if _OPEN_WINDOWS.get(key) is window_ref():
                _OPEN_WINDOWS.pop(key, None)

        window.destroyed.connect(forget_window)
    if catalog_path is not None:
        window.show_catalog(catalog_path)
    window.show()
    window.raise_()
    window.activateWindow()
    return window
