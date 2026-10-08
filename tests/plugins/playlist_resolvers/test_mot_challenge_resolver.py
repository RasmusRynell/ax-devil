"""Tests for the MOT Challenge playlist resolver plugin."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PlaylistResolverWidget,
)
from ax_devil.modules.workspace import FileOverlaySourceSpec, OverlaySourceKind, SeekableVideoContent
from ax_devil.plugins.playlist_resolvers.mot_challenge.plugin import (
    MOTChallengeResolverPlugin,
)
from ax_devil.plugins.playlist_resolvers.mot_challenge.resolver import (
    build_playlist_contents,
    discover_sequences,
    parse_seqinfo,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEQINFO_TEMPLATE = textwrap.dedent("""\
    [Sequence]
    name={name}
    imDir=img1
    frameRate={fps}
    seqLength={length}
    imWidth={width}
    imHeight={height}
    imExt=.jpg
""")


def _make_sequence(
    root: Path,
    name: str,
    *,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    length: int = 100,
    create_det: bool = True,
    create_gt: bool = False,
) -> Path:
    """Create a minimal MOT sequence directory structure."""
    seq_dir = root / name
    seq_dir.mkdir(parents=True)
    (seq_dir / "img1").mkdir()
    ini = SEQINFO_TEMPLATE.format(name=name, fps=fps, width=width, height=height, length=length)
    (seq_dir / "seqinfo.ini").write_text(ini)

    if create_det:
        (seq_dir / "det").mkdir()
        (seq_dir / "det" / "det.txt").write_text("1,-1,100,100,50,120,0.9,-1,-1,-1\n")

    if create_gt:
        (seq_dir / "gt").mkdir()
        (seq_dir / "gt" / "gt.txt").write_text("1,1,100,100,50,120,1,1,0.8\n")

    return seq_dir


# ---------------------------------------------------------------------------
# Plugin metadata
# ---------------------------------------------------------------------------


class TestMOTChallengeResolverPluginMetadata:
    """Tests for MOTChallengeResolverPlugin identity and definition."""

    def test_definition_and_settings_widget(self, qtbot: QtBot) -> None:
        assert MOTChallengeResolverPlugin.plugin_id() == "mot_challenge"
        assert MOTChallengeResolverPlugin.plugin_type() == PLAYLIST_RESOLVER_PLUGIN_TYPE
        assert MOTChallengeResolverPlugin.display_name() == "MOT Challenge"
        assert MOTChallengeResolverPlugin.description() is not None

        defn = MOTChallengeResolverPlugin.definition()
        assert defn.plugin_type == PLAYLIST_RESOLVER_PLUGIN_TYPE
        assert defn.plugin_id == "mot_challenge"
        assert defn.display_name == "MOT Challenge"

        widget = MOTChallengeResolverPlugin().create_settings_widget()
        qtbot.addWidget(widget)
        assert isinstance(widget, PlaylistResolverWidget)


# ---------------------------------------------------------------------------
# seqinfo.ini parsing
# ---------------------------------------------------------------------------


class TestParseSeqinfo:
    def test_parses_valid_ini(self, tmp_path: Path) -> None:
        ini = tmp_path / "seqinfo.ini"
        ini.write_text(SEQINFO_TEMPLATE.format(name="MOT16-01", fps=30, width=1920, height=1080, length=450))
        result = parse_seqinfo(ini)
        assert result["name"] == "MOT16-01"
        assert result["framerate"] == "30"
        assert result["imwidth"] == "1920"

    def test_raises_on_missing_section(self, tmp_path: Path) -> None:
        ini = tmp_path / "seqinfo.ini"
        ini.write_text("[Other]\nkey=val\n")
        with pytest.raises(ValueError, match="Missing.*Sequence"):
            parse_seqinfo(ini)


# ---------------------------------------------------------------------------
# Sequence discovery
# ---------------------------------------------------------------------------


class TestDiscoverSequences:
    def test_discovers_sorted_sequences_with_metadata_and_ignores_unrelated_folders(self, tmp_path: Path) -> None:
        """Discovery accepts an empty root and yields only complete sequence folders in name order."""
        assert discover_sequences(tmp_path) == []
        (tmp_path / "random_folder").mkdir()
        _make_sequence(tmp_path, "MOT16-06", fps=14, width=640, height=480, length=1194)
        _make_sequence(tmp_path, "MOT16-01", length=450, fps=30)

        seqs = discover_sequences(tmp_path)

        assert [seq.name for seq in seqs] == ["MOT16-01", "MOT16-06"]
        assert seqs[0].frame_count == 450
        seq = seqs[1]
        assert (seq.fps, seq.width, seq.height, seq.frame_count) == (14.0, 640, 480, 1194)
        assert (seq.im_dir, seq.im_ext) == ("img1", ".jpg")

    def test_discovers_train_and_test_split_sequences_from_dataset_root(self, tmp_path: Path) -> None:
        train = tmp_path / "train"
        test = tmp_path / "test"
        _make_sequence(train, "MOT17-02-DPM", create_gt=True)
        _make_sequence(test, "MOT17-01-DPM", create_gt=False)

        seqs = discover_sequences(tmp_path)

        assert [seq.name for seq in seqs] == ["MOT17-01-DPM", "MOT17-02-DPM"]

    def test_raises_on_missing_root(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            discover_sequences(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# Playlist building
# ---------------------------------------------------------------------------


class TestBuildPlaylistContents:
    def test_builds_single_playlist_entry_with_det_overlay(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01", fps=25, width=1920, height=1080, length=100)
        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)

        assert len(playlists) == 1
        assert playlists[0].display_name == "MOT Challenge"
        assert len(playlists[0].entries) == 1

        entry = playlists[0].entries[0]
        assert len(entry.lanes) == 1
        assert entry.lanes[0].video.display_name == "SEQ-01"
        lane = entry.lanes[0]
        assert isinstance(lane.video, SeekableVideoContent)
        assert lane.video.source_spec.path == tmp_path / "SEQ-01" / "img1" / "%06d.jpg"
        assert lane.video.metadata == {}
        assert lane.overlay is not None
        assert lane.overlay.display_name == "DET"
        assert lane.overlay.source_spec == FileOverlaySourceSpec(
            path=tmp_path / "SEQ-01" / "det" / "det.txt",
            handler_type="MOT_FILE",
            decoder_kwargs={"width": 640, "height": 480},
        )
        assert lane.source_kind == OverlaySourceKind.FILE_SOURCE
        assert lane.metadata == {}
        assert lane.overlay.metadata == {}

    def test_det_and_gt_overlays_attached(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01", create_det=True, create_gt=True)
        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)

        assert len(playlists) == 1
        lanes = playlists[0].entries[0].lanes
        assert [lane.display_name for lane in lanes] == ["DET", "GT"]

    def test_no_overlays_when_det_missing(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01", create_det=False)
        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)
        assert len(playlists) == 1
        lane = playlists[0].entries[0].lanes[0]
        assert lane.overlay is None

    def test_multiple_sequences_become_multiple_entries(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "SEQ-01")
        _make_sequence(tmp_path, "SEQ-02")
        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)
        assert len(playlists) == 1
        assert len(playlists[0].entries) == 2

    def test_mot17_detector_variants_share_one_entry(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "MOT17-01-DPM")
        _make_sequence(tmp_path, "MOT17-01-FRCNN")
        _make_sequence(tmp_path, "MOT17-01-SDP")

        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)

        assert len(playlists) == 1
        assert len(playlists[0].entries) == 1
        lanes = playlists[0].entries[0].lanes
        assert lanes[0].video.display_name == "MOT17-01"
        assert len(lanes) == 3
        assert [lane.display_name for lane in lanes] == ["DPM", "FRCNN", "SDP"]

    def test_grouped_detector_variants_deduplicate_gt_overlay(self, tmp_path: Path) -> None:
        _make_sequence(tmp_path, "MOT17-01-DPM", create_gt=True)
        _make_sequence(tmp_path, "MOT17-01-FRCNN", create_gt=True)
        _make_sequence(tmp_path, "MOT17-01-SDP", create_gt=True)

        seqs = discover_sequences(tmp_path)
        playlists = build_playlist_contents(seqs)

        assert len(playlists) == 1
        assert len(playlists[0].entries) == 1
        lanes = playlists[0].entries[0].lanes
        assert lanes[0].video.display_name == "MOT17-01"
        assert len(lanes) == 4
        assert [lane.display_name for lane in lanes] == ["DPM", "GT", "FRCNN", "SDP"]
