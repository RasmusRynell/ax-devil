"""Tests for the sidebar row projection."""

from __future__ import annotations

from pathlib import Path

from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import KindColor, StatusColor
from ax_devil.modules.thumbnails import CameraThumbnail, FileThumbnail
from ax_devil.modules.workspace.core import (
    LiveRTSPStreamSpec,
    LiveStreamItem,
    LiveVideoContent,
    OnScreenWorkspaceItem,
    OverlaySourceKind,
    PlaylistContent,
    PlaylistEntry,
    PlaylistItem,
    VideoItem,
)
from ax_devil.modules.workspace.core.items import ItemResolution, UnreadableItem
from ax_devil.modules.workspace.ui.browser_projection import build_browser_sections
from ax_devil.modules.workspace.ui.browser_rows import RowAction, WorkspaceBrowserRow
from ax_devil.modules.workspace.ui.live_stream_status import LiveStreamState, LiveStreamStatus
from tests.helpers.browser_rows import browser_sections, item_rows, store_of, store_of_items
from tests.helpers.contents import make_live_video, make_playlist, make_video
from tests.helpers.workspace import content_item, store_with


def inline_action(row: WorkspaceBrowserRow) -> RowAction | None:
    """Return the action *row* shows as a button, if any."""
    return next((offered.action for offered in row.inline_actions), None)


def test_open_items_are_inputs_to_browser_projection() -> None:
    playlist = make_playlist()
    state, playlist = store_with(playlist)
    first_open_item = frozenset({playlist.on_screen_item(0)})
    second_open_item = frozenset({playlist.on_screen_item(1)})

    first_rows = item_rows(state, first_open_item)
    second_rows = item_rows(state, second_open_item)

    assert first_rows[0].children[0].is_open
    assert not first_rows[0].children[1].is_open
    assert not second_rows[0].children[0].is_open
    assert second_rows[0].children[1].is_open


def test_video_rows_include_overlay_children_targets_refs_and_information() -> None:
    video = make_video("test.mp4", overlay_count=2)
    state, video = store_with(video)
    state.set_item_considered(video.lane_consideration_ref(0, 1), False)
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    row = item_rows(state, open_items)[0]

    assert row.label == "test.mp4"
    assert row.icon_kind == "video"
    assert row.thumbnail == FileThumbnail(Path("/tmp/test.mp4"))
    assert row.children[0].thumbnail == row.thumbnail, "an overlay lane shows its video"
    assert row.item == state.workspace.items[0]
    assert row.is_open
    assert row.information_factory is not None
    information = row.information_factory()
    assert ("path", str(Path("/tmp/test.mp4"))) in information.fields
    assert information.children[0].title == "Overlay 1"
    assert ("path", str(Path("/tmp/Overlay 1.txt"))) in information.children[0].fields
    assert ("handler type", "AXIS_JSON") in information.children[0].fields
    assert [child.label for child in row.children] == ["Overlay 1", "Overlay 2"]
    assert row.children[0].consideration_ref == video.lane_consideration_ref(0, 0)
    assert row.children[0].is_considered
    assert not row.children[1].is_considered
    assert _offers(row.children[1])[RowAction.OPEN] == "Hidden lane: choose Unhide and open"
    assert _offers(row.children[1])[RowAction.OPEN_TO_SIDE] == "Hidden lane: choose Unhide and open"
    assert inline_action(row.children[1]) is RowAction.UNHIDE_AND_OPEN
    assert RowAction.HIDE in _offers(row.children[0]), "a lane playback uses can be hidden"
    assert RowAction.UNHIDE in _offers(row.children[1]) and RowAction.HIDE not in _offers(row.children[1])
    assert {RowAction.HIDE, RowAction.UNHIDE}.isdisjoint(_offers(row)), "the video itself is not hidden"


