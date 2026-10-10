"""Workspace Items resolving into Content: each built-in kind, identity, and resolution errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.modules.workspace.core import (
    ITEM_KINDS,
    FileOverlaySourceSpec,
    ItemResolutionError,
    LiveOverlayMode,
    LiveRTSPOverlaySourceSpec,
    LiveStreamItem,
    LiveVideoContent,
    OverlayFile,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    PlaylistSettings,
    SeekableVideoContent,
    VideoItem,
    WorkspaceDecoderOption,
    WorkspaceIntake,
)
from tests.helpers.contents import make_video
from tests.helpers.workspace import FakeResolutionContext, StaticDecoderOptions

_CONTEXT = FakeResolutionContext(
    WorkspaceIntake(
        StaticDecoderOptions(
            file_options=(WorkspaceDecoderOption(handler_type="FILE", display_name="File"),),
            live_options=(WorkspaceDecoderOption(handler_type="LIVE", display_name="Live"),),
        )
    )
)


def _playlist(name: str) -> PlaylistContent:
    return PlaylistContent(
        display_name=name,
        entries=(PlaylistEntry(lanes=make_video(f"{name}.mp4").standalone_lanes(), default_considered=True),),
    )


class _Resolver:
    """A playlist resolver that records the settings it was given and returns fixed playlists."""

    def __init__(self, playlists: list[PlaylistContent], error: Exception | None = None) -> None:
        self.playlists = playlists
        self.error = error
        self.settings: list[PlaylistSettings] = []

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        self.settings.append(settings)
        if self.error is not None:
            raise self.error
        return self.playlists


def test_item_kinds_are_registered_under_the_strings_written_to_workspace_files() -> None:
    assert ITEM_KINDS == {"video": VideoItem, "live_stream": LiveStreamItem, "playlist": PlaylistItem}


def test_video_item_resolves_to_seekable_video_named_by_its_label(tmp_path: Path) -> None:
    video = tmp_path / "lot.mp4"
    overlay = tmp_path / "lot.json"
    video.write_bytes(b"")
    overlay.write_text("{}")
    item = VideoItem(label="Parking lot", video=video, overlays=(OverlayFile(overlay, "FILE"),))

    [content] = item.resolve(_CONTEXT)

    assert isinstance(content, SeekableVideoContent)
    assert content.display_name == "Parking lot"
    assert content.source_spec.path == video
    assert [overlay.source_spec for overlay in content.overlays] == [
        FileOverlaySourceSpec(path=overlay, handler_type="FILE")
    ]
    assert content.item_id == item.id


@pytest.mark.parametrize("missing", ["video", "overlay"])
def test_video_item_with_a_missing_file_fails_with_the_path(tmp_path: Path, missing: str) -> None:
    paths = {"video": tmp_path / "lot.mp4", "overlay": tmp_path / "lot.json"}
    for name, path in paths.items():
        if name != missing:
            path.write_bytes(b"")
    item = VideoItem(label="Lot", video=paths["video"], overlays=(OverlayFile(paths["overlay"], "FILE"),))

    with pytest.raises(ItemResolutionError, match=str(paths[missing])):
        item.resolve(_CONTEXT)


def test_video_item_with_an_unknown_decoder_fails(tmp_path: Path) -> None:
    video = tmp_path / "lot.mp4"
    overlay = tmp_path / "lot.json"
    video.write_bytes(b"")
    overlay.write_text("{}")

    with pytest.raises(ItemResolutionError, match="Unknown overlay file handler type: GONE"):
        VideoItem(label="Lot", video=video, overlays=(OverlayFile(overlay, "GONE"),)).resolve(_CONTEXT)


def test_content_ids_come_from_the_item_and_stay_the_same_on_every_resolution() -> None:
    first = PlaylistItem(label="Runs", resolver="runs")
    second = PlaylistItem(label="Runs", resolver="runs")
    context = FakeResolutionContext(resolvers={"runs": _Resolver([_playlist("a"), _playlist("b")])})

    resolved = first.resolve(context)

    assert [content.content_id for content in resolved] == [content.content_id for content in first.resolve(context)]
    assert [content.item_id for content in resolved] == [first.id, first.id]
    assert len({content.content_id for content in resolved}) == 2
    assert {content.content_id for content in resolved}.isdisjoint(
        content.content_id for content in second.resolve(context)
    )


def test_renaming_keeps_the_item_id_and_an_empty_or_default_name_returns_to_the_default() -> None:
    item = VideoItem(video=Path("/clips/a.mp4"))

    renamed = item.with_label("Gate")

    assert (renamed.id, renamed.label, renamed.video) == (item.id, "Gate", item.video)
    assert (renamed.with_label("").label, renamed.with_label("").display_name) == ("", "a.mp4")
    assert renamed.with_label("a.mp4").label == ""


def test_items_without_a_label_are_shown_by_a_name_derived_from_their_recipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Video: file name; live: expanded host; playlist: the resolver's own names. None of it is stored."""
    monkeypatch.setenv("TEST_CAMERA_ADDR", "10.0.0.7")
    video = tmp_path / "lot.mp4"
    video.write_bytes(b"")
    live = LiveStreamItem(host="$TEST_CAMERA_ADDR")
    context = FakeResolutionContext(
        _CONTEXT.intake, resolvers={"runs": _Resolver([_playlist("train"), _playlist("test")])}
    )

    assert [content.display_name for content in VideoItem(video=video).resolve(context)] == ["lot.mp4"]
    assert [content.display_name for content in live.resolve(context)] == ["10.0.0.7"]
    assert live.with_label("10.0.0.7").label == ""
    playlists = PlaylistItem(resolver="runs").resolve(context)
    assert [content.display_name for content in playlists] == ["train", "test"]
    assert live.to_json(None)["label"] == "" and live.to_json(None)["host"] == "$TEST_CAMERA_ADDR"


