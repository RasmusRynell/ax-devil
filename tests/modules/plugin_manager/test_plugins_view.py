"""The Plugins view lists the plugins and runs plugin changes, offering a restart once one succeeds."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QToolButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.chrome.elided_label import ElidedLabel
from ax_devil.modules.chrome.list_row import ListRow
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.plugin_manager.add_plugin_dialog import AddPluginDialog
from ax_devil.modules.plugin_manager.catalog import PluginGroup, plugin_catalog
from ax_devil.modules.plugin_manager.plugin_changes import PluginChange, PluginChangeError, PluginChanges
from ax_devil.modules.plugin_manager.plugins_view import PluginsView
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    PluginRecord,
    PluginStatus,
    RuntimePluginRegistry,
)


def _record(plugin_id: str, origin: str, error: str | None = None, handlers: tuple[str, ...] = ()) -> PluginRecord:
    files = tuple(FileToSceneDecoderDefinition(name.upper(), Mock(), name) for name in handlers)
    return PluginRecord(
        definition=DecoderPluginDefinition(
            plugin_type=DECODER_PLUGIN_TYPE, plugin_id=plugin_id, display_name=plugin_id, file_to_scene_decoders=files
        ),
        entrypoint=Path(f"/plugins/{plugin_id}"),
        origin=origin,
        status=PluginStatus.FAILED if error else PluginStatus.LOADED,
        error=error,
    )


SELECTION = {"yolo-detections": "yolo-detections>=0.2", "old-model-decoder": "old-model-decoder"}


@pytest.fixture
def records() -> list[PluginRecord]:
    """The registry's records, as the view reads them."""
    return [
        _record("mot", "builtin", handlers=("mot", "mot-extra")),
        _record("yolo", "package:yolo-detections", handlers=("yolo-boxes",)),
        _record("old", "package:old-model-decoder", error="requires plugin API 1, host provides 2"),
    ]


class FakeInstaller:
    """Records the changes it is asked to make; holds each until released when *blocking*."""

    def __init__(self, *, blocking: bool = False, failure: str = "") -> None:
        self.made: list[PluginChange] = []
        self.release = threading.Event()
        if not blocking:
            self.release.set()
        self._failure = failure

    def __call__(self, change: PluginChange) -> None:
        self.release.wait(5)
        self.made.append(change)
        if self._failure:
            raise PluginChangeError(self._failure)


def _view(
    qtbot: QtBot, records: list[PluginRecord], installer: FakeInstaller, reload: Callable[[], None] = lambda: None
) -> PluginsView:
    def catalog() -> tuple[PluginGroup, ...]:
        return plugin_catalog(records, SELECTION, {"yolo-detections": "0.2.1"}.get)

    view = PluginsView(PluginChanges(installer), catalog, reload)
    qtbot.addWidget(view)
    view.resize(340, 600)
    view.show()
    return view


def _rows(view: PluginsView) -> list[tuple[str, str]]:
    """Return each row's title and subtitle, top to bottom."""
    rows = []
    for row in view.findChildren(ListRow):
        texts = [label.text() for label in row.findChildren(QLabel) if label.text()]
        rows.append((texts[0], texts[1] if len(texts) > 1 else ""))
    return rows


def _menu_action(view: PluginsView, name: str, action_text: str) -> None:
    button = next(button for button in view.findChildren(QToolButton) if button.accessibleName().endswith(name))
    assert button.isEnabled()
    menu = button.menu()
    assert menu is not None
    next(action for action in menu.actions() if action.text() == action_text).trigger()


def _banner(view: PluginsView) -> Banner:
    banner = view.findChild(Banner)
    assert banner is not None
    return banner


def _banner_shown(view: PluginsView) -> bool:
    return _banner(view).isVisibleTo(view)


def _add_button(view: PluginsView) -> SmallButton:
    return next(button for button in view.findChildren(SmallButton) if button.text() == "Add")


def test_the_view_lists_failed_installed_and_built_in_plugins(qtbot: QtBot, records: list[PluginRecord]) -> None:
    view = _view(qtbot, records, FakeInstaller())

    assert len(view.findChildren(SectionCaption)) == 3
    assert _rows(view) == [
        ("old-model-decoder", "Requires plugin API 1, host provides 2"),
        ("yolo-detections", "yolo-boxes"),
        ("mot", ""),
        ("mot", ""),
        ("mot-extra", ""),
    ]
    assert not _banner_shown(view)


