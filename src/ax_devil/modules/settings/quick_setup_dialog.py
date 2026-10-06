"""Quick Setup: theme and text size, shown once on first start and again from Help → Quick Setup.

Choices apply to the running app as soon as they are clicked, so the app itself is the preview. Closing the dialog
in any way keeps what was chosen, saves it, and records that setup is done. The dialog opens at the size the largest
text size needs, because window managers may ignore a later resize; its content keeps its natural size, centered.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from functools import partial
from typing import Generic, Optional, TypeVar

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.theme import DARK_COLORS, LIGHT_COLORS
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole, body_px
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.configuration_preferences import apply_preferences
from ax_devil.modules.settings.logging_config import get_logger
from ax_devil.modules.settings.settings import GlobalSettings
from ax_devil.modules.settings.text_size import TextSize
from ax_devil.modules.settings.theme_mode import ThemeMode

logger = get_logger(__name__)


_ChoiceT = TypeVar("_ChoiceT", ThemeMode, TextSize)

_LABEL_FLAGS = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap

_PreviewPainter = Callable[[QPainter, QRectF, QPalette], None]


class _OptionTile(QAbstractButton):
    """A selectable card: a painted preview above the option's name; arrow keys move between sibling tiles."""

    def __init__(
        self,
        label: str,
        preview_size: Callable[[int], QSize],
        paint_preview: _PreviewPainter,
        sibling_labels: Sequence[str],
    ) -> None:
        super().__init__()
        self.setText(label)
        self._sibling_labels = sibling_labels
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_size = preview_size
        self._paint_preview = paint_preview
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        follow_appearance(self, self.updateGeometry)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Return the size at the current text size."""
        return self.size_at(body_px())

    def size_at(self, text_px: int) -> QSize:
        """Size every tile in a row alike: as wide as the preview or longest word, as tall as the longest label wraps.

        Labels are measured in the selected weight so selecting a tile never clips its label.
        """
        preview = self._preview_size(text_px)
        metrics = QFontMetrics(TextRole.STRONG.font_at(text_px))
        words = (word for label in self._sibling_labels for word in label.split())
        inner_width = max(preview.width(), *(metrics.horizontalAdvance(word) for word in words))
        label_height = max(
            metrics.boundingRect(QRect(0, 0, inner_width, 0), _LABEL_FLAGS, label).height()
            for label in self._sibling_labels
        )
        return QSize(inner_width + 2 * Space.L, preview.height() + Space.M + label_height + 2 * Space.L)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Never shrink below the preview and label."""
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw the card, its preview and its label."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        # The theme's accent; Highlight is the muted selection background.
        highlight = palette.color(QPalette.ColorRole.Link)

        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isChecked():
            fill = QColor(highlight)
            fill.setAlphaF(0.12)
            painter.setBrush(fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(card, Radius.POPUP, Radius.POPUP)
        border = QColor(highlight) if self.isChecked() or self.hasFocus() else palette.color(QPalette.ColorRole.Mid)
        if self.underMouse() and not self.isChecked():
            border = QColor(highlight)
            border.setAlphaF(0.6)
        painter.setPen(QPen(border, 2.0 if self.isChecked() else 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card, Radius.POPUP, Radius.POPUP)

        preview_size = self._preview_size(body_px())
        preview = QRectF(
            (self.width() - preview_size.width()) / 2, Space.L, preview_size.width(), preview_size.height()
        )
        painter.save()
        self._paint_preview(painter, preview, palette)
        painter.restore()

        painter.setPen(palette.color(QPalette.ColorRole.Text))
        font = TextRole.STRONG.font() if self.isChecked() else TextRole.BODY.font()
        painter.setFont(font)
        label_top = int(preview.bottom()) + Space.M
        label_rect = QRect(Space.L, label_top, self.width() - 2 * Space.L, self.height() - label_top - Space.L)
        painter.drawText(label_rect, _LABEL_FLAGS, self.text())


class _TileRow(QWidget, Generic[_ChoiceT]):
    """One tile per option in a row; *on_change* receives the newly selected option."""

    def __init__(
        self,
        options: Iterable[_ChoiceT],
        current: _ChoiceT,
        on_change: Callable[[_ChoiceT], None],
        preview_size: Callable[[int], QSize],
        paint_preview: Callable[[_ChoiceT], _PreviewPainter],
    ) -> None:
        super().__init__()
        options = list(options)
        labels = [option.label for option in options]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.M)
        self._tiles: dict[_ChoiceT, _OptionTile] = {}
        for option in options:
            button = _OptionTile(option.label, preview_size, paint_preview(option), labels)
            button.setChecked(option == current)
            button.toggled.connect(partial(_report_selection, on_change, option))
            layout.addWidget(button)
            self._tiles[option] = button
        layout.addStretch(1)

    def select(self, option: _ChoiceT) -> None:
        """Select *option*'s tile, as a click would."""
        self._tiles[option].setChecked(True)

    def size_at(self, text_px: int) -> QSize:
        """Return the row's size at *text_px* body text; its tiles are all the same size."""
        tile = next(iter(self._tiles.values())).size_at(text_px)
        count = len(self._tiles)
        return QSize(count * tile.width() + (count - 1) * Space.M, tile.height())


def _report_selection(on_change: Callable[[_ChoiceT], None], option: _ChoiceT, checked: bool) -> None:
    if checked:
        on_change(option)


def _theme_preview_size(text_px: int) -> QSize:
    return QSize(round(text_px * 6.4), text_px * 4)


def _paint_window(painter: QPainter, rect: QRectF, colors: dict[str, str]) -> None:
    """Draw a miniature app window in one theme's colors."""
    painter.setPen(QColor(colors["border>input"]))
    painter.setBrush(QColor(colors["background"]))
    painter.drawRoundedRect(rect, Radius.CONTROL, Radius.CONTROL)
    painter.setPen(Qt.PenStyle.NoPen)
    inner = rect.adjusted(1, 1, -1, -1)
    title = QRectF(inner.left(), inner.top(), inner.width(), inner.height() * 0.18)
    painter.setBrush(QColor(colors["background>title"]))
    painter.drawRect(title)
    panel = QRectF(inner.left(), title.bottom(), inner.width() * 0.28, inner.bottom() - title.bottom())
    painter.setBrush(QColor(colors["background>panel"]))
    painter.drawRect(panel)

    line = inner.height() * 0.09
    text = QColor(colors["foreground"])
    text.setAlphaF(0.55)
    painter.setBrush(text)
    left = panel.right() + inner.width() * 0.08
    for row, fraction in enumerate((0.55, 0.4, 0.48)):
        top = title.bottom() + inner.height() * (0.14 + 0.17 * row)
        painter.drawRoundedRect(QRectF(left, top, inner.width() * fraction, line), line / 2, line / 2)
    painter.setBrush(QColor(colors["primary"]))
    accent_top = title.bottom() + inner.height() * 0.65
    painter.drawRoundedRect(QRectF(left, accent_top, inner.width() * 0.22, line * 1.6), line, line)


def _paint_theme(theme: ThemeMode) -> _PreviewPainter:
    def paint(painter: QPainter, rect: QRectF, _palette: QPalette) -> None:
        if theme is not ThemeMode.DARK:
            _paint_window(painter, rect, LIGHT_COLORS)
        if theme is ThemeMode.LIGHT:
            return
        if theme is ThemeMode.AUTO:
            # Dark below the diagonal, light above: the app follows whichever the system uses.
            painter.setClipRegion(
                QPolygonF([rect.topRight(), rect.bottomRight(), rect.bottomLeft()]).toPolygon(),
            )
        _paint_window(painter, rect, DARK_COLORS)

    return paint


def _text_preview_size(text_px: int) -> QSize:
    # Room for "Aa" at the largest text size, so the row does not change height when the text size does.
    largest = TextSize.largest_px()
    return QSize(largest * 2, round(largest * 1.6))


def _paint_text_size(size: TextSize) -> _PreviewPainter:
    def paint(painter: QPainter, rect: QRectF, palette: QPalette) -> None:
        font = QApplication.font()
        font.setPixelSize(size.body_px)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "Aa")

    return paint


