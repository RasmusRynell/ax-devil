"""Workspace Items resolving into Content: each built-in kind, identity, and resolution errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.core.cancellation import NEVER, CancelToken
from ax_devil.core.source_paths import Fingerprint, SourcePaths
from ax_devil.modules.workspace.core import (
    ITEM_KINDS,
    FileOverlaySourceSpec,
    ItemResolution,
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
    entry_target,
    find_entry,
)
from tests.helpers.contents import make_video
from tests.helpers.workspace import FakeResolutionContext, FakeResolver, StaticDecoderOptions

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


class _Resolver(FakeResolver):
    """A playlist resolver that records the settings it was given and returns fixed playlists."""

    def __init__(self, playlists: list[PlaylistContent], error: Exception | None = None) -> None:
        self.playlists = playlists
        self.error = error
        self.settings: list[PlaylistSettings] = []

    def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
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

    [content] = item.resolve(_CONTEXT, NEVER)

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
        item.resolve(_CONTEXT, NEVER)


def test_video_item_with_an_unknown_decoder_fails(tmp_path: Path) -> None:
    video = tmp_path / "lot.mp4"
    overlay = tmp_path / "lot.json"
    video.write_bytes(b"")
    overlay.write_text("{}")

    with pytest.raises(ItemResolutionError, match="Unknown overlay file handler type: GONE"):
        VideoItem(label="Lot", video=video, overlays=(OverlayFile(overlay, "GONE"),)).resolve(_CONTEXT, NEVER)


def test_content_ids_come_from_the_item_and_stay_the_same_on_every_resolution() -> None:
    first = PlaylistItem(label="Runs", resolver="runs")
    second = PlaylistItem(label="Runs", resolver="runs")
    context = FakeResolutionContext(resolvers={"runs": _Resolver([_playlist("a"), _playlist("b")])})

    resolved = first.resolve(context, NEVER)

    assert [content.content_id for content in resolved] == [
        content.content_id for content in first.resolve(context, NEVER)
    ]
    assert [content.item_id for content in resolved] == [first.id, first.id]
    assert len({content.content_id for content in resolved}) == 2
    assert {content.content_id for content in resolved}.isdisjoint(
        content.content_id for content in second.resolve(context, NEVER)
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

    assert [content.display_name for content in VideoItem(video=video).resolve(context, NEVER)] == ["lot.mp4"]
    assert [content.display_name for content in live.resolve(context, NEVER)] == ["10.0.0.7"]
    assert live.with_label("10.0.0.7").label == ""
    playlists = PlaylistItem(resolver="runs").resolve(context, NEVER)
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

    [content] = item.resolve(_CONTEXT, NEVER)

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
        LiveStreamItem(label="Entrance", host="$TEST_CAMERA_ADDR").resolve(_CONTEXT, NEVER)


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

    [content] = item.resolve(FakeResolutionContext(resolvers={"mot": resolver}), NEVER)

    assert resolver.settings == [{"root": "/runs/exp3"}]
    assert isinstance(content, PlaylistContent)
    assert content.display_name == "Exp 3"


def test_playlist_item_with_several_playlists_names_each_after_the_item() -> None:
    context = FakeResolutionContext(resolvers={"runs": _Resolver([_playlist("train"), _playlist("test")])})

    contents = PlaylistItem(label="Exp 3", resolver="runs").resolve(context, NEVER)

    assert [content.display_name for content in contents] == ["Exp 3 / train", "Exp 3 / test"]


def test_playlists_sharing_a_name_get_entries_with_distinct_bookmark_targets_that_stay_the_same_on_every_run() -> None:
    context = FakeResolutionContext(
        resolvers={"runs": _Resolver([_playlist("runs"), _playlist("runs"), _playlist("runs")])}
    )
    item = PlaylistItem(resolver="runs")

    contents = item.resolve(context, NEVER)
    keys = [key for content in contents for key in content.bookmark_entries]

    assert len(set(keys)) == len(keys) == 3
    second = entry_target(contents[1], 0)
    assert find_entry(contents, second) == (contents[1], 0)
    assert find_entry(item.resolve(context, NEVER), second) is not None
    assert find_entry(item.with_label("Renamed").resolve(context, NEVER), second) is not None


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
        item.resolve(FakeResolutionContext(resolvers=resolvers), NEVER)


def test_playlist_item_keeps_its_own_copy_of_the_settings_from_the_caller_and_the_resolver() -> None:
    class _MutatingResolver(FakeResolver):
        def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
            cast(dict[str, Any], settings)["root"] = "/changed/by/plugin"
            return [_playlist("MOT")]

    caller_settings: dict[str, Any] = {"root": "/runs/exp3"}
    item = PlaylistItem(label="Exp 3", resolver="mot", settings=caller_settings)
    caller_settings["root"] = "/changed/by/caller"

    item.resolve(FakeResolutionContext(resolvers={"mot": _MutatingResolver()}), NEVER)

    assert item.settings == {"root": "/runs/exp3"}


def test_a_resolution_carries_what_the_item_reads_as_it_was_before_resolving(tmp_path: Path) -> None:
    """A file rewritten while resolving differs from the result's fingerprint, so watching it reports the change."""
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("one", encoding="utf-8")

    class _Rewriting(FakeResolver):
        def source_paths(self, settings: PlaylistSettings) -> SourcePaths:
            return SourcePaths(files=frozenset({manifest}))

        def resolve(self, settings: PlaylistSettings, token: CancelToken) -> list[PlaylistContent]:
            manifest.write_text("rewritten while resolving", encoding="utf-8")
            return [_playlist("MOT")]

    context = FakeResolutionContext(resolvers={"runs": _Rewriting()})

    resolution = ItemResolution.of(PlaylistItem(resolver="runs"), context, NEVER)

    assert resolution.error is None
    assert resolution.sources.fingerprints[manifest] != Fingerprint.of(manifest)


