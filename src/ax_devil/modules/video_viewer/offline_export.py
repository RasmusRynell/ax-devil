"""Exporting offline lanes: the render catalog check, the frozen lane inputs and the export dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from ax_devil.modules.video_viewer.offline_lane import OfflineLane
from ax_devil.modules.video_viewer.overlay_persistence import OverlayPersistencePolicy
from ax_devil.modules.video_viewer.scene_frame_presenter import SceneFramePresenter


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
    from ax_devil.modules.video_viewer.export import ExportDialog, ExportLane

    if not lanes:
        return
    catalogs = [lane.presenter.scene_render_catalog if lane.presenter is not None else None for lane in lanes]
    if any(catalog is None for catalog in catalogs):
        QMessageBox.warning(container, "Export video", "Render catalogs are still loading. Try again in a moment.")
        return

    # Pause playback so the export has exclusive access to the decoder
    pause_playback()

    # Disable export to prevent re-entrant launches
    set_export_allowed(False)

    export_lanes: list[ExportLane] = []
    for lane, active_catalog in zip(lanes, catalogs, strict=True):
        assert lane.video_source is not None
        # Snapshot filter state so it can't change during export
        frozen_filter = lane.filter_model.snapshot() if lane.filter_model is not None else None

        # Export uses the same timestamp selection settings as the viewer.
        export_lanes.append(
            ExportLane(
                name=lane.name or lane.content.display_name,
                video_source=lane.video_source,
                presenter=SceneFramePresenter(scene_filter=frozen_filter, scene_render_catalog=active_catalog),
                overlay_source=lane.overlay_source,
                overlay_policy=(
                    OverlayPersistencePolicy(lane.overlay_policy.settings.copy())
                    if lane.overlay_policy is not None
                    else None
                ),
            )
        )

    try:
        with ExportDialog(export_lanes, parent=container) as dialog:
            dialog.exec()
    finally:
        set_export_allowed(True)
    # Playback intentionally stays paused — user resumes manually
