"""Exporting offline lanes: the render catalog check, the frozen lane inputs and the export dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.choice_dialog import notify
from ax_devil.modules.video_viewer.offline_lane import OfflineLane


def run_lane_export(
    container: QWidget,
    lanes: list[OfflineLane],
    *,
    pause_playback: Callable[[], None],
    set_export_allowed: Callable[[bool], None],
) -> None:
    """Let the user export *lanes*, each with the render catalog it shows now.

    While a lane has none, because its first catalog is still loading, the user is told to try again and nothing is
    exported. Playback is paused for the export and stays paused; *set_export_allowed* forbids another export while
    the dialog is open.
    """
    from ax_devil.modules.video_viewer.export import ExportDialog

    if not lanes:
        return
    inputs = [lane.export_lane() for lane in lanes]
    if any(lane is None for lane in inputs):
        notify(container, "Export video", "Render catalogs are still loading. Try again in a moment.")
        return

    # Pause playback so the export has exclusive access to the decoder
    pause_playback()

    # Disable export to prevent re-entrant launches
    set_export_allowed(False)

    export_lanes = [lane for lane in inputs if lane is not None]

    try:
        with ExportDialog(export_lanes, parent=container) as dialog:
            dialog.exec()
    finally:
        set_export_allowed(True)
    # Playback intentionally stays paused — user resumes manually
