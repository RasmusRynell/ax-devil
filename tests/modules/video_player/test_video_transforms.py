"""test_transforms.py.

Comprehensive tests for video frame transformation utilities.
Tests all core math: aspect preservation and centering.
"""

from ax_devil.modules.video_player.engine.video_transforms import calculate_aspect_preserving_fit


class TestAspectPreservingFit:
    """Test aspect ratio calculations and centering."""

    def test_common_aspect_fit_cases(self) -> None:
        cases = (
            ((1920, 1080), (800, 600), (800, 450), 800 / 1920, (0, 75)),
            ((1024, 768), (1920, 1080), (1440, 1080), 1080 / 768, (240, 0)),
            ((1920, 1080), (960, 540), (960, 540), 0.5, (0, 0)),
            ((320, 240), (1280, 960), (1280, 960), 4.0, (0, 0)),
            ((2560, 400), (800, 600), (800, 125), 800 / 2560, (0, 237)),
            ((400, 2560), (800, 600), (93, 600), 600 / 2560, (353, 0)),
        )

        for original_size, target_size, fitted_size, scale_factor, center_offset in cases:
            result = calculate_aspect_preserving_fit(original_size, target_size)
            assert result.original_size == original_size
            assert result.target_size == target_size
            assert result.fitted_size == fitted_size
            assert result.scale_factor == scale_factor
            assert result.center_offset == center_offset


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_edge_cases(self) -> None:
        zero_container = calculate_aspect_preserving_fit((1920, 1080), (0, 0))
        assert zero_container.fitted_size == (0, 0)
        assert zero_container.center_offset == (0, 0)

        zero_content = calculate_aspect_preserving_fit((0, 0), (800, 600))
        assert zero_content.fitted_size == (0, 0)
        assert zero_content.center_offset == (0, 0)

        single_pixel = calculate_aspect_preserving_fit((1, 1), (800, 600))
        assert single_pixel.fitted_size == (600, 600)
        assert single_pixel.center_offset == (100, 0)

        result = calculate_aspect_preserving_fit((1920, 1080), (801, 601))
        assert isinstance(result.fitted_size[0], int)
        assert isinstance(result.fitted_size[1], int)
        assert isinstance(result.center_offset[0], int)
        assert isinstance(result.center_offset[1], int)