def test_live_stream_item_keeps_references_and_expands_them_only_when_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_CAMERA_ADDR", "camera.local")
    monkeypatch.setenv("TEST_CAMERA_PASS", "s3cret")
    monkeypatch.setenv("TEST_BROKER_USER", "broker-user")
    item = LiveStreamItem(
        label="Entrance",
        host="$TEST_CAMERA_ADDR",
        username="root",
        password="$TEST_CAMERA_PASS",
        overlay_mode=LiveOverlayMode.RTSP,
        handler_type="LIVE",
        mqtt_username="$TEST_BROKER_USER",
        mqtt_password="pa$$word",
    )

    [content] = item.resolve(_CONTEXT)

    assert isinstance(content, LiveVideoContent)
    assert content.display_name == "Entrance"
    spec = content.source_spec
    assert (spec.host, spec.username, spec.password) == ("camera.local", "root", "s3cret")
    assert content.overlay_spec == LiveRTSPOverlaySourceSpec(handler_type="LIVE")
    assert (item.host, item.password) == ("$TEST_CAMERA_ADDR", "$TEST_CAMERA_PASS")
    assert (item.expanded().mqtt_username, item.expanded().mqtt_password) == ("broker-user", "pa$$word")


def test_live_stream_item_whose_host_reference_is_unset_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_CAMERA_ADDR", raising=False)

    with pytest.raises(ItemResolutionError, match="device host"):
        LiveStreamItem(label="Entrance", host="$TEST_CAMERA_ADDR").resolve(_CONTEXT)


def test_live_stream_item_representation_hides_passwords() -> None:
    item = LiveStreamItem(
        label="Entrance",
        host="camera.local",
        password="synthetic-device-secret",
        stream_url="rtsp://user:synthetic-url-secret@camera.local/stream",
        mqtt_password="synthetic-broker-secret",
    )

    for secret in ("synthetic-device-secret", "synthetic-url-secret", "synthetic-broker-secret"):
        assert secret not in repr(item)


def test_playlist_item_runs_its_resolver_with_its_settings_and_takes_its_label() -> None:
    resolver = _Resolver([_playlist("MOT")])
    item = PlaylistItem(label="Exp 3", resolver="mot", settings={"root": "/runs/exp3"})

    [content] = item.resolve(FakeResolutionContext(resolvers={"mot": resolver}))

    assert resolver.settings == [{"root": "/runs/exp3"}]
    assert isinstance(content, PlaylistContent)
    assert content.display_name == "Exp 3"


def test_playlist_item_with_several_playlists_names_each_after_the_item() -> None:
    context = FakeResolutionContext(resolvers={"runs": _Resolver([_playlist("train"), _playlist("test")])})

    contents = PlaylistItem(label="Exp 3", resolver="runs").resolve(context)

    assert [content.display_name for content in contents] == ["Exp 3 / train", "Exp 3 / test"]


@pytest.mark.parametrize(
    ("resolvers", "message"),
    [
        ({}, "'runs' is not installed"),
        ({"runs": _Resolver([], ValueError("Setting 'root' is missing."))}, "Setting 'root' is missing."),
        ({"runs": _Resolver([], FileNotFoundError("Folder gone"))}, "Folder gone"),
        ({"runs": _Resolver([])}, "Exp 3 has nothing to open"),
    ],
)
def test_playlist_item_that_cannot_resolve_fails_with_a_reason(resolvers: dict[str, _Resolver], message: str) -> None:
    item = PlaylistItem(label="Exp 3", resolver="runs")

    with pytest.raises(ItemResolutionError, match=message):
        item.resolve(FakeResolutionContext(resolvers=resolvers))


def test_playlist_item_keeps_its_own_copy_of_the_settings_from_the_caller_and_the_resolver() -> None:
    class _MutatingResolver:
        def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
            cast(dict[str, Any], settings)["root"] = "/changed/by/plugin"
            return [_playlist("MOT")]

    caller_settings: dict[str, Any] = {"root": "/runs/exp3"}
    item = PlaylistItem(label="Exp 3", resolver="mot", settings=caller_settings)
    caller_settings["root"] = "/changed/by/caller"

    item.resolve(FakeResolutionContext(resolvers={"mot": _MutatingResolver()}))

    assert item.settings == {"root": "/runs/exp3"}