def test_the_view_warns_while_a_plugin_has_failed_to_load(qtbot: QtBot, records: list[PluginRecord]) -> None:
    view = PluginsView(
        PluginChanges(FakeInstaller()), lambda: plugin_catalog(records, SELECTION, lambda _: None), lambda: None
    )
    qtbot.addWidget(view)

    with qtbot.waitSignal(view.warning_changed) as failed:
        view.refresh()
    records.pop()
    with qtbot.waitSignal(view.warning_changed) as healthy:
        view.refresh()

    assert (failed.args, healthy.args) == (["1 failed to load"], [""])


def test_the_initial_warning_comes_from_loaded_plugins_without_reading_the_catalog(
    qtbot: QtBot, records: list[PluginRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RuntimePluginRegistry, "list_plugins", lambda: records)
    unread = Mock(side_effect=AssertionError("the plugin selection must not be read at startup"))
    view = PluginsView(PluginChanges(FakeInstaller()), unread, lambda: None)
    qtbot.addWidget(view)

    assert view.warning() == "1 failed to load"


def test_updating_a_plugin_disables_changes_until_it_is_done_and_then_offers_a_restart(
    qtbot: QtBot, records: list[PluginRecord]
) -> None:
    installer = FakeInstaller(blocking=True)
    view = _view(qtbot, records, installer)

    _menu_action(view, "yolo-detections", "Update")

    assert ("yolo-detections", "Updating…") in _rows(view)
    assert not _add_button(view).isEnabled()
    more_buttons = [button for button in view.findChildren(QToolButton) if button.menu() and button.isVisibleTo(view)]
    assert more_buttons and not any(button.isEnabled() for button in more_buttons)
    assert not _banner_shown(view)

    installer.release.set()
    qtbot.waitUntil(lambda: _banner_shown(view), timeout=5000)
    assert installer.made == [PluginChange.update("yolo-detections", "yolo-detections>=0.2")]
    assert _add_button(view).isEnabled()
    assert ("yolo-detections", "yolo-boxes") in _rows(view)
    with qtbot.waitSignal(view.restart_requested, timeout=1000):
        _banner(view).action.click()


def test_a_failed_removal_shows_its_reason_under_the_row_and_offers_no_restart(
    qtbot: QtBot, records: list[PluginRecord]
) -> None:
    installer = FakeInstaller(failure="Plugin is not installed: old-model-decoder")
    view = _view(qtbot, records, installer)

    _menu_action(view, "old-model-decoder", "Remove")

    def failure_lines() -> list[ElidedLabel]:
        return [
            label
            for label in view.findChildren(ElidedLabel)
            if label.full_text().startswith("Plugin is not") and label.isVisibleTo(view)
        ]

    qtbot.waitUntil(lambda: len(failure_lines()) == 1, timeout=5000)
    assert installer.made == [PluginChange.remove("old-model-decoder")]
    assert failure_lines()[0].toolTip() == "Plugin is not installed: old-model-decoder"
    assert _add_button(view).isEnabled()
    assert not _banner_shown(view)


def test_adding_a_plugin_installs_what_was_entered(
    qtbot: QtBot, records: list[PluginRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    installer = FakeInstaller()
    view = _view(qtbot, records, installer)

    def enter_and_accept(dialog: AddPluginDialog) -> int:
        edit = dialog.findChild(QLineEdit)
        assert edit is not None
        edit.setText("  /home/me/my-plugin ")
        return AddPluginDialog.DialogCode.Accepted.value

    monkeypatch.setattr(AddPluginDialog, "exec", enter_and_accept)
    _add_button(view).click()

    qtbot.waitUntil(lambda: _banner_shown(view), timeout=5000)
    assert installer.made == [PluginChange.install("/home/me/my-plugin")]


def test_reload_loads_the_plugins_again_and_lists_them_anew(qtbot: QtBot, records: list[PluginRecord]) -> None:
    def reload() -> None:
        records.append(_record("lab", "package:lab-experiments"))

    view = _view(qtbot, records, FakeInstaller(), reload)
    assert ("lab-experiments", "Not loaded") not in _rows(view)

    next(button for button in view.findChildren(SmallButton) if button.accessibleName() == "Reload plugins").click()

    assert ("lab-experiments", "Not loaded") in _rows(view)


def test_the_list_is_read_again_when_the_view_is_shown(qtbot: QtBot, records: list[PluginRecord]) -> None:
    view = _view(qtbot, records, FakeInstaller())
    view.hide()
    records.append(_record("late", "package:late-plugin", error="entry-point discovery failed: No module named 'x'"))

    view.show()

    assert ("late-plugin", "Entry-point discovery failed: No module named 'x'") in _rows(view)
