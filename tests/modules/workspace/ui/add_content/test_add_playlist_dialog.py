"""The Add Playlist dialog returns an item from the selected resolver."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.workspace.core import (
    FileVideoSourceSpec,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistResolver,
    PlaylistSettings,
    SeekableVideoContent,
)
from ax_devil.modules.workspace.ui.add_content.add_playlist_dialog import AddPlaylistDialog
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from tests.helpers.add_dialogs import Discovery
from tests.helpers.workspace import FakeResolutionContext, FakeResolver, inline_resolver


class _Resolver(FakeResolver):
    """Resolve settings naming an existing root into one playlist, and fail for any other root."""

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        if settings.get("root") != "/selected":
            raise ValueError(f"Folder not found: {settings.get('root')}")
        video = SeekableVideoContent(display_name="clip", source_spec=FileVideoSourceSpec(path=Path("/clip.mp4")))
        return [PlaylistContent(display_name="Playlist", entries=(PlaylistEntry(video.standalone_lanes(), True),))]


class _AnyResolverContext(FakeResolutionContext):
    """Resolve every resolver id with the test resolver."""

    def playlist_resolver(self, resolver_id: str) -> PlaylistResolver:
        return _Resolver()


@pytest.fixture
def playlist_dialog(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> AddPlaylistDialog:
    """Return the dialog with every installed resolver's form, resolving through a test resolver."""
    dialog = AddPlaylistDialog(inline_resolver(_AnyResolverContext()))
    qtbot.addWidget(dialog)
    return dialog


def test_add_playlist_dialog_returns_an_item_from_the_selected_resolver(playlist_dialog: AddPlaylistDialog) -> None:
    """A resolver submitting after the user switched away cannot close the dialog with its settings."""
    settings = playlist_dialog._settings
    first, second = list(settings._resolver_by_widget)[:2]
    second_id = settings._resolver_by_widget[second]
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(first))
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(second))

    first.submit_settings({"root": "/selected"})
    assert playlist_dialog.result() == AddPlaylistDialog.DialogCode.Rejected
    assert playlist_dialog.get_result() is None

    second.submit_settings({"root": "/selected"})
    assert playlist_dialog.result() == AddPlaylistDialog.DialogCode.Accepted
    result = playlist_dialog.get_result()
    assert result is not None
    assert isinstance(result.item, PlaylistItem)
    assert (result.item.resolver, result.item.settings) == (second_id, {"root": "/selected"})
    first.submit_settings({"root": "/selected"})
    assert playlist_dialog.get_result() is result


def test_add_playlist_dialog_stays_open_with_the_reason_when_settings_do_not_resolve(
    playlist_dialog: AddPlaylistDialog,
) -> None:
    settings = playlist_dialog._settings
    form = next(iter(settings._resolver_by_widget))
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(form))

    form.submit_settings({"root": "/gone"})

    assert playlist_dialog.result() != AddPlaylistDialog.DialogCode.Accepted
    assert playlist_dialog.get_result() is None
    assert "Folder not found: /gone" in playlist_dialog._message_label.text()


def test_closing_playlist_dialog_interrupts_its_resolution(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, discovery: Discovery
) -> None:
    """Closing a form stops its active filesystem work instead of only hiding the answer."""
    started, stopped = Event(), Event()

    def resolve(self: _Resolver, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
        started.set()
        try:
            token.wait(5)
        except Cancelled:
            stopped.set()
            raise
        raise AssertionError("Resolution was not cancelled")

    monkeypatch.setattr(_Resolver, "resolve", resolve)
    dialog = AddPlaylistDialog(ItemResolver(_AnyResolverContext()))
    qtbot.addWidget(dialog)
    settings = dialog._settings
    form = next(iter(settings._resolver_by_widget))
    settings._resolver_combo.setCurrentIndex(settings._resolver_combo.findData(form))
    form.submit_settings({"root": "/selected"})
    discovery.release_all()
    qtbot.waitUntil(started.is_set)

    dialog.reject()

    qtbot.waitUntil(stopped.is_set)
    assert dialog.get_result() is None


@pytest.mark.parametrize("available", [False, True], ids=["missing-plugin", "unsupported-restoration"])
def test_playlist_edit_cannot_submit_blank_settings_when_source_cannot_restore(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    from ax_devil.modules.plugin_system import PlaylistResolverWidget
    from tests.helpers.dialogs import visible_texts

    monkeypatch.setattr(PlaylistResolverWidget, "restore_settings", lambda self, settings: False)
    if available:
        from ax_devil.plugins.playlist_resolvers.folder_pair.settings_widget import FolderPairSettingsWidget

        monkeypatch.setattr(FolderPairSettingsWidget, "restore_settings", lambda self, settings: False)
    item = PlaylistItem(resolver="folder_pair" if available else "not-installed", settings={"keep": [1, 2]})
    dialog = AddPlaylistDialog(inline_resolver(), existing=item)
    qtbot.addWidget(dialog)
    dialog.show()
    assert any("cannot restore" in text or "unavailable" in text for text in visible_texts(dialog))
    for form in dialog.findChildren(PlaylistResolverWidget):
        form.submit_settings({})
    assert dialog.get_result() is None
    assert item.settings == {"keep": [1, 2]}
    dialog.close()
