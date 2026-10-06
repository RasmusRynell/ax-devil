"""Shared frameless window chrome and dialog infrastructure."""

from ax_devil.modules.chrome.base_dialog import BaseDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow, window_uses_custom_frame
from ax_devil.modules.chrome.palette_css import palette_color_css, qcolor_to_css
from ax_devil.modules.chrome.title_bar import TitleBar
from ax_devil.modules.chrome.window_frame_controller import WindowFrameController

__all__ = [
    "BaseDialog",
    "ChromeWindow",
    "TitleBar",
    "WindowFrameController",
    "palette_color_css",
    "qcolor_to_css",
    "window_uses_custom_frame",
]