def test_live_rows_use_live_icon_and_embedded_overlay_child() -> None:
    live_video = make_live_video(overlay_source=OverlaySourceKind.RTSP_SOURCE)
    state, live_video = store_with(live_video)

    row = item_rows(state)[0]

    assert row.icon_kind == "live_video"
    assert [child.label for child in row.children] == ["RTSP"]
    assert row.children[0].consideration_ref is None
    assert row.activation_target is None
    assert row.children[0].activation_target is None, "a live camera starts only with its Start stream action"
    assert row.thumbnail == CameraThumbnail(live_video.content_id, live_video.source_spec.connection(3.0), 1)


def test_playlist_rows_include_entries_lanes_open_flags_and_information_payloads() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        metadata={"resolver": "Synthetic"},
        entries=(
            PlaylistEntry(
                lanes=make_video("a.mp4", overlay_count=2).standalone_lanes(),
                default_considered=True,
                metadata={"note": "first entry"},
            ),
            PlaylistEntry(lanes=make_video("b.mp4").standalone_lanes(), default_considered=True),
        ),
    )
    state, playlist = store_with(playlist)
    open_items = frozenset({playlist.on_screen_item(1)})

    row = item_rows(state, open_items)[0]
    first_entry = row.children[0]
    second_entry = row.children[1]

    assert row.label == "Suite"
    assert row.icon_kind == "playlist"
    assert row.item == state.workspace.items[0]
    assert row.is_open
    assert row.information_factory is not None
    assert ("resolver", "Synthetic") in row.information_factory().fields
    assert first_entry.label == "a.mp4"
    assert first_entry.consideration_ref == playlist.entry_consideration_ref(0)
    assert not first_entry.is_open
    assert first_entry.children[0].consideration_ref == playlist.lane_consideration_ref(0, 0)
    assert first_entry.information_factory is not None
    assert ("note", "first entry") in first_entry.information_factory().fields
    assert second_entry.is_open
    assert second_entry.thumbnail == FileThumbnail(Path("/tmp/b.mp4"))
    assert row.thumbnail is None, "a playlist shows no picture"


def test_multi_video_playlist_entry_uses_playlist_icon() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        entries=(
            PlaylistEntry(
                lanes=(
                    make_video("a.mp4").standalone_lanes()[0],
                    make_video("b.mp4").standalone_lanes()[0],
                ),
                default_considered=True,
            ),
        ),
    )
    state, playlist = store_with(playlist)

    row = item_rows(state)[0]

    assert row.children[0].icon_kind == "playlist"


def test_sections_group_rows_by_kind_in_a_fixed_order_with_counts(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"")
    store = store_of_items(
        VideoItem(video=tmp_path / "a.mp4"),
        PlaylistItem(resolver="fixed"),
        LiveStreamItem(label="cam", host="camera.local"),
        VideoItem(video=tmp_path / "b.mp4"),
        VideoItem(video=tmp_path / "missing.mp4"),
        UnreadableItem.from_raw({"kind": "future"}, "Unknown kind"),
    )

    sections = browser_sections(store)

    assert [(s.kind.title, len(s.rows)) for s in sections] == [
        ("Live", 1),
        ("Videos", 3),
        ("Playlists", 1),
        ("Other", 1),
    ]
    assert [[row.label for row in section.rows] for section in sections] == [
        ["cam"],
        ["a.mp4", "b.mp4", "missing.mp4"],
        ["Suite"],
        ["future"],
    ]
    missing = sections[1].rows[2]
    assert (missing.detail, missing.unavailable_reason) == ("", f"File not found: {tmp_path / 'missing.mp4'}")
    assert sections[3].rows[0].detail == ""


def test_sections_are_present_only_for_kinds_in_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"")

    sections = browser_sections(store_of_items(PlaylistItem(resolver="fixed"), VideoItem(video=tmp_path / "a.mp4")))

    assert [section.kind.title for section in sections] == ["Videos", "Playlists"]
    assert browser_sections(store_of_items()) == ()


