"""Paint observations shared by the diagnostics tests."""

from ax_devil.modules.diagnostics.render_metrics import FrameIdentity, PaintSample


def sample(at: float, sequence: int = 1) -> PaintSample:
    """Build a CPU paint observation with known, disjoint stage durations."""
    return PaintSample(
        completed_at=at,
        frame=FrameIdentity(sequence, sequence * 40_000),
        overlay=None,
        overlay_reused=False,
        paint_ms=4.0,
        image_ms=1.0,
        compose_ms=1.0,
        draw_ms=1.0,
        primitive_count=0,
        drawn_primitive_count=0,
        generation=None,
        width=640,
        height=480,
    )
