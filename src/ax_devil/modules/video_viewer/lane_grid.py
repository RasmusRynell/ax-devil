"""Lane grid dimensions shared by offline display and export."""

import math


def lane_grid_columns(lane_count: int) -> int:
    """Return the preferred lane-grid column count."""
    if lane_count <= 2:
        return lane_count
    return math.ceil(math.sqrt(lane_count))


def lane_grid_rows(lane_count: int) -> int:
    """Return the preferred lane-grid row count."""
    columns = lane_grid_columns(lane_count)
    return math.ceil(lane_count / columns)
