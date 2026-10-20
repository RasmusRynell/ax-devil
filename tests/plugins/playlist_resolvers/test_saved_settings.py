"""Restoring resolver forms retains the saved recipe until the user changes it."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import PlaylistSettings
from ax_devil.plugins.playlist_resolvers.folder_pair.settings_widget import FolderPairSettingsWidget
from ax_devil.plugins.playlist_resolvers.mot_challenge.settings_widget import MOTChallengeSettingsWidget
from tests.helpers.widgets import button


def _sequence(root: Path, name: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "seqinfo.ini").write_text(
        f"[Sequence]\nname={name}\nimDir=img1\nframeRate=30\nseqLength=3\nimWidth=640\nimHeight=480\nimExt=.jpg\n"
    )
    (folder / "img1").mkdir()


@pytest.mark.parametrize("explicit", [False, True], ids=["all-future-sequences", "explicit-subset"])
def test_mot_restore_preserves_selection_meaning_order_and_missing_names(
    tmp_path: Path, qtbot: QtBot, explicit: bool
) -> None:
    for name in ("A", "B", "C"):
        _sequence(tmp_path, name)
    settings: PlaylistSettings = {
        "root": str(tmp_path),
        "extension": {"retain": True},
        **({"sequences": ["B", "gone", "A"]} if explicit else {}),
    }
    widget = MOTChallengeSettingsWidget()
    qtbot.addWidget(widget)
    assert widget.restore_settings(settings)
    save = button(widget, "Save")
    qtbot.waitUntil(save.isEnabled)
    sequences = widget.findChild(QListWidget)
    assert sequences is not None
    checked = [
        item.text().split()[0]
        for item in sequences.findItems("*", Qt.MatchFlag.MatchWildcard)
        if item.checkState() == Qt.CheckState.Checked
    ]
    assert checked == (["A", "B"] if explicit else ["A", "B", "C"])
    if explicit:
        assert any("gone" in label.text() for label in widget.findChildren(QLabel))
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        save.click()
    assert submitted.args[0] == settings

    other = tmp_path / "other"
    _sequence(other, "NEW")
    root = widget.findChild(QLineEdit)
    assert root is not None
    root.setText(str(other))
    button(widget, "Scan").click()
    qtbot.waitUntil(save.isEnabled)
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        save.click()
    assert submitted.args[0] == {"root": str(other), "sequences": ["NEW"], "extension": {"retain": True}}


def test_folder_pair_restore_submits_saved_folders_decoder_and_unknown_settings(tmp_path: Path, qtbot: QtBot) -> None:
    videos, overlays = tmp_path / "videos", tmp_path / "overlays"
    videos.mkdir()
    overlays.mkdir()
    (videos / "clip.mp4").touch()
    (overlays / "clip.json").write_text("{}")
    settings: PlaylistSettings = {
        "videos_dir": str(videos),
        "overlays_dir": str(overlays),
        "handler_type": "ADF_BETA_FRAME",
        "extension": [1, 2],
    }
    widget = FolderPairSettingsWidget()
    qtbot.addWidget(widget)
    assert widget.restore_settings(settings)
    with qtbot.waitSignal(widget.settings_submitted) as submitted:
        button(widget, "Save").click()
    assert submitted.args[0] == settings
