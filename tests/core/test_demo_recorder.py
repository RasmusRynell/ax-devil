"""Protect source timing, media isolation and composition in the standalone demo tool."""

import json
import os
import select
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage

from tools.demo_recorder.__main__ import isolated_environment, run_capture
from tools.demo_recorder.capture import frame_schedule, simulate_detection, wipe
from tools.demo_recorder.render import style_frame


def test_schedule_samples_variable_timestamps_and_rejects_overrun() -> None:
    """Take timing follows decoded timestamps, including duplicated output frames, without end freezes."""
    times = [1_000_000, 1_020_000, 1_090_000, 1_130_000, 1_300_000]
    assert frame_schedule(times, 0, 6, 20) == [0, 1, 2, 3, 3, 3]
    assert frame_schedule(times, 0.1, 3, 20) == [2, 3, 3]
    with pytest.raises(ValueError, match="beyond"):
        frame_schedule(times, 0, 8, 20)


def test_recorder_isolates_inherited_desktop_and_app_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A take cannot inherit the desktop connection, real app storage overrides or Qt driver settings."""
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "AX_DEVIL_TARGET_PASSWORD", "QT_PLUGIN_PATH", "QSG_RHI_BACKEND"):
        monkeypatch.setenv(key, "must-not-reach-worker")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/real/config")
    env = isolated_environment(tmp_path)
    assert env["HOME"] == str(tmp_path)
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert not any(value == "must-not-reach-worker" for value in env.values())
    assert (tmp_path / "runtime").stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(sys.platform != "linux", reason="The recorder uses Linux private displays and process groups")
def test_failed_launcher_does_not_leave_a_recording_process(tmp_path: Path) -> None:
    """Even a child ignoring SIGTERM is stopped when its launcher fails."""
    child = (
        "import os, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "sys.stdout.write(f'{os.getpid()}\\n'); sys.stdout.flush(); time.sleep(60)"
    )
    launcher = (
        "import subprocess, sys; "
        f"p = subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.PIPE); "
        "sys.stdout.buffer.write(p.stdout.readline()); sys.stdout.flush(); sys.exit(7)"
    )
    path = tmp_path / "capture.log"
    with path.open("w") as log:
        assert run_capture([sys.executable, "-c", launcher], dict(os.environ), log) == 7
    pid = int(path.read_text().strip())
    try:
        process = os.pidfd_open(pid)  # Becomes readable when the process exits, even if it is not our child.
    except ProcessLookupError:
        return  # Already gone.
    try:
        assert select.select([process], [], [], 5)[0], f"Recording child {pid} survived cleanup"
    finally:
        os.close(process)


def test_synthetic_detection_preserves_ground_truth_and_repeats(tmp_path: Path) -> None:
    """Synthetic annotations are bounded, reproducible and separate from the source file."""
    source = tmp_path / "gt.json"
    source.write_text(
        json.dumps(
            {
                "version": "1.0",
                "1": [
                    {"track_id": index, "x_min": 0.9, "x_max": 1.0, "y_min": 0.0, "y_max": 0.1, "polygon": [0, 0]}
                    for index in range(30)
                ],
            }
        )
    )
    original = source.read_bytes()
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    simulate_detection(source, first)
    simulate_detection(source, second)
    assert source.read_bytes() == original
    assert first.read_bytes() == second.read_bytes()
    objects = json.loads(first.read_text())["1"]
    assert 0 < len(objects) < 30
    assert all("polygon" not in item for item in objects)
    assert all(0 <= item["x_min"] < item["x_max"] <= 1 for item in objects)
    assert all(0 <= item["y_min"] < item["y_max"] <= 1 for item in objects)


def test_wipe_changes_only_the_video_region() -> None:
    """A catalog wipe must not replace app chrome or the unrevealed part of the video."""
    before = QImage(100, 80, QImage.Format.Format_RGB32)
    after = QImage(100, 80, QImage.Format.Format_RGB32)
    before.fill(QColor("red"))
    after.fill(QColor("blue"))
    image = wipe(before, after, QRect(20, 10, 60, 40), 0.5)
    assert image.pixelColor(25, 20) == QColor("blue")
    assert image.pixelColor(65, 20) == QColor("red")
    assert image.pixelColor(25, 60) == QColor("red")
    assert image.pixelColor(5, 5) == QColor("red")
    assert before.pixelColor(25, 20) == QColor("red")


def test_midnight_style_keeps_frame_geometry_and_center() -> None:
    """Styling preserves dimensions and the app center while adding a dark margin."""
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    styled = style_frame(image, "midnight")
    assert styled.size() == image.size()
    assert styled.pixelColor(200, 100) == QColor("white")
    assert styled.pixelColor(0, 0).lightness() < 100
    assert style_frame(image, "plain") == image
