"""The rows of the fields tree: a labelled field and a frame event, each painting and copying itself."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import dataclass

from PySide6.QtCore import (
    QRect,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPalette,
)

from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Space, TextRole
from ax_devil.modules.data_sources.scene_history import FrameEvent
from ax_devil.modules.object_inspection.frame_labels import format_frame_time
from ax_devil.modules.object_inspection.pinned_fields import FieldId
from ax_devil.modules.scene.inspection import FieldOrigin, InspectionField
from ax_devil.modules.scene.model import RGB

_EVENT_TEXT_CHARS = (
    4  # An event's text is left out when it has less room than this, rather than shown as a lone ellipsis.
)
_BAR_CHARS = 10


@dataclass(frozen=True, slots=True)
class RowFonts:
    """The fonts and their metrics a row paints with, for the current text size."""

    body: QFont
    mono: QFont
    caption: QFont
    body_metrics: QFontMetrics
    mono_metrics: QFontMetrics
    small_metrics: QFontMetrics

    @classmethod
    def current(cls) -> RowFonts:
        """Return the fonts for the current text size."""
        body, mono = TextRole.BODY.font(), TextRole.MONO.font()
        return cls(
            body,
            mono,
            TextRole.CAPTION.font(),
            QFontMetrics(body),
            QFontMetrics(mono),
            QFontMetrics(TextRole.SMALL.font()),
        )


def _draw_text(
    painter: QPainter,
    rect: QRect,
    font: QFont,
    color: QColor,
    text: str,
    elide: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
) -> None:
    painter.setFont(font)
    painter.setPen(color)
    shown = QFontMetrics(font).elidedText(text, elide, rect.width())
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), shown)


class Row(ABC):
    """One line under a section: it has a *key* that identifies it across updates, paints itself and says what
    copying it puts on the clipboard. A row that is an event carries it in *event*; a row with a *label* has it
    drawn in the view's label column; a row with a *field_id* can be pinned, and one that is not *copyable* has
    nothing to copy. A row that only heads others is not *counted* in its section's size.
    """

    event: FrameEvent | None = None
    label: str = ""
    field_id: FieldId | None = None
    copyable: bool = True
    counted: bool = True

    @property
    @abstractmethod
    def key(self) -> Hashable:
        """Return what identifies this row when the same section is shown again with new values."""

    @abstractmethod
    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the row inside *rect*, its label in the first *label_width* pixels."""

    @abstractmethod
    def copy_text(self) -> str:
        """Return the full text the copy icon and Ctrl+C put on the clipboard."""

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width the row's label needs in the label column, 0 if it has none."""
        return 0

    def value_px(self, fonts: RowFonts) -> int:
        """Return the width the row's value needs, 0 if it has none."""
        return 0

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return the full text of the row if painting it in *rect* cuts anything off, else ``None``."""
        return None


def format_field_value(value: str | int | float | bool) -> str:
    """Return *value* as the tree shows it: booleans lowercase, floats to four significant digits."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:.1f}" if abs(value) >= 1e4 else f"{value:.4g}"
    return str(value)


