"""Application appearance: light and dark palettes, readable status colors, and the body text size.

Both apply immediately; widgets that style themselves follow through ``chrome.appearance.follow_appearance``.
"""

import atexit
from enum import Enum
from typing import cast
from zlib import crc32

import qdarktheme
import qdarktheme._main as qdarktheme_main
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ax_devil.modules.chrome.appearance import notify_text_size_applied, notify_text_size_changed
from ax_devil.modules.chrome.palette_css import palette_color_css
from ax_devil.modules.chrome.tokens import Height, Radius, Space, body_px

SEE_THROUGH = ".QWidget { background: transparent; }"
"""Stylesheet that lets a surface's own background through the plain containers inside it.

The application style fills every container with the window color. ``.QWidget`` matches plain containers only, so
fields, buttons and the kit's own widgets keep their fills.
"""

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

# Cool neutral surfaces separate chrome from content; blue marks focus and selection.
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
    "foreground>input.placeholder": "#5a6a7d",
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

# The video surface and its letterbox stay near-black in both themes, so footage looks the same and the white
# playback controls on the scrim stay readable.
VIDEO_CANVAS = QColor("#0e0f10")
VIDEO_CANVAS_TEXT = QColor("#8b9097")


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
    notify_text_size_applied()


_TEXT_SIZE_RULES = "\n/* ax-devil: rules sized from the body text */\n"


def _button_stylesheet(palette: QPalette) -> str:
    """Keep action buttons quiet; reserve the accent for focus and checked state, including default buttons."""
    hover = palette_color_css(palette, QPalette.ColorRole.Text, alpha=0.08)
    pressed = palette_color_css(palette, QPalette.ColorRole.Text, alpha=0.14)
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
    return f"""
        QPushButton, QPushButton:default, QPushButton:flat, QToolButton {{
            color: palette(text);
            background: transparent;
            border: 1px solid transparent;
            border-radius: {Radius.CONTROL}px;
        }}
        QPushButton, QPushButton:default, QPushButton:flat {{
            padding: {Space.XS}px {Space.M}px;
        }}
        QPushButton:hover:enabled, QPushButton:default:hover:enabled, QToolButton:hover:enabled {{
            background: {hover};
        }}
        QPushButton:checked:enabled, QPushButton:default:checked:enabled, QToolButton:checked:enabled {{
            background: {hover};
            color: palette(highlight);
        }}
        QPushButton:pressed:enabled, QPushButton:default:pressed:enabled, QToolButton:pressed:enabled {{
            background: {pressed};
        }}
        QPushButton:focus:enabled, QToolButton:focus:enabled {{
            border-color: palette(highlight);
        }}
        QPushButton:disabled, QPushButton:default:disabled, QPushButton:default:checked:disabled,
        QToolButton:disabled, QToolButton:checked:disabled {{
            color: {disabled};
            background: transparent;
            border-color: transparent;
        }}
    """


def _install_stylesheet(app: QApplication) -> None:
    """Apply the theme engine's stylesheet plus rules that the engine sizes in fixed pixels."""
    theme_rules = app.styleSheet().split(_TEXT_SIZE_RULES)[0]
    # Group-box titles sit in the box's top margin; give them room for the current text size.
    title_room = body_px() // 2
    # QSS minimum heights describe the content box. Allow Qt to grow it for taller fonts or icons.
    # Equal vertical padding and borders align adjacent fields and buttons, including default buttons.
    control_content = Height.CONTROL.px - 2 * Space.XS - 2
    # The theme draws 18 px check marks; larger text gets larger ones so they keep pace with their labels.
    indicator = max(18, round(body_px() * 1.2))
    sized_rules = f"""
        QCheckBox::indicator, QRadioButton::indicator {{ width: {indicator}px; height: {indicator}px; }}
        QGroupBox {{ margin-top: {title_room + Space.XS}px; padding-top: {title_room}px; }}
        QLineEdit, QAbstractSpinBox, QComboBox, QPushButton {{
            min-height: {control_content}px;
            padding-top: {Space.XS}px;
            padding-bottom: {Space.XS}px;
        }}
    """
    app.setStyleSheet(f"{theme_rules}{_TEXT_SIZE_RULES}{sized_rules}{_button_stylesheet(app.palette())}")


def apply_theme(mode: str) -> None:
    """Change appearance immediately, including starting or stopping OS theme following."""
    qdarktheme.setup_theme(mode, custom_colors=THEME_COLORS)
    _guard_listener_exit()
    _sync_palette(QApplication.palette())


_listener_exit_guarded = False


def _guard_listener_exit() -> None:
    """Keep the theme engine from terminating its OS-theme thread after the thread has already ended.

    The engine registers ``terminate()`` for the interpreter's exit. Terminating a thread that has finished
    (no ``gsettings``, or the monitor stopped) signals a recycled native thread and crashes the process after a
    normal shutdown. This runs first at exit because it registers after the engine does.
    """
    global _listener_exit_guarded
    listener = getattr(qdarktheme_main, "_listener", None)
    if _listener_exit_guarded or listener is None:
        return
    _listener_exit_guarded = True

    def release_finished_listener() -> None:
        if listener.isFinished():
            atexit.unregister(listener.kill)

    atexit.register(release_finished_listener)


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


def _pick(palette: QPalette, dark_and_light: tuple[str, str]) -> QColor:
    """Return the dark or the light color of a pair, by the surface *palette* belongs to."""
    dark = palette.color(QPalette.ColorRole.Text).lightnessF() > 0.5
    return QColor(dark_and_light[0 if dark else 1])


class StatusColor(Enum):
    """Semantic foreground colors with contrast on light and dark surfaces."""

    SUCCESS = ("#9adf9a", "#246b36")
    WARNING = ("#ffcc66", "#875600")
    ERROR = ("#f14c4c", "#b42332")

    def color(self, palette: QPalette) -> QColor:
        """Return the foreground appropriate to the surface's palette."""
        return _pick(palette, self.value)

    def css(self, palette: QPalette) -> str:
        """Return the stylesheet declaration that colors text with this status on the surface's palette."""
        return f"color: {self.color(palette).name()};"


class KindColor(Enum):
    """Accent colors that tell kinds of things apart, with contrast on light and dark surfaces.

    Live streams, videos and playlists each keep one color wherever they are marked, so they read apart everywhere.
    """

    LIVE = ("#e5534b", "#c4362e")
    VIDEO = ("#a8b3bf", "#526477")
    PLAYLIST = ("#b18cf0", "#6f45c2")
    OVERLAY = ("#8f9aa6", "#607080")
    INFO = ("#6db3f0", "#1f6fb5")
    MUTED = ("#858585", "#5a6a7d")

    def color(self, palette: QPalette) -> QColor:
        """Return the accent appropriate to the surface's palette."""
        return _pick(palette, self.value)


_CATEGORY_COLORS = (
    ("#6ea8f7", "#2a62c4"),
    ("#e0a35c", "#a35f12"),
    ("#8bc47c", "#3a7a2c"),
    ("#e07f9d", "#b02f5c"),
    ("#4fc3c3", "#117a7a"),
    ("#b18cf0", "#6f45c2"),
    ("#d6c25a", "#7d6a00"),
    ("#e5806b", "#b04125"),
)


def category_color(key: str, palette: QPalette) -> QColor:
    """Return a color for the category named *key* (a class or an event kind), with contrast on the surface *palette*.

    The same name gets the same color in every session, whatever its case.
    """
    return _pick(palette, _CATEGORY_COLORS[crc32(key.casefold().encode()) % len(_CATEGORY_COLORS)])
