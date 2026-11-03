"""The painted parts of a row table: the caption header and the one-pass painter of a row's cells."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QRect,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPalette,
)
from PySide6.QtWidgets import (
    QHeaderView,
    QWidget,
)

from ax_devil.modules.chrome.chip import paint_tag
from ax_devil.modules.chrome.copy_cell import paint_copy_icon
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.chrome.theme import category_color
from ax_devil.modules.chrome.tokens import Height, Space, TextRole
from ax_devil.modules.video_viewer.media_tools.presence import Presence, paint_presence
from ax_devil.modules.video_viewer.media_tools.row_columns import (
    MIN_ID_CHARS,
    RIGHT_ALIGNED,
    TEXT_ROLES,
    CellKind,
    Labeled,
    elide_mode,
)
from ax_devil.modules.video_viewer.media_tools.row_model import RowTableModel

_DIMMED_OPACITY = 0.5
_RULE_ALPHA = 0.12
_DIVIDER_ALPHA = 0.15
_DIVIDER_ACTIVE_ALPHA = 0.6


class RowHeader(QHeaderView):
    """Column headers in the caption style, the sorted one emphasised and marked with its direction.

    The divider after each resizable column is faint, brighter under the pointer and while it is being dragged.
    """

    def __init__(self, model: RowTableModel, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._table = model
        self._hover = -1
        self._pressed = -1
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setStretchLastSection(False)
        self.setMouseTracking(True)

    @property
    def pressed_handle(self) -> int:
        """Return the section whose divider is being dragged, or -1."""
        return self._pressed

    def _handle_at(self, x: int) -> int:
        """Return the resizable section whose right-hand divider is at *x*, or -1."""
        for section in range(self.count()):
            if self.isSectionHidden(section) or self.sectionResizeMode(section) != QHeaderView.ResizeMode.Interactive:
                continue
            if abs(x - (self.sectionViewportPosition(section) + self.sectionSize(section))) <= Space.S:
                return section
        return -1

    def _set_hover(self, section: int) -> None:
        if section != self._hover:
            self._hover = section
            self.viewport().update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Note which divider a press starts dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._handle_at(int(event.position().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Follow the divider under the pointer."""
        if self._pressed < 0:
            self._set_hover(self._handle_at(int(event.position().x())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """End a divider drag."""
        super().mouseReleaseEvent(event)
        self._pressed = -1
        self._set_hover(self._handle_at(int(event.position().x())))
        self.viewport().update()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Clear the hovered divider when the pointer leaves."""
        if event.type() == QEvent.Type.Leave and self._pressed < 0:
            self._set_hover(-1)
        return super().viewportEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:  # noqa: N802
        """Draw the title (elided to fit), the sort direction if this column is sorted, a rule under the header and
        the divider after the column.
        """
        column = self._table.columns[logicalIndex]
        sorted_column, order = self._table.sort_state()
        is_sorted = sorted_column == logicalIndex
        palette = self.palette()
        font = TextRole.CAPTION.font()
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(font)
        text_color = palette.color(QPalette.ColorRole.Text if is_sorted else QPalette.ColorRole.PlaceholderText)
        painter.setPen(text_color)
        left_pad = Space.M if logicalIndex == 0 else Space.S
        inner = rect.adjusted(left_pad, 0, -Space.S, 0)
        arrow = TextRole.CAPTION.px + Space.XS if is_sorted else 0
        right_aligned = column.cell in RIGHT_ALIGNED
        text_rect = inner.adjusted(0, 0, 0, 0)
        title = QFontMetrics(font).elidedText(column.title, Qt.TextElideMode.ElideRight, inner.width() - arrow)
        align = Qt.AlignmentFlag.AlignRight if right_aligned else Qt.AlignmentFlag.AlignLeft
        if right_aligned:
            text_rect.setRight(inner.right() - arrow)
        painter.drawText(text_rect, align | Qt.AlignmentFlag.AlignVCenter, title)
        if is_sorted:
            width = QFontMetrics(font).horizontalAdvance(title)
            size = TextRole.CAPTION.px
            x = inner.right() - size + 1 if right_aligned else inner.left() + width + Space.XS
            icon = (Icon.CHEVRON_UP if order == Qt.SortOrder.AscendingOrder else Icon.MENU).icon(text_color)
            icon.paint(painter, QRect(x, rect.top() + (rect.height() - size) // 2, size, size))
        rule = QColor(palette.color(QPalette.ColorRole.Text))
        rule.setAlphaF(_RULE_ALPHA)
        painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), rule)
        if self.sectionResizeMode(logicalIndex) == QHeaderView.ResizeMode.Interactive:
            active = logicalIndex in (self._hover, self._pressed)
            divider = QColor(palette.color(QPalette.ColorRole.Text))
            divider.setAlphaF(_DIVIDER_ACTIVE_ALPHA if active else _DIVIDER_ALPHA)
            inset = Space.XS if active else Space.S
            painter.fillRect(QRect(rect.right(), rect.top() + inset, 1, rect.height() - 2 * inset), divider)
        painter.restore()


class RowPainter:
    """Paint the cells of a visible row with shared appearance resources."""

    def __init__(self, model: RowTableModel) -> None:
        self._table = model
        # What the view tells about the pointer and the last copy, for the copy column's icon.
        self.hover_row = -1
        self.copied_key: Hashable | None = None
        # The last shown column, which gets more room on its right to keep its text clear of the edge and the copy icon.
        self.last_section = -1
        self.tail_px = 0
        # The row and the elision of the cell being painted, for the painters.
        self._row = -1
        self._elide = Qt.TextElideMode.ElideRight
        self._painters: dict[CellKind, Callable[[QPainter, QRect, QPalette, Any, QColor], None]] = {
            CellKind.ID_DOT: self._paint_id_dot,
            CellKind.TEXT: self._paint_text,
            CellKind.MONO: self._paint_mono,
            CellKind.NUMBER: self._paint_number,
            CellKind.PRESENCE: self._paint_presence,
            CellKind.TAG: self._paint_tag,
            CellKind.ID_CHIPS: self._paint_chips,
            CellKind.COPY: self._paint_copy,
        }
        self.load_metrics()

    def load_metrics(self) -> None:
        """Read the fonts and sizes for the current text size."""
        roles = set(TEXT_ROLES.values())
        self._fonts = {role: role.font() for role in roles}
        self._metrics = {role: QFontMetrics(font) for role, font in self._fonts.items()}
        self._row_height = Height.ROW.px
        self._dot = max(6, TextRole.BODY.px * 2 // 3)
        self.tail_px = Space.S + TextRole.BODY.px // 3

    def chrome_px(self, kind: CellKind) -> int:
        """Return the width a cell of *kind* spends on anything but its text."""
        if kind is CellKind.ID_DOT:
            return self._dot + Space.S
        return 2 * Space.M if kind is CellKind.TAG else 0

    def shown_text(self, row: int, section: int, width: int) -> str | None:
        """Return the text of a cell as painted in a column *width* wide, or ``None`` if it has no text."""
        column = self._table.columns[section]
        full = column.cell.text_of(column.value(self._table.row_at(row)))
        if full is None:
            return None
        room = self._inner(QRect(0, 0, width, self._row_height), section, column.cell).width()
        return self._metrics[TEXT_ROLES[column.cell]].elidedText(
            full, elide_mode(column), max(0, room - self.chrome_px(column.cell))
        )

    def _inner(self, rect: QRect, section: int, kind: CellKind) -> QRect:
        """Return *rect* inside the cell padding; the copy cell has none."""
        if kind is CellKind.COPY:
            return rect
        right = Space.S + (self.tail_px if section == self.last_section else 0)
        return rect.adjusted(Space.M if section == 0 else Space.S, 0, -right, 0)

    def paint(self, painter: QPainter, rect: QRect, row: int, header: QHeaderView) -> None:
        """Read the row once and paint only columns that intersect the viewport."""
        model = self._table
        record = model.row_at(row)
        # The application's palette: a view's own has the theme stylesheet's selection colors as its highlight.
        palette = QGuiApplication.palette()
        dimmed = model.is_dimmed(row)
        text = palette.color(QPalette.ColorRole.PlaceholderText if dimmed else QPalette.ColorRole.Text)
        self._row = row
        for position, column in enumerate(model.columns):
            if header.isSectionHidden(position):
                continue
            cell = QRect(
                header.sectionViewportPosition(position), rect.top(), header.sectionSize(position), rect.height()
            )
            if not cell.intersects(rect):
                continue
            self._elide = elide_mode(column)
            painter.save()
            painter.setClipRect(cell, Qt.ClipOperation.IntersectClip)
            if dimmed:
                painter.setOpacity(_DIMMED_OPACITY)
            self._painters[column.cell](
                painter, self._inner(cell, position, column.cell), palette, column.value(record), text
            )
            painter.restore()

    def _text(self, painter: QPainter, rect: QRect, role: TextRole, text: QColor, value: str, flags: int = 0) -> None:
        painter.setFont(self._fonts[role])
        painter.setPen(text)
        shown = self._metrics[role].elidedText(value, self._elide, rect.width())
        painter.drawText(rect, flags | int(Qt.AlignmentFlag.AlignVCenter), shown)

    def _paint_text(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.BODY, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_mono(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.MONO, text, value, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_number(self, painter: QPainter, rect: QRect, palette: QPalette, value: str, text: QColor) -> None:
        self._text(painter, rect, TextRole.MONO, text, value, int(Qt.AlignmentFlag.AlignRight))

    def _paint_id_dot(self, painter: QPainter, rect: QRect, palette: QPalette, value: Labeled, text: QColor) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(category_color(value.category, palette))
        painter.drawEllipse(QRect(rect.left(), rect.top() + (rect.height() - self._dot) // 2, self._dot, self._dot))
        after_dot = rect.adjusted(self.chrome_px(CellKind.ID_DOT), 0, 0, 0)
        self._text(painter, after_dot, TextRole.MONO, text, value.text, int(Qt.AlignmentFlag.AlignLeft))

    def _paint_presence(self, painter: QPainter, rect: QRect, palette: QPalette, value: Presence, text: QColor) -> None:
        paint_presence(
            painter,
            rect,
            palette,
            spans=value.spans,
            frame_count=value.frame_count,
            event_frames=value.event_frames,
            current_frame=value.current_frame,
            color=category_color(value.category, palette),
        )

    def _paint_tag(self, painter: QPainter, rect: QRect, palette: QPalette, value: Labeled, text: QColor) -> None:
        paint_tag(painter, rect, value.text, category_color(value.category, palette))

    def _paint_chips(
        self, painter: QPainter, rect: QRect, palette: QPalette, value: Sequence[Labeled], text: QColor
    ) -> None:
        """Draw the ids as muted text joined by commas: in full if they fit, else each shortened in the middle, else
        the first few and ``+n`` for the rest.
        """
        metrics = self._metrics[TextRole.MONO]
        ids = [label.text for label in value]
        separator = metrics.horizontalAdvance(", ")
        smallest = MIN_ID_CHARS * metrics.horizontalAdvance("0")
        shown: list[str] = []
        for count in range(len(ids), 0, -1):
            more = f", +{len(ids) - count}" if count < len(ids) else ""
            room = rect.width() - metrics.horizontalAdvance(more) - separator * (count - 1)
            each = room // count
            if sum(metrics.horizontalAdvance(i) for i in ids[:count]) <= room:
                shown = ids[:count]
            elif count == 1 or each >= smallest:
                shown = [metrics.elidedText(i, Qt.TextElideMode.ElideMiddle, each) for i in ids[:count]]
            else:
                continue
            shown[-1] += more
            break
        painter.setFont(self._fonts[TextRole.MONO])
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), ", ".join(shown))

    def _paint_copy(self, painter: QPainter, rect: QRect, palette: QPalette, value: object, text: QColor) -> None:
        copied = self.copied_key is not None and self._table.key_at(self._row) == self.copied_key
        paint_copy_icon(painter, rect, palette, bright=self._row == self.hover_row, copied=copied)
