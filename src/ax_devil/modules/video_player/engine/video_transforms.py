"""Video frame transformation helpers (aspect preservation, centering math)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransformInfo:
    """Transform metadata for video frame scaling and centering."""

    original_size: tuple[int, int]
    fitted_size: tuple[int, int]
    target_size: tuple[int, int]
    scale_factor: float
    center_offset: tuple[int, int]


def calculate_aspect_preserving_fit(content_size: tuple[int, int], container_size: tuple[int, int]) -> TransformInfo:
    """Calculate centered fit with aspect preservation.

    Args:
        content_size: Original video dimensions (width, height)
        container_size: Target container dimensions (width, height)

    Returns:
        TransformInfo with scale factor, fitted size, and centering offset
    """
    content_w, content_h = content_size
    container_w, container_h = container_size

    # Handle edge cases
    if content_w <= 0 or content_h <= 0 or container_w <= 0 or container_h <= 0:
        return TransformInfo(
            original_size=content_size,
            fitted_size=(0, 0),
            target_size=container_size,
            scale_factor=0.0,
            center_offset=(0, 0),
        )

    # Calculate aspect ratios
    content_aspect = content_w / content_h
    container_aspect = container_w / container_h

    # Determine scaling: fit to width or height
    if content_aspect > container_aspect:
        # Content is wider → fit to container width (letterboxing)
        scale_factor = container_w / content_w
        fitted_w = container_w
        fitted_h = int(content_h * scale_factor)
        offset_x = 0
        offset_y = (container_h - fitted_h) // 2
    else:
        # Content is taller or equal → fit to container height (pillarboxing)
        scale_factor = container_h / content_h
        fitted_w = int(content_w * scale_factor)
        fitted_h = container_h
        offset_x = (container_w - fitted_w) // 2
        offset_y = 0

    return TransformInfo(
        original_size=content_size,
        fitted_size=(fitted_w, fitted_h),
        target_size=container_size,
        scale_factor=scale_factor,
        center_offset=(offset_x, offset_y),
    )
