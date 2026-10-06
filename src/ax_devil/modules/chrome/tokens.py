"""Shared type scale, spacing, corner radii and fixed heights for the application's widgets.

Widgets take sizes from here instead of literals, so the whole app keeps one compact rhythm. Text roles and heights
derive from the body text size, the application font's size, which the user's **Text size** preference sets; spacing
and radii stay fixed. Sizes are pixels; Qt scales them with the screen's device pixel ratio.
"""

from __future__ import annotations

from enum import Enum, IntEnum, unique

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication, QWidget

from ax_devil.modules.chrome.appearance import follow_appearance

DEFAULT_BODY_PX = 15


def body_px() -> int:
    """Return the body text size: the application font's pixel size once the text size is applied, else the default."""
    size = QApplication.font().pixelSize() if QApplication.instance() is not None else -1
    return size if size > 0 else DEFAULT_BODY_PX


def system_body_px() -> int:
    """Return the operating system's interface text size in pixels."""
    return QFontInfo(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)).pixelSize()


class Space(IntEnum):
    """Margins and gaps: 2 px for hairline gaps, otherwise steps of 4 px."""

    XS = 2
    S = 4
    M = 8
    L = 12
    XL = 16


class Radius(IntEnum):
    """Corner radii."""

    CONTROL = 4
    POPUP = 6


@unique
class Height(Enum):
    """Fixed heights of repeated chrome elements, as pixels added to one line of body text."""

    TITLE_BAR = 16
    PANE_HEADER = 12
    CONTROL = 10
    ROW = 8

    @property
    def px(self) -> int:
        """Return the height for the current body text size."""
        return body_px() + self.value


@unique
class TextRole(Enum):
    """Text styles of the type scale; each role builds its Qt font or gives it to a widget.

    Values are (pixels relative to body text, weight, fixed-width, uppercase).
    """

    BODY = (0, 400, False, False)
    STRONG = (0, 600, False, False)
    SMALL = (-2, 400, False, False)
    SMALL_STRONG = (-2, 600, False, False)
    CAPTION = (-3, 600, False, True)
    HEADING = (2, 600, False, False)
    DISPLAY = (14, 700, False, False)
    MONO = (-1, 400, True, False)
    MONO_SMALL = (-2, 400, True, False)

    @property
    def px(self) -> int:
        """Return this role's text size for the current body text size."""
        return body_px() + self.value[0]

    def font(self) -> QFont:
        """Return the application font, or the fixed-width system font, at this role's size and weight."""
        return self.font_at(body_px())

    def font_at(self, text_px: int) -> QFont:
        """Return this role's font as it would be with *text_px* body text, for measuring another text size."""
        offset, weight, mono, uppercase = self.value
        font = _fixed_font() if mono else QApplication.font()
        font.setPixelSize(text_px + offset)
        font.setWeight(QFont.Weight(weight))
        if uppercase:
            font.setCapitalization(QFont.Capitalization.AllUppercase)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        return font

    def apply(self, widget: QWidget) -> None:
        """Give *widget* this role's font, now and after every text-size change."""
        follow_appearance(widget, lambda: widget.setFont(self.font()))


def _fixed_font() -> QFont:
    # The generic family rich text also uses, so lists and hover cards show one monospace face.
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
