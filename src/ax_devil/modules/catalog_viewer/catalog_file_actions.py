"""The commands that make, remove or choose catalog files from the catalog viewer window."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QWidget

from ax_devil.modules.background_jobs import LatestRequest
from ax_devil.modules.catalog_viewer.new_catalog_dialog import NewCatalogDialog
from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.choice_dialog import ask, notify
from ax_devil.modules.scene.rendering import (
    SceneRenderCatalogFile,
    SceneRenderCatalogListing,
    SceneRenderCatalogManager,
)


class CatalogFileActions:
    """New copy, Delete, Apply to all and Use as default for the catalog the viewer shows.

    Each command runs on the window's *request*, so showing another catalog cancels one still in flight. The window
    says which catalog is shown and what to do with the outcome: show another catalog, reload this one, or report
    that it does not load.
    """

    def __init__(
        self,
        window: QWidget,
        manager: SceneRenderCatalogManager,
        request: LatestRequest,
        *,
        path: Callable[[], Path],
        show_catalog: Callable[[Path], None],
        reload: Callable[[], None],
        load_failed: Callable[[Exception], None],
    ) -> None:
        self._window = window
        self._manager = manager
        self._request = request
        self._path = path
        self._show_catalog = show_catalog
        self._reload = reload
        self._load_failed = load_failed

    def create_copy(self) -> None:
        source = self._manager.listing().catalog_for(self._path())
        with NewCatalogDialog(source.name if source is not None else self._path().stem, self._window) as dialog:
            if dialog.exec() != BaseDialog.DialogCode.Accepted or not dialog.name():
                return
            name = dialog.name()
        base_path = self._path()
        self._request.start(
            lambda result, error: self._manager.create_catalog(
                name, base_catalog_path=base_path, on_created=result, on_failed=error
            ),
            on_result=self._show_created,
            on_error=self._warn_create,
        )

    def _show_created(self, created: SceneRenderCatalogFile) -> None:
        self._show_catalog(created.path)

    def _warn_create(self, error: Exception) -> None:
        notify(self._window, "New Catalog", f"Could not create the catalog: {error}")

    def apply_to_all(self) -> None:
        self._request.start(
            lambda result, error: self._manager.apply_to_all(
                self._path(), on_done=partial(result, None), on_failed=error
            ),
            on_result=self._action_done,
            on_error=self._warn_apply,
        )

    def _warn_apply(self, error: Exception) -> None:
        self._load_failed(error)
        notify(self._window, "Apply to All", f"Could not use the catalog: {error}")

    def use_as_default(self) -> None:
        self._request.start(
            lambda result, error: self._manager.use_as_default(
                self._path(), on_done=partial(result, None), on_failed=error, on_not_saved=result
            ),
            on_result=self._default_done,
            on_error=self._default_load_failed,
        )

    def _default_done(self, persistence_error: Exception | None) -> None:
        self._reload()
        if persistence_error is not None:
            self._warn_default(persistence_error)

    def _default_load_failed(self, error: Exception) -> None:
        self._load_failed(error)
        self._warn_default(error)

    def _warn_default(self, error: Exception) -> None:
        notify(self._window, "Use as Default", f"Could not make the catalog the default: {error}")

    def _action_done(self, _result: None) -> None:
        self._reload()

    def delete_catalog(self) -> None:
        listing = self._manager.listing()
        default_note = (
            "\n\nIt is the default catalog, so new views will start from the default built-in catalog."
            if self._path() == listing.default_path
            else ""
        )
        answer = ask(
            self._window,
            "Delete Catalog",
            f"Delete {listing.label(self._path())}?\n\n{self._path()}\n\n"
            f"The file is deleted permanently.{default_note}",
            {"Delete": True, "Cancel": False},
            False,
        )
        if not answer:
            return
        try:
            self._manager.delete_catalog(self._path())
        except (OSError, ValueError) as error:
            notify(self._window, "Delete Catalog", f"Could not delete the catalog: {error}")
            return
        self._request.start(
            lambda result, error: self._manager.refresh_catalogs(on_done=result, on_failed=error),
            on_result=self._show_default,
            on_error=self._load_failed,
        )

    def _show_default(self, listing: SceneRenderCatalogListing) -> None:
        self._show_catalog(listing.chosen_default_path)
