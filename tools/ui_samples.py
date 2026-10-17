"""Small, reproducible files opened by the UI screenshot scenarios."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ax_devil.modules.workspace.core import (
    Bookmark,
    BookmarkKind,
    BookmarkTarget,
    LiveStreamItem,
    OverlayFile,
    PlaylistItem,
    VideoItem,
    Workspace,
    save_workspace,
)
from ax_devil.plugins.decoders.onvif_xml.plugin import ONVIF_XML

CLIP_FPS = 30
CLIP_FRAMES = 60
SHOWN_FRAME = 30
# Object id, class, first and last frame, start and end box (left, top, width, height as frame fractions), likelihood.
TRACKS = (
    ("1", "Human", 0, 59, (0.05, 0.45, 0.10, 0.40), (0.42, 0.40, 0.10, 0.42), 0.97),
    ("2", "Vehicle", 0, 59, (0.78, 0.25, 0.15, 0.30), (0.40, 0.30, 0.15, 0.30), 0.88),
    ("3", "Human", 0, 24, (0.30, 0.35, 0.12, 0.45), (0.45, 0.36, 0.12, 0.45), 0.42),
    ("7", "Human", 25, 59, (0.48, 0.36, 0.12, 0.45), (0.65, 0.38, 0.12, 0.45), 0.46),  # 3, renamed; overlaps 1 and 2
    ("4", "Head", 0, 59, (0.15, 0.07, 0.03, 0.05), (0.75, 0.08, 0.03, 0.05), 0.71),
    ("5", "Truck", 10, 59, (0.02, 0.04, 0.95, 0.92), (0.02, 0.04, 0.95, 0.92), 0.35),  # nearly the whole frame
    ("6", "Bicycle", 0, 19, (0.60, 0.70, 0.10, 0.12), (0.70, 0.70, 0.10, 0.12), 0.64),
    ("8", "Car", 0, 29, (0.85, 0.75, 0.12, 0.10), (0.95, 0.75, 0.12, 0.10), 0.81),
)
EVENTS = {
    20: '<tt:Delete ObjectId="6"/>',
    25: '<tt:Rename><tt:from ObjectId="3"/><tt:to ObjectId="7"/></tt:Rename>',
    SHOWN_FRAME: '<tt:Delete ObjectId="8"/>',
}


def _onvif_box(left: float, top: float, width: float, height: float) -> str:
    """Return an ONVIF bounding box, whose coordinates run from -1 to 1 with y pointing up."""
    return (
        f'<tt:BoundingBox left="{2 * left - 1:.4f}" top="{1 - 2 * top:.4f}" '
        f'right="{2 * (left + width) - 1:.4f}" bottom="{1 - 2 * (top + height):.4f}"/>'
    )


def write_tracks(path: Path) -> Path:
    """Write an ONVIF metadata file for the generated clip, with tracks, deletes and a rename, and return its path."""
    lines = []
    for frame in range(CLIP_FRAMES):
        objects = []
        for object_id, class_name, first, last, start, end, likelihood in TRACKS:
            if first <= frame <= last:
                progress = (frame - first) / (last - first)
                left, top, width, height = (a + (b - a) * progress for a, b in zip(start, end))
                objects.append(
                    f'<tt:Object ObjectId="{object_id}"><tt:Appearance>'
                    f"<tt:Shape>{_onvif_box(left, top, width, height)}</tt:Shape>"
                    f'<tt:Class><tt:Type Likelihood="{likelihood}">{class_name}</tt:Type></tt:Class>'
                    "</tt:Appearance></tt:Object>"
                )
        tree = f"<tt:ObjectTree>{EVENTS[frame]}</tt:ObjectTree>" if frame in EVENTS else ""
        # Samples match frames by time, and the generated clip's frame times start at zero.
        time = datetime.fromtimestamp(frame / CLIP_FPS, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        namespace = "http://www.onvif.org/ver10/schema"
        lines.append(f'<tt:Frame xmlns:tt="{namespace}" UtcTime="{time}">{"".join(objects)}{tree}</tt:Frame>')
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def sample_workspace(folder: Path, clip: Path) -> Path:
    """Save videos, camera recipes, a playlist, bookmarks and a missing file as a real workspace."""
    tracks = write_tracks(folder / "tracks.xml")
    second = folder / "second-lane.mp4"
    shutil.copyfile(clip, second)
    video = VideoItem(video=clip, overlays=(OverlayFile(tracks, ONVIF_XML),), label="Tracked video")
    videos = folder / "night-shift" / "videos"
    overlays = folder / "night-shift" / "overlays"
    videos.mkdir(parents=True)
    overlays.mkdir()
    for index in range(1, 6):
        shutil.copyfile(clip, videos / f"run_{index:02d}.mp4")
        shutil.copyfile(tracks, overlays / f"run_{index:02d}.xml")
    playlist = PlaylistItem(
        resolver="folder_pair",
        settings={"videos_dir": str(videos), "overlays_dir": str(overlays), "handler_type": ONVIF_XML},
        label="Night shift",
    )
    target = BookmarkTarget(video.id)
    run_two = BookmarkTarget(playlist.id, "Folder Pair/run_02")
    workspace = Workspace(
        name="Parking lot",
        items=(
            video,
            VideoItem(video=second, overlays=video.overlays, label="Second lane"),
            LiveStreamItem(host="192.0.2.10", label="Entrance"),
            LiveStreamItem(host="192.0.2.10", label="Entrance"),
            LiveStreamItem(host="192.0.2.11", camera_head=2, label="Parking north"),
            playlist,
            VideoItem(video=folder / "gate.mp4"),
        ),
        bookmarks=tuple(
            Bookmark(target=mark_target, frame=frame, time_s=frame / CLIP_FPS, kind=kind, note=note)
            for mark_target, frame, kind, note in (
                (target, 20, BookmarkKind.ISSUE, "Bicycle 6 disappears"),
                (target, 25, BookmarkKind.QUESTION, "ID 3 renamed to 7 behind the car"),
                (target, SHOWN_FRAME, BookmarkKind.ISSUE, "Car 8 deleted at the frame edge"),
                (run_two, 41, BookmarkKind.ISSUE, "Truck box covers the whole frame"),
                (run_two, 54, BookmarkKind.EXAMPLE, "Clean tracks, good for the slides"),
            )
        ),
        reviewed=frozenset({target}),
    )
    path = folder / "Parking lot.ax-devil.workspace"
    save_workspace(workspace, path)
    return path