def _text_size_at(label: QLabel, role: TextRole) -> Callable[[int], QSize]:
    """Return *label*'s text size, in *role*, at a given body text size."""
    return lambda text_px: QFontMetrics(role.font_at(text_px)).size(0, label.text())


class QuickSetupDialog(BaseDialog):
    """Pick theme and text size, with the running app as the live preview."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent=parent, title="Quick Setup", modal=True)
        self._settings = GlobalSettings()
        self._config = ConfigManager()
        self._opened_with = self._settings.snapshot()
        self._setup_content()
        self.add_button("Done", self.accept, is_default=True)

    def opening_size_hint(self) -> QSize:
        """Open large enough for the largest text size, so picking it never needs the window to grow.

        Each part is measured the same way at both sizes, so only the growth is added to the size Qt reports now. The
        content is a column, as wide as its widest part; the title bar and the button row grow by a line each.
        """
        largest, current = TextSize.largest_px(), body_px()
        sizes = [(size_at(current), size_at(largest)) for size_at in self._parts]
        width = max(new.width() for _, new in sizes) - max(old.width() for old, _ in sizes)
        height = sum(new.height() - old.height() for old, new in sizes)
        line_growth = QFontMetrics(TextRole.BODY.font_at(largest)).height() - self.fontMetrics().height()
        height += (largest - current) + line_growth
        # Layouts round each part separately; a little slack keeps that rounding from reaching a scrollbar.
        return self.sizeHint() + QSize(max(0, width) + Space.S, max(0, height) + Space.S)

    def _setup_content(self) -> None:
        self._block = block = QWidget()
        # Each part of the column, with its size at a given body text size.
        self._parts: list[Callable[[int], QSize]] = []
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.S)

        self.theme_row = _TileRow(
            ThemeMode,
            self._settings.theme,
            self._set_theme,
            _theme_preview_size,
            _paint_theme,
        )
        self._add_section(layout, "Theme", self.theme_row)
        self._parts.append(self.theme_row.size_at)
        self.text_size_row = _TileRow(
            TextSize,
            self._settings.text_size,
            self._set_text_size,
            _text_preview_size,
            _paint_text_size,
        )
        self._add_section(layout, "Text size", self.text_size_row)
        self._parts.append(self.text_size_row.size_at)

        layout.addSpacing(Space.S)
        note = QLabel("You can change these later in File → Settings.")
        note.setStyleSheet("color: palette(placeholder-text);")
        TextRole.SMALL.apply(note)
        layout.addWidget(note)
        self._parts.append(_text_size_at(note, TextRole.SMALL))

        # Spare room from opening at the largest text size goes around the content, never into it.
        content = QWidget()
        centering = QGridLayout(content)
        centering.setContentsMargins(0, 0, 0, 0)
        centering.addWidget(block, 0, 0, Qt.AlignmentFlag.AlignCenter)
        self.add_content_widget(content, stretch=1)

    def _add_section(self, layout: QVBoxLayout, title: str, row: QWidget) -> None:
        caption = QLabel(title)
        TextRole.CAPTION.apply(caption)
        layout.addWidget(caption)
        self._parts.append(_text_size_at(caption, TextRole.CAPTION))
        layout.addWidget(row)
        layout.addSpacing(Space.L)

    def _set_theme(self, theme: ThemeMode) -> None:
        self._settings.theme = theme

    def _set_text_size(self, size: TextSize) -> None:
        self._settings.text_size = size

    def done(self, result: int) -> None:
        """Save the choices in effect and record setup as done, however the dialog was closed."""
        snapshot = self._settings.snapshot()
        if snapshot == self._opened_with and self._settings.quick_setup_done:
            super().done(result)
            return
        snapshot["quick_setup_done"] = True
        try:
            apply_preferences(self._config, self._settings, snapshot, {})
        except (ValueError, OSError) as exc:
            logger.error(f"Could not save appearance choices: {exc}")
            QMessageBox.warning(self, "Quick Setup", f"Could not save these choices: {exc}")
        super().done(result)
