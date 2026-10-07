"""Application appearance: light and dark palettes, readable status colors, and the body text size.

Both apply immediately; widgets that style themselves follow through ``chrome.appearance.follow_appearance``.
"""

from enum import Enum
from typing import cast

import qdarktheme
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ax_devil.modules.chrome.appearance import notify_text_size_changed
from ax_devil.modules.chrome.tokens import Height, Space, body_px

# Keep overrides in the theme engine so widgets, icons, and QPalette agree.
DARK_COLORS: dict[str, str] = {
    "background": "#121314",
    "background>list": "#191a1b",
    "background>panel": "#191a1b",
    "background>popup": "#252627",
    "background>table": "#121314",
    "background>textarea": "#121314",
    "background>title": "#191a1b",
    "foreground": "#cccccc",
    "foreground>icon": "#b0b0b0",
    "foreground>disabled": "#6e6e6e",
    "foreground>input.placeholder": "#858585",
    "border": "#252627",
    "border>input": "#393a3b",
    "input.background": "#252627",
    "primary": "#3994bc",
    "primary>list.selectionBackground": "#1e3947",
    "primary>table.selectionBackground": "#1e3947",
    "primary>selection.background": "#244758",
    "primary>textarea.selectionBackground": "#244758",
    "primary>list.inactiveSelectionBackground": "#252627",
    "primary>table.inactiveSelectionBackground": "#252627",
    "list.hoverBackground": "#252627",
    "list.alternateBackground": "#191a1b",
    "table.alternateBackground": "#191a1b",
    "tableSectionHeader.background": "#252627",
    "treeSectionHeader.background": "#252627",
    "toolbar.background": "#191a1b",
    "statusBar.background": "#191a1b",
    "scrollbar.background": "#00000000",
    "scrollbarSlider.background": "#ffffff25",
    "scrollbarSlider.hoverBackground": "#ffffff40",
}

# Cool neutral surfaces separate chrome from content; blue marks actions and selection.
LIGHT_COLORS: dict[str, str] = {
    "background": "#f6f8fb",
    "background>list": "#eef2f6",
    "background>panel": "#edf2f7",
    "background>popup": "#ffffff",
    "background>table": "#fcfdff",
    "background>textarea": "#fcfdff",
    "background>title": "#edf2f7",
    "foreground": "#263445",
    "foreground>icon": "#526477",
    "foreground>disabled": "#7c8794",
    "foreground>input.placeholder": "#65768a",
    "border": "#d5dee8",
    "border>input": "#7b8d9f",
    "input.background": "#ffffff",
    "primary": "#23678d",
    "primary>list.selectionBackground": "#dceaf4",
    "primary>table.selectionBackground": "#dceaf4",
    "primary>selection.background": "#dceaf4",
    "primary>textarea.selectionBackground": "#dceaf4",
    "primary>list.inactiveSelectionBackground": "#e3e9f0",
    "primary>table.inactiveSelectionBackground": "#e3e9f0",
    "list.hoverBackground": "#e3ebf3",
    "list.alternateBackground": "#f1f5f9",
    "table.alternateBackground": "#f1f5f9",
    "tableSectionHeader.background": "#e7edf4",
    "treeSectionHeader.background": "#e7edf4",
    "toolbar.background": "#edf2f7",
    "statusBar.background": "#edf2f7",
    "scrollbar.background": "#00000000",
    "scrollbarSlider.background": "#52647745",
    "scrollbarSlider.hoverBackground": "#52647770",
}

THEME_COLORS: dict[str, str | dict[str, str]] = {"[dark]": DARK_COLORS, "[light]": LIGHT_COLORS}


def setup_theme(app: QApplication, mode: str, size_px: int) -> None:
    """Apply both palettes and the body text size, and keep custom Qt painting in sync with OS theme changes."""
    # Finish after QDarkTheme has applied both its stylesheet and partial palette;
    # changing the palette inside its notification would interrupt Qt propagation.
    app.paletteChanged.connect(_sync_palette, Qt.ConnectionType.QueuedConnection)
    # The theme engine installs its style on first use, which resets fonts to the platform's; size them afterwards.
    apply_theme(mode)
    apply_text_size(size_px)


def apply_text_size(size_px: int) -> None:
    """Make *size_px* the body text size of every widget, keeping the system's typeface."""
    app = cast(QApplication, QApplication.instance())
    font = app.font()
    font.setPixelSize(size_px)
    app.setFont(font)
    notify_text_size_changed()
    # Re-polishes widgets, which keep a stylesheet-polished font until the stylesheet is applied again.
    _install_stylesheet(app)
    notify_text_size_changed()


_TEXT_SIZE_RULES = "\n/* ax-devil: rules sized from the body text */\n"


def _install_stylesheet(app: QApplication) -> None:
    """Apply the theme engine's stylesheet plus rules that the engine sizes in fixed pixels."""
    theme_rules = app.styleSheet().split(_TEXT_SIZE_RULES)[0]
    # Group-box titles sit in the box's top margin; give them room for the current text size.
    title_room = body_px() // 2
    # QSS minimum heights describe the content box. Allow Qt to grow it for taller fonts or icons.
    # Equal vertical padding aligns adjacent fields/buttons; default buttons have no border in QDarkTheme.
    control_content = Height.CONTROL.px - 2 * Space.XS - 2
    sized_rules = f"""
        QGroupBox {{ margin-top: {title_room + Space.XS}px; padding-top: {title_room}px; }}
        QLineEdit, QAbstractSpinBox, QComboBox, QPushButton {{
            min-height: {control_content}px;
            padding-top: {Space.XS}px;
            padding-bottom: {Space.XS}px;
        }}
        QPushButton:default, QPushButton:flat {{
            padding-top: {Space.XS + 1}px;
            padding-bottom: {Space.XS + 1}px;
        }}
    """
    app.setStyleSheet(f"{theme_rules}{_TEXT_SIZE_RULES}{sized_rules}")


def apply_theme(mode: str) -> None:
    """Change appearance immediately, including starting or stopping OS theme following."""
    qdarktheme.setup_theme(mode, custom_colors=THEME_COLORS)
    _sync_palette(QApplication.palette())


def _sync_palette(_palette: QPalette) -> None:
    # QDarkTheme's stylesheet palette only supplies text/link roles. Custom
    # painters also need its surfaces, borders and selection colors, rather
    # than the native platform's (usually light) defaults.
    app = cast(QApplication, QApplication.instance())
    palette = app.palette()
    theme = "dark" if palette.color(QPalette.ColorRole.Text).lightnessF() > 0.5 else "light"
    complete = qdarktheme.load_palette(theme, custom_colors=THEME_COLORS)
    if palette != complete:
        app.setPalette(complete)
        # Resolve palette(...) rules after the complete palette is installed.
        _install_stylesheet(app)


class StatusColor(Enum):
    """Semantic foreground colors with contrast on light and dark surfaces."""

    SUCCESS = ("#9adf9a", "#246b36")
    WARNING = ("#ffcc66", "#875600")
    ERROR = ("#f14c4c", "#b42332")

    def color(self, palette: QPalette) -> QColor:
        """Return the foreground appropriate to the surface's palette."""
        dark = palette.color(QPalette.ColorRole.Text).lightnessF() > 0.5
        return QColor(self.value[0 if dark else 1])

    def css(self, palette: QPalette) -> str:
        """Return the stylesheet declaration that colors text with this status on the surface's palette."""
        return f"color: {self.color(palette).name()};"
