"""Video playback speed policy for source pacing and viewer controls."""

from __future__ import annotations

MIN_PLAYBACK_SPEED = 0.01
MAX_PLAYBACK_SPEED = 10.0
DEFAULT_PLAYBACK_SPEED = 1.0
PLAYBACK_SPEED_STEP = 0.1


def clamp_playback_speed(value: float) -> float:
    """Clamp playback speed to the allowed range."""
    return max(MIN_PLAYBACK_SPEED, min(MAX_PLAYBACK_SPEED, value))


def step_playback_speed(current: float, delta_steps: int, *, floor: float | None = None) -> float:
    """Apply playback speed stepping with optional floor for button/hotkey behavior."""
    if floor is not None and delta_steps > 0 and current < floor:
        return floor

    if delta_steps == 0:
        stepped = current
    else:
        stepped = current + (PLAYBACK_SPEED_STEP * delta_steps)

    # Hundredths, as the controls show, so repeated steps do not drift.
    clamped = round(clamp_playback_speed(stepped), 2)
    if floor is None:
        return clamped
    return max(floor, clamped)


def scale_frame_period(base_period_s: float, speed: float) -> float:
    """Scale a frame period by playback speed."""
    safe_speed = clamp_playback_speed(speed)
    return base_period_s / safe_speed


def format_playback_speed(speed: float) -> str:
    """Format speed for user-facing controls, such as ``1×``, ``0.25×`` or ``1.7×``."""
    return f"{round(clamp_playback_speed(speed), 2):g}×"