class FieldRow(Row):
    """A *label* in muted text and its *text* in monospace, with a thin bar for a score, or after a colored *tag*.
    A *swatch* is drawn in that color before the label.

    Copying gives *copy*, the value in full (the text itself if not given); a long value is cut in the way *elide*
    says. An *unavailable* row stands for a pinned field the object does not have: its text is muted and there is
    nothing to copy.
    """

    def __init__(
        self,
        label: str,
        text: str,
        *,
        copy: str | None = None,
        fraction: float | None = None,
        tag: str | None = None,
        elide: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        field_id: FieldId | None = None,
        unavailable: bool = False,
        swatch: RGB | None = None,
    ) -> None:
        self.label = label
        self.field_id = field_id
        self.copyable = not unavailable
        self._unavailable = unavailable
        self._text = text
        self._copy = text if copy is None else copy
        self._fraction = fraction
        self._tag = tag
        self._elide = elide
        self._swatch = swatch

    @property
    def key(self) -> Hashable:
        """Return the field id, else the label: a color name repeats under each attribute."""
        return self.field_id if self.field_id is not None else self.label

    def _swatch_px(self, fonts: RowFonts) -> int:
        """Return the indent under the heading, the swatch and the gap after it, or 0 without a swatch."""
        return 0 if self._swatch is None else Space.M + fonts.small_metrics.ascent() + Space.S

    def _paint_label(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts) -> None:
        """Draw the swatch, if any, then the label in what room is left; with no room only the swatch shows."""
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        if self._swatch is not None:
            side = fonts.small_metrics.ascent()
            box = QRect(rect.left() + Space.M, rect.top() + (rect.height() - side) // 2, side, side)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(palette.color(QPalette.ColorRole.Mid))
            painter.setBrush(QColor(self._swatch.r, self._swatch.g, self._swatch.b))
            painter.drawRoundedRect(box.adjusted(0, 0, -1, -1), Space.XS, Space.XS)
            painter.restore()
            rect = rect.adjusted(self._swatch_px(fonts), 0, 0, 0)
            if rect.width() < fonts.body_metrics.averageCharWidth():
                return
        _draw_text(painter, rect, fonts.body, muted, self.label, Qt.TextElideMode.ElideMiddle)

    def _areas(self, rect: QRect, fonts: RowFonts, label_width: int) -> tuple[QRect, QRect, QRect | None]:
        """Return the label's area, the value's, and the score bar's groove if the value leaves room for one."""
        label = QRect(rect.left(), rect.top(), label_width, rect.height())
        left = rect.left() + label_width + Space.M
        value = QRect(left, rect.top(), max(0, rect.right() - left + 1), rect.height())
        bar_width = _BAR_CHARS * fonts.mono_metrics.averageCharWidth()
        if self._fraction is None or value.width() < 2 * bar_width:
            return label, value, None
        groove = QRect(value.right() - bar_width + 1, value.top() + (value.height() - Space.S) // 2, bar_width, Space.S)
        value.setRight(groove.left() - Space.M)
        return label, value, groove

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width of the swatch and the label."""
        return self._swatch_px(fonts) + fonts.body_metrics.horizontalAdvance(self.label) + 1

    def value_px(self, fonts: RowFonts) -> int:
        """Return the width of the tag and the text."""
        return self._tag_width(fonts) + fonts.mono_metrics.horizontalAdvance(self._text) + 1

    def _tag_width(self, fonts: RowFonts) -> int:
        """Return the width of the tag and the gap after it, or 0 without a tag."""
        if self._tag is None:
            return 0
        return fonts.small_metrics.horizontalAdvance(self._tag) + 2 * Space.M + Space.M

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the label, then the value with its bar at the right edge when it has one."""
        label, value, groove = self._areas(rect, fonts, label_width)
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        self._paint_label(painter, label, palette, fonts)
        if groove is not None and self._fraction is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(palette.color(QPalette.ColorRole.Mid))
            painter.drawRoundedRect(groove, Space.XS, Space.XS)
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            filled = QRect(groove.left(), groove.top(), round(groove.width() * self._fraction), groove.height())
            painter.drawRoundedRect(filled, Space.XS, Space.XS)
        if self._tag is not None:
            value.setLeft(
                value.left() + paint_tag(painter, value, self._tag, category_color(self._tag, palette)) + Space.M
            )
        if self._unavailable:
            font = QFont(fonts.body)
            font.setItalic(True)
            _draw_text(painter, value, font, muted, self._text)
            return
        _draw_text(painter, value, fonts.mono, palette.color(QPalette.ColorRole.Text), self._text, self._elide)

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return ``label: value`` in full if the label or the value is cut off; a swatch row always, for its color."""
        label, value, _ = self._areas(rect, fonts, label_width)
        if (
            self._swatch is not None
            or self._swatch_px(fonts) + fonts.body_metrics.horizontalAdvance(self.label) > label.width()
            or fonts.mono_metrics.horizontalAdvance(self._text) + self._tag_width(fonts) > value.width()
        ):
            return f"{self.label}: {self._copy}"
        return None

    def copy_text(self) -> str:
        """Return the value in full."""
        return self._copy


def field_row(field: InspectionField, group: str) -> FieldRow:
    """Return *field* of the group titled *group* as a row; a float copies with all its digits."""
    value = field.value
    copy = str(value) if isinstance(value, float) else format_field_value(value)
    rgb = field.rgb
    if rgb is not None:
        copy = f"{copy} {QColor(rgb.r, rgb.g, rgb.b).name()}"
    return FieldRow(
        field.path[-1] if rgb is not None else ".".join(field.path),
        field.text or format_field_value(value),
        copy=copy,
        fraction=field.fraction,
        field_id=FieldId(group, field.path),
        swatch=rgb,
    )


class HeadingRow(Row):
    """The name of the attribute whose colors follow, across the whole row; it has nothing to copy or pin."""

    copyable = False
    counted = False

    def __init__(self, path: tuple[str, ...]) -> None:
        self._path = path
        self.label = path[-1].replace("_", " ")

    @property
    def key(self) -> Hashable:
        """Return the attribute's path."""
        return self._path

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the name in muted text, cut at the right."""
        _draw_text(painter, rect, fonts.body, palette.color(QPalette.ColorRole.PlaceholderText), self.label)

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return the attribute's full path, which the name leaves out."""
        return ".".join(self._path)

    def copy_text(self) -> str:
        """Return nothing."""
        return ""


class OriginCaptionRow(Row):
    """The caption over one origin's fields in a group with source-supplied fields; nothing to copy, pin or count."""

    copyable = False
    counted = False

    def __init__(self, origin: FieldOrigin) -> None:
        self._origin = origin
        self.label = origin.title

    @property
    def key(self) -> Hashable:
        """Return the origin."""
        return self._origin

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the origin's title in muted caption text, cut at the right."""
        _draw_text(painter, rect, fonts.caption, palette.color(QPalette.ColorRole.PlaceholderText), self.label)

    def copy_text(self) -> str:
        """Return nothing."""
        return ""


def field_rows(fields: Sequence[InspectionField], group: str) -> Iterator[Row]:
    """Return *fields* of the group titled *group* as rows, each run of colors of one attribute under a heading.

    When any field came from the source, each origin's fields form a run under a caption, the source's first.
    """
    captioned = any(field.origin is FieldOrigin.SOURCE for field in fields)
    for origin in FieldOrigin:
        run = [field for field in fields if field.origin is origin]
        if captioned and run:
            yield OriginCaptionRow(origin)
        yield from _attribute_rows(run, group)


def _attribute_rows(fields: Sequence[InspectionField], group: str) -> Iterator[Row]:
    previous: tuple[str, ...] | None = None
    for field in fields:
        parent = field.path[:-1] if field.rgb is not None else None
        if parent is not None and parent != previous:
            yield HeadingRow(parent)
        previous = parent
        yield field_row(field, group)


def unavailable_row(field_id: FieldId) -> FieldRow:
    """Return the row that says the object has no *field_id*, in place of a value that would be stale or look empty."""
    return FieldRow(field_id.label, "unavailable", field_id=field_id, unavailable=True)


def id_row(entity_id: str) -> FieldRow:
    """Return the row that shows an object's *entity_id*, cut in the middle if it does not fit."""
    return FieldRow("id", entity_id, elide=Qt.TextElideMode.ElideMiddle)


class EventRow(Row):
    """An event involving the object, on the view's grid: its time in monospace as the label, then its kind as a
    colored tag and its label as the value.
    """

    def __init__(self, event: FrameEvent) -> None:
        self.event = event
        self.label = format_frame_time(event.frame_id.timestamp_us)

    @property
    def key(self) -> Hashable:
        """Return the event's frame, kind and label."""
        event = self.event
        assert event is not None
        return (event.frame_id.sequence_id, event.kind, event.label)

    def label_px(self, fonts: RowFonts) -> int:
        """Return the width of the time."""
        return fonts.mono_metrics.horizontalAdvance(self.label) + 1

    def _tag_px(self, fonts: RowFonts) -> int:
        """Return the width of the kind tag and the gap after it."""
        event = self.event
        assert event is not None
        return fonts.small_metrics.horizontalAdvance(event.kind) + 3 * Space.M

    def value_px(self, fonts: RowFonts) -> int:
        """Return the width of the tag and the label."""
        event = self.event
        assert event is not None
        return self._tag_px(fonts) + fonts.body_metrics.horizontalAdvance(event.label)

    def _value_left(self, rect: QRect, label_width: int) -> int:
        return rect.left() + label_width + Space.M

    def paint(self, painter: QPainter, rect: QRect, palette: QPalette, fonts: RowFonts, label_width: int) -> None:
        """Draw the time in the label column, then the kind tag and the label in the value column."""
        event = self.event
        assert event is not None
        _draw_text(
            painter,
            QRect(rect.left(), rect.top(), label_width, rect.height()),
            fonts.mono,
            palette.color(QPalette.ColorRole.PlaceholderText),
            self.label,
        )
        tag_left = self._value_left(rect, label_width)
        tag_width = paint_tag(
            painter,
            QRect(tag_left, rect.top(), rect.right() - tag_left + 1, rect.height()),
            event.kind,
            category_color(event.kind, palette),
        )
        text_left = tag_left + tag_width + Space.M
        if rect.right() - text_left >= _EVENT_TEXT_CHARS * fonts.body_metrics.averageCharWidth():
            _draw_text(
                painter,
                QRect(text_left, rect.top(), rect.right() - text_left + 1, rect.height()),
                fonts.body,
                palette.color(QPalette.ColorRole.Text),
                event.label,
            )

    def cut_text(self, rect: QRect, fonts: RowFonts, label_width: int) -> str | None:
        """Return the whole event if its time, kind or label is cut off."""
        event = self.event
        assert event is not None
        shown = rect.right() + 1 - self._value_left(rect, label_width) - self._tag_px(fonts)
        if (
            fonts.mono_metrics.horizontalAdvance(self.label) > label_width
            or fonts.body_metrics.horizontalAdvance(event.label) > shown
        ):
            return self.copy_text()
        return None

    def copy_text(self) -> str:
        """Return time, kind and label."""
        event = self.event
        assert event is not None
        return f"{self.label} {event.kind} {event.label}"
