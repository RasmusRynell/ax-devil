"""Shared constants for video player components.

Centralizes all timing, sizing, and styling constants used across
video player widgets for consistent behavior and easy customization.
"""

from ax_devil.modules.chrome.tokens import Space

# ===== FADE ANIMATION TIMING =====
# Consistent fade in/out timing for all floating controls
DEFAULT_FADE_DURATION = 200  # milliseconds
DEFAULT_AUTO_HIDE_DELAY = 200  # milliseconds
MOUSE_IDLE_HIDE_DELAY = 2000  # milliseconds

# ===== TRANSPARENCY VALUES =====
# Alpha values for semi-transparent overlays
INFO_OVERLAY_BACKGROUND_ALPHA = 128  # Semi-transparent background for info overlays

# ===== DRAGGABLE HANDLE SETTINGS =====
HANDLE_WIDTH = 30
HANDLE_HEIGHT = 60
HANDLE_ICON_SIZE_PX = 24
DRAG_THRESHOLD_PIXELS = 2  # Minimum movement before drag starts

# ===== PANEL ANIMATION SETTINGS =====
PANEL_ANIMATION_DURATION = 400  # milliseconds
PANEL_COLLAPSED_WIDTH = 0
PANEL_EXPANDED_WIDTH = 400
PANEL_MAX_PANE_SHARE = 0.5  # Open panels take at most this share of their pane, unless their content needs more

# ===== UI STYLING =====
CONTROL_PANEL_PADDING = Space.M  # Internal padding from video edges

# ===== TIMELINE SLIDER =====
TIMELINE_GROOVE_HEIGHT = 3  # Height of the track
TIMELINE_GROOVE_HOVER_HEIGHT = 5  # Height of the track while the pointer is over it
TIMELINE_HANDLE_RADIUS = 6  # Radius of the draggable knob, shown while the pointer is over the track
TIMELINE_CACHED_ALPHA = 45  # Alpha for decoded and cached frames, kept faint so it does not read as played progress
TIMELINE_TRACK_ALPHA_ENABLED = 90  # Alpha for full track when enabled
TIMELINE_TRACK_ALPHA_DISABLED = 40  # Alpha for full track when disabled
TIMELINE_PROGRESS_ALPHA_ENABLED = 220  # Alpha for played section when enabled
TIMELINE_PROGRESS_ALPHA_DISABLED = 100  # Alpha for played section when disabled