def test_a_live_rows_detail_and_mark_follow_its_stream_and_its_name_is_not_repeated() -> None:
    store = store_of_items(LiveStreamItem(label="Gate", host="camera.local"), LiveStreamItem(host="dock.local"))
    named, unnamed = store.contents()

    idle_named, idle_unnamed = item_rows(store)
    (idle_section,) = browser_sections(store)

    assert (idle_named.label, idle_named.detail, idle_unnamed.detail) == ("Gate", "camera.local", "")
    assert idle_named.mark == (Icon.LIVE_VIDEO, KindColor.MUTED)
    assert idle_section.mark_color is KindColor.MUTED, "no stream is live"

    failed = LiveStreamStatus(LiveStreamState.FAILED, "Unauthorized")
    paused = LiveStreamStatus(LiveStreamState.PAUSED)
    live_now = LiveStreamStatus(LiveStreamState.LIVE)
    statuses = {named.content_id: (live_now, failed), unnamed.content_id: (paused, live_now)}
    opened = frozenset(OnScreenWorkspaceItem(kind="video", content_id=content_id) for content_id in statuses)

    failing, streaming = item_rows(store, opened, statuses)
    (section,) = browser_sections(store, opened, statuses)

    assert failing.detail == "camera.local · Unauthorized", "the most urgent viewer wins"
    assert failing.mark == (Icon.WARNING, StatusColor.WARNING)
    (failing_section,) = browser_sections(store, opened, {named.content_id: statuses[named.content_id]})
    assert failing_section.mark_color is KindColor.LIVE, "a stream is live though the row shows the failed one"
    assert (streaming.detail, streaming.mark) == ("Live", (Icon.STREAMING, KindColor.LIVE))
    assert section.mark_color is KindColor.LIVE


def test_a_playlists_tooltip_counts_entries_and_those_hidden() -> None:
    playlist = PlaylistContent(
        display_name="Suite",
        entries=tuple(
            PlaylistEntry(lanes=make_video(f"{name}.mp4").standalone_lanes(), default_considered=True) for name in "abc"
        ),
    )
    store, playlist = store_with(playlist)

    assert item_rows(store)[0].tooltip.splitlines()[0] == "Playlist · 3 entries"

    store.set_item_considered(playlist.entry_consideration_ref(1), False)

    row = item_rows(store)[0]
    assert row.detail == ""
    assert row.tooltip.splitlines()[0] == "Playlist · 3 entries, 1 hidden"


def test_a_playlist_starts_expanded_unless_it_has_too_many_entries_to_list_at_once() -> None:
    def playlist(entries: int) -> PlaylistContent:
        lanes = make_video("clip.mp4").standalone_lanes()
        entry = PlaylistEntry(lanes=lanes, default_considered=True)
        return PlaylistContent(display_name="Suite", entries=(entry,) * entries)

    short, _ = store_with(playlist(3))
    long, _ = store_with(playlist(5000))

    assert item_rows(short)[0].expanded_at_first
    assert not item_rows(long)[0].expanded_at_first
    assert not item_rows(short)[0].children[0].expanded_at_first


def test_search_text_includes_the_detail() -> None:
    live = LiveVideoContent(
        display_name="Gate", source_spec=LiveRTSPStreamSpec(host="camera.local", username="root", password="secret")
    )
    (section,) = browser_sections(store_of(live))

    assert section.rows[0].search_text == "Gate camera.local"


def test_cameras_offer_start_stream_while_idle_and_stop_stream_while_a_viewer_shows_them(tmp_path: Path) -> None:
    store = store_of_items(LiveStreamItem(host="192.0.2.10", label="Gate"))
    [live] = store.contents()

    closed = item_rows(store)[0]
    opened = item_rows(store, frozenset({OnScreenWorkspaceItem(kind="video", content_id=live.content_id)}))[0]

    assert (inline_action(closed), inline_action(opened)) == (RowAction.START_STREAM, RowAction.STOP_STREAM)
    assert (closed.is_dimmed, opened.is_dimmed) == (True, False), "a camera is dimmed while nothing shows it"


