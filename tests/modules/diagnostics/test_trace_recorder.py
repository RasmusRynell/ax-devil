"""Verify sampling retains early and late activity across existing Qt threads."""

import json
import sys
import tempfile
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from ax_devil.modules.diagnostics import trace_recorder
from ax_devil.modules.diagnostics.debug_window import DebugWindow
from ax_devil.modules.diagnostics.trace_recorder import TraceRecorder


class SampleWorker(QThread):
    """Expose two distinguishable phases to the sampler."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = Event()
        self.advance = Event()
        self.late = Event()
        self.finish = Event()

    def run(self) -> None:
        """Run phases in a Qt-owned thread created before recording."""
        self._early_work()
        self._late_work()

    def _early_work(self) -> None:
        self.ready.set()
        self.advance.wait(5)

    def _late_work(self) -> None:
        self.late.set()
        self.finish.wait(5)


def test_sampling_retains_beginning_and_end_across_existing_qt_thread(tmp_path: Path, qtbot: QtBot) -> None:
    recorder = TraceRecorder()
    worker = SampleWorker()
    worker.start()
    assert worker.ready.wait(5)
    try:
        recorder.start()
        qtbot.waitUntil(lambda: recorder.sample_count >= 5)
        worker.advance.set()
        assert worker.late.wait(5)
        target = recorder.sample_count + 5
        qtbot.waitUntil(lambda: recorder.sample_count >= target)
        recorder.stop()
        path = tmp_path / "samples.json"
        recorder.save(path)
        data = json.loads(path.read_text())
        assert data["format"] == "ax-devil-stack-samples"
        assert len(data["samples"]) == recorder.sample_count
        assert 0 <= data["samples"][0][0] < data["samples"][-1][0] <= data["duration_ms"]
        assert all(a[0] < b[0] for a, b in zip(data["samples"], data["samples"][1:]))
        observed: dict[str, list[float]] = {"_early_work": [], "_late_work": []}
        for time_ms, threads in data["samples"]:
            for _, stack_id in threads:
                for frame_id in data["stacks"][stack_id]:
                    name = data["frames"][frame_id].split(" (")[0]
                    if name in observed:
                        observed[name].append(time_ms)
        assert observed["_early_work"]
        assert observed["_late_work"]
        assert max(observed["_early_work"]) < min(observed["_late_work"])
        assert len(data["threads"]) >= 2
        assert "Performance sampler" not in data["threads"].values()
        # Export again without consuming the temporary sample history.
        recorder.save(tmp_path / "again.json")
        assert json.loads((tmp_path / "again.json").read_text()) == data
        recorder.start()
        qtbot.waitUntil(lambda: recorder.sample_count >= 2)
        recorder.stop()
        recorder.save(path)
        assert not any("_early_work" in name for name in json.loads(path.read_text())["frames"])
    finally:
        worker.advance.set()
        worker.finish.set()
        worker.wait(5000)
        recorder.cleanup()


def test_failed_save_retains_samples(tmp_path: Path, qtbot: QtBot) -> None:
    recorder = TraceRecorder()
    try:
        recorder.start()
        qtbot.waitUntil(lambda: recorder.sample_count >= 2)
        recorder.stop()
        invalid_path = tmp_path / "directory.json"
        invalid_path.mkdir()
        with pytest.raises(OSError):
            recorder.save(invalid_path)
        assert recorder.has_trace
        recorder.save(tmp_path / "retry.json")
    finally:
        recorder.cleanup()
    assert not recorder.recording
    assert not recorder.has_trace


def test_controls_save_retry_and_close(tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = DebugWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.show()
    controls = window.trace_controls
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    controls.record_button.click()
    qtbot.waitUntil(lambda: controls.recorder.sample_count >= 2)
    controls.record_button.click()
    assert not controls.recorder.recording
    assert controls.save_button.isEnabled()
    path = tmp_path / "controls.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    controls.save_button.click()
    assert len(json.loads(path.read_text())["samples"]) >= 2
    assert "controls.json" in controls.status.text()
    controls.record_button.click()
    window.close()
    assert not controls.recorder.recording
    assert not controls.recorder.has_trace
    assert not window.timer.isActive()


def test_sampling_failure_keeps_completed_snapshots(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = TraceRecorder()
    original = recorder._sample

    def fail_after_two() -> None:
        if recorder.sample_count == 2:
            raise OSError("disk full")
        original()

    monkeypatch.setattr(recorder, "_sample", fail_after_two)
    try:
        recorder.start()
        qtbot.waitUntil(lambda: recorder.failure is not None)
        recorder.stop()
        recorder.save(tmp_path / "partial.json")
        data = json.loads((tmp_path / "partial.json").read_text())
        assert data["failure"] == "disk full"
        assert len(data["samples"]) == 2
    finally:
        recorder.cleanup()


@pytest.mark.parametrize("registered", [True, False])
def test_reused_thread_ids_keep_separate_exported_histories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool
) -> None:
    """Reused identifiers cannot relabel old samples after replacement or observed absence."""
    first = Thread(name="first worker")
    second = Thread(name="second worker")
    monkeypatch.setattr(first, "_ident", 42)
    monkeypatch.setattr(second, "_ident", 42)
    threads = [first] if registered else []
    frames = {42: sys._getframe()}
    monkeypatch.setattr(trace_recorder, "enumerate_threads", lambda: threads)
    monkeypatch.setattr(sys, "_current_frames", lambda: frames.copy())
    recorder = TraceRecorder()
    recorder._spool = tempfile.TemporaryFile(mode="w+b")
    recorder._started_at = monotonic()
    try:
        recorder._sample()
        recorder._sample()
        if registered:
            threads[:] = [second]
        else:
            frames.clear()
            recorder._sample()
            frames[42] = sys._getframe()
        recorder._sample()
        recorder._ended_at = monotonic()
        path = tmp_path / "reused.json"
        recorder.save(path)
        data = json.loads(path.read_text())
        old_id = data["samples"][0][1][0][0]
        new_id = data["samples"][-1][1][0][0]
        assert data["samples"][1][1][0][0] == old_id
        assert old_id != new_id
        assert len(data["threads"]) == 2
        if registered:
            assert data["threads"][old_id] == "first worker"
            assert data["threads"][new_id] == "second worker"
        else:
            assert data["samples"][2][1] == []
    finally:
        recorder.cleanup()
