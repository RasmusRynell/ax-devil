"""A resolver plugin's settings form can run slow work, such as scanning a folder, without freezing the dialog."""

from __future__ import annotations

import threading

from PySide6.QtCore import QCoreApplication, QEvent
from pytestqt.qtbot import QtBot

from ax_devil.modules.plugin_system import JobContext, PlaylistResolverWidget


def test_a_form_scans_in_the_background_and_hears_back_on_the_gui_thread(qtbot: QtBot) -> None:
    form = PlaylistResolverWidget()
    qtbot.addWidget(form)
    scanned_on: list[threading.Thread] = []
    heard: list[tuple[object, threading.Thread]] = []

    def scan(_job: JobContext[None]) -> list[str]:
        scanned_on.append(threading.current_thread())
        return ["sequence-01"]

    def hear(value: object) -> None:
        heard.append((value, threading.current_thread()))

    form.jobs.submit("scan", scan, on_result=hear, on_error=hear)
    qtbot.waitUntil(lambda: len(heard) == 1)

    assert scanned_on[0] is not threading.main_thread()
    assert heard == [(["sequence-01"], threading.main_thread())]


def test_a_scan_still_running_when_its_form_is_closed_is_told_to_stop(qtbot: QtBot) -> None:
    form = PlaylistResolverWidget()
    started, release = threading.Event(), threading.Event()
    noticed: list[bool] = []

    def scan(job: JobContext[None]) -> None:
        started.set()
        assert release.wait(5)
        noticed.append(job.cancelled)

    form.jobs.submit("scan", scan, on_result=lambda _value: None, on_error=lambda _error: None)
    assert started.wait(5)
    form.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    release.set()
    qtbot.waitUntil(lambda: noticed == [True])