def test_only_cameras_offer_an_inline_action(tmp_path: Path) -> None:
    store = store_of_items(
        PlaylistItem(resolver="fixed"),
        PlaylistItem(label="Broken", resolver="missing"),
        VideoItem(video=tmp_path / "missing.mp4"),
    )
    store.add_items([content_item(make_video("clip.mp4", overlay_count=1))])

    rows = {row.label: row for row in item_rows(store)}

    assert inline_action(rows["Suite"]) is None
    assert inline_action(rows["Broken"]) is None and rows["Broken"].unavailable_reason is not None
    assert inline_action(rows["missing.mp4"]) is None
    assert inline_action(rows["clip.mp4"]) is None and not rows["clip.mp4"].idle
    nested = [child for row in rows.values() for child in row.children]
    nested += [lane for row in nested for lane in row.children]
    assert nested and all(inline_action(child) is None for child in nested)
    assert all(row.offered(RowAction.EDIT) is not None for row in rows.values() if row.item and row.item.editable)
    assert all(child.offered(RowAction.EDIT) is None for child in nested)


def _offers(row: WorkspaceBrowserRow) -> dict[RowAction, str]:
    """Return what *row* offers, each action with the reason it is disabled; empty for an enabled one."""
    return {offered.action: offered.disabled_reason for offered in row.actions}


def test_a_video_row_offers_export_only_once_it_is_open_and_nested_rows_offer_no_item_edits() -> None:
    state, video = store_with(make_video("test.mp4", overlay_count=1))
    open_items = frozenset({OnScreenWorkspaceItem(kind="video", content_id=video.content_id)})

    closed = item_rows(state)[0]
    opened = item_rows(state, open_items)[0]

    assert list(_offers(closed)) == [
        RowAction.OPEN,
        RowAction.OPEN_TO_SIDE,
        RowAction.INFORMATION,
        RowAction.EXPORT_VIDEO,
        RowAction.REMOVE,
    ]
    assert _offers(closed)[RowAction.EXPORT_VIDEO] != ""
    assert _offers(opened)[RowAction.EXPORT_VIDEO] == ""
    assert list(_offers(closed.children[0])) == [
        RowAction.OPEN,
        RowAction.OPEN_TO_SIDE,
        RowAction.INFORMATION,
        RowAction.HIDE,
    ]


def test_a_camera_cannot_open_or_export_and_an_unreadable_item_cannot_be_edited() -> None:
    store = store_of_items(
        LiveStreamItem(host="192.0.2.10", label="Gate"), UnreadableItem.from_raw({"kind": "radar", "id": "r1"}, "no")
    )

    camera, unreadable = item_rows(store)

    assert list(_offers(camera)) == [
        RowAction.START_STREAM,
        RowAction.INFORMATION,
        RowAction.EDIT,
        RowAction.REMOVE,
    ]
    assert RowAction.EDIT not in _offers(unreadable)
    assert _offers(unreadable)[RowAction.OPEN] == "", "opening an item that failed says why, so it is offered"
    assert _offers(unreadable)[RowAction.REMOVE] == ""


def test_an_item_still_loading_is_idle_and_marked_with_its_kind() -> None:
    items = [LiveStreamItem(host="192.0.2.10"), PlaylistItem(resolver="fixed")]

    sections = build_browser_sections([ItemResolution.pending(item) for item in items], lambda _ref: True)

    rows = [row for section in sections for row in section.rows]
    assert all(row.offered(RowAction.EDIT) is not None for row in rows)
    assert [(row.icon_kind, row.idle, inline_action(row)) for row in rows] == [
        ("live_video", True, None),
        ("playlist", True, None),
    ]
