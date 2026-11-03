"""Render context for catalog evaluation.

This module defines the context information provided to catalog evaluators
at render time, allowing them to resolve aspect-ratio-aware drawing instructions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Context provided to catalog evaluators at render time.

    Contains all the information needed to create properly sized and positioned
    drawing instructions for the current display, including pre-calculated spacing units.
    """

    width: int  # Target render width in pixels
    height: int  # Target render height in pixels
    aspect_ratio: float  # width / height
    scale_factor: float  # min(width, height) for scaling elements

    # Base conversion factors in normalized [0,1] coordinates
    px_h: float  # Convert horizontal pixels to normalized (1 pixel width)
    px_v: float  # Convert vertical pixels to normalized (1 pixel height)
    px_min: float  # Convert pixels based on min dimension (1 pixel min)

    @staticmethod
    def create(width: int, height: int) -> RenderContext:
        """Factory method that creates RenderContext with base conversion factors.

        Args:
            width: Target render width in pixels
            height: Target render height in pixels

        Returns:
            RenderContext with conversion factors for pixel-to-normalized conversion
        """
        if width <= 0 or height <= 0:
            raise ValueError("Render target dimensions must be positive.")
        aspect_ratio = width / height
        scale_factor = min(width, height)

        # Pre-calculate base conversion factors for performance
        px_h = 1.0 / width  # Convert 1 horizontal pixel to normalized
        px_v = 1.0 / height  # Convert 1 vertical pixel to normalized
        px_min = 1.0 / scale_factor  # Convert 1 pixel based on min dimension

        return RenderContext(
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
            scale_factor=scale_factor,
            px_h=px_h,
            px_v=px_v,
            px_min=px_min,
        )