def test_only_live_streams_have_hosts_and_a_reference_lists_the_expanded_one_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TEST_CAMERA_ADDR", "10.0.0.7")

    assert LiveStreamItem(host="$TEST_CAMERA_ADDR").hosts == ("$TEST_CAMERA_ADDR", "10.0.0.7")
    assert LiveStreamItem(host="192.0.2.10").hosts == ("192.0.2.10",)
    assert VideoItem(video=tmp_path / "clip.mp4").hosts == ()


@pytest.mark.parametrize("resolver_id", ["folder_pair", "mot_challenge"])
def test_cancelled_playlist_scan_interrupts_directory_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolver_id: str
) -> None:
    """An active scan stops at cancellation instead of returning partial content or an item error."""
    from collections.abc import Iterator

    from ax_devil.core.cancellation import Cancelled
    from ax_devil.modules.workspace.core import ItemResolution
    from ax_devil.plugins.playlist_resolvers.folder_pair.plugin import FolderPairResolverPlugin
    from ax_devil.plugins.playlist_resolvers.mot_challenge.plugin import MOTChallengeResolverPlugin

    videos = tmp_path / "videos"
    videos.mkdir()
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    token = CancelToken()
    enumeration_finished = False

    def interrupted_directory(_path: Path) -> Iterator[Path]:
        nonlocal enumeration_finished
        token.cancel()
        yield videos / "a.mp4"
        enumeration_finished = True

    monkeypatch.setattr(Path, "iterdir", interrupted_directory)
    context = FakeResolutionContext(
        resolvers={"folder_pair": FolderPairResolverPlugin(), "mot_challenge": MOTChallengeResolverPlugin()}
    )
    item = PlaylistItem(
        resolver=resolver_id,
        settings={
            "root": str(videos),
            "videos_dir": str(videos),
            "overlays_dir": str(overlays),
            "handler_type": "FILE",
        },
    )

    with pytest.raises(Cancelled):
        ItemResolution.of(item, context, token)

    assert not enumeration_finished
