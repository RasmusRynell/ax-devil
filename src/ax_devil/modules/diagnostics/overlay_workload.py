"""The overlay workload of a viewer's last paint, as the text the diagnostics show for it."""

from __future__ import annotations

from ax_devil.modules.diagnostics.render_metrics import ViewerSnapshot

WORKLOAD_ROWS = ("Backend", "Target", "Entities", "Primitives", "Types", "Cache", "Rebuild", "Reuse / window")
"""The rows of the workload table, in display order."""


def workload_texts(viewer: ViewerSnapshot) -> dict[str, str]:
    """Return the text of every workload row for *viewer*'s last paint; "—" where there is nothing to say."""
    last = viewer.last
    workload_values = dict.fromkeys(WORKLOAD_ROWS, "—")
    if last:
        sample = last.sample
        generation = sample.generation
        workload_values["Backend"] = sample.backend
        workload_values["Target"] = f"{sample.width} × {sample.height} logical px"
        workload_values["Primitives"] = (
            f"{sample.primitive_count} available · {sample.drawn_primitive_count} submitted for drawing"
        )
        if generation:
            workload_values["Entities"] = (
                f"{generation.input_entity_count} input → {generation.filtered_entity_count} after filtering"
            )
            workload_values["Types"] = (
                ", ".join(f"{count} {kind}" for kind, count in generation.primitive_counts) or "none"
            )
            filter_state = "reused" if generation.filter_cache_hit else "built"
            drawing_state = "reused" if generation.drawing_cache_hit else "built"
            workload_values["Cache"] = f"Scene {filter_state} · drawing {drawing_state}"
            workload_values["Rebuild"] = (
                ", ".join(reason.value for reason in generation.build_reasons) or "No drawing rebuild"
            )
        hits, total = viewer.drawing_cache_reuse
        workload_values["Reuse / window"] = f"{hits} / {total} drawing retrievals" if total else "—"
    return workload_values
