"""Welcome screen widget shown when no workspace viewers are open."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.key_chips import (
    KeyChipColors,
    draw_key_chips,
    key_chip_parts,
    key_chips_height,
    key_chips_width,
)
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.workspace.core.startup_request import VideoFileStartup

if TYPE_CHECKING:
    from PySide6.QtCore import QEvent

    from ax_devil.modules.shortcuts.shortcuts import ShortcutManager

_HINT_ROW_SPACING = Space.S
_LABEL_KEY_GAP = Space.XL
_GROUP_SPACING = 2 * Space.XL
_ROW_H_PAD = Space.M
_ROW_RADIUS = Radius.CONTROL
_MAX_LABEL_WIDTH = 420.0
_RECENT_GROUP = "Recent"
_DROP_HINT = "Or drop a video file, with an optional overlay file, anywhere here"


@dataclass(frozen=True, slots=True)
class WelcomeItem:
    """One clickable welcome row: a label, its key binding text, and what clicking it does."""

    label: str
    keys: QKeySequence
    tooltip: str
    activate: Callable[[], None]


@dataclass(frozen=True, slots=True)
class _PlacedRow:
    """A welcome row measured and positioned for painting and hit-testing."""

    item: WelcomeItem
    rect: QRectF
    label: str
    key_parts: list[str]
    badge_width: float


@dataclass(frozen=True, slots=True)
class _WelcomeLayout:
    """Positions of every header, row, and the drop hint for the current widget size."""

    headers: list[tuple[str, QPointF]]
    rows: list[_PlacedRow]
    drop_hint: QPointF
    label_x: float
    badge_right: float
    minimum_size: QSize


@dataclass(frozen=True, slots=True)
class _WelcomeFonts:
    """Fonts and metrics shared by layout and painting."""

    label: QFont
    badge: QFont
    header: QFont
    label_fm: QFontMetricsF
    badge_fm: QFontMetricsF
    header_fm: QFontMetricsF


class WelcomeWidget(QWidget):
    """Show clickable VS Code-style actions, recent videos, and a drop hint centered in the empty workspace."""

    recent_video_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self._shortcut_manager: ShortcutManager | None = None
        self._recent_videos: tuple[VideoFileStartup, ...] = ()
        self._hovered: int | None = None

    def set_shortcut_manager(self, manager: ShortcutManager) -> None:
        """Bind to a ShortcutManager so rows reflect current key bindings and trigger their actions."""
        self._shortcut_manager = manager
        self.updateGeometry()
        self.update()

    def set_recent_videos(self, entries: Sequence[VideoFileStartup]) -> None:
        """Show *entries* as a Recent group; clicking one emits ``recent_video_requested``."""
        self._recent_videos = tuple(entries)
        self._hovered = None
        self.updateGeometry()
        self.update()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """Keep every row available when the host needs to scroll the welcome screen."""
        return _layout_rows(self, self._resolve_grouped_hints()).minimum_size

    def item_at(self, pos: QPointF) -> WelcomeItem | None:
        """Return the welcome row under widget position *pos*, if any."""
        rows = _layout_rows(self, self._resolve_grouped_hints()).rows
        index = _row_index_at(rows, pos)
        return rows[index].item if index is not None else None

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        """Paint the welcome rows."""
        groups = self._resolve_grouped_hints()
        if not groups:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_layout(painter, self, _layout_rows(self, groups), self._hovered)
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Highlight the row under the cursor and show its tooltip."""
        rows = _layout_rows(self, self._resolve_grouped_hints()).rows
        index = _row_index_at(rows, event.position())
        if index != self._hovered:
            self._hovered = index
            self.setCursor(Qt.CursorShape.PointingHandCursor if index is not None else Qt.CursorShape.ArrowCursor)
            self.setToolTip(rows[index].item.tooltip if index is not None else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Clear the hover highlight when the cursor leaves."""
        self._hovered = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Run the action of the clicked row."""
        item = self.item_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if item is None:
            super().mouseReleaseEvent(event)
            return
        item.activate()

    def _resolve_grouped_hints(self) -> OrderedDict[str, list[WelcomeItem]]:
        """Build grouped rows from ShortcutManager's current bindings, then recent videos."""
        sm = self._shortcut_manager
        groups: OrderedDict[str, list[WelcomeItem]] = OrderedDict()
        if sm is not None:
            for defn in sm.definitions():
                if not defn.show_on_welcome:
                    continue
                seq = sm.current_key_sequence(defn.action_id)
                groups.setdefault(defn.welcome_group or "General", []).append(
                    WelcomeItem(
                        label=defn.display_name,
                        keys=seq if seq is not None else QKeySequence(),
                        tooltip="",
                        activate=lambda action_id=defn.action_id, manager=sm: manager.get_action(action_id).trigger(),
                    )
                )
        if self._recent_videos:
            groups[_RECENT_GROUP] = [
                WelcomeItem(
                    label=entry.label,
                    keys=QKeySequence(),
                    tooltip=entry.description,
                    activate=lambda entry=entry: self.recent_video_requested.emit(entry),
                )
                for entry in self._recent_videos
            ]
        return groups


def _fonts() -> _WelcomeFonts:
    label_font = TextRole.BODY.font()
    badge_font = TextRole.MONO_SMALL.font()
    header_font = TextRole.CAPTION.font()
    return _WelcomeFonts(
        label=label_font,
        badge=badge_font,
        header=header_font,
        label_fm=QFontMetricsF(label_font),
        badge_fm=QFontMetricsF(badge_font),
        header_fm=QFontMetricsF(header_font),
    )


def _layout_rows(widget: QWidget, groups: OrderedDict[str, list[WelcomeItem]]) -> _WelcomeLayout:
    """Measure and center every group, row, and the drop hint in *widget*."""
    fonts = _fonts()
    measured: list[list[tuple[WelcomeItem, str, list[str], float]]] = []
    max_label_width = 0.0
    max_badge_width = 0.0
    for items in groups.values():
        group_rows = []
        for item in items:
            label = fonts.label_fm.elidedText(item.label, Qt.TextElideMode.ElideMiddle, _MAX_LABEL_WIDTH)
            key_parts = key_chip_parts(item.keys)
            badge_w = key_chips_width(fonts.badge_fm, key_parts)
            max_label_width = max(max_label_width, fonts.label_fm.horizontalAdvance(label))
            max_badge_width = max(max_badge_width, badge_w)
            group_rows.append((item, label, key_parts, badge_w))
        measured.append(group_rows)

    row_height = max(fonts.label_fm.height(), key_chips_height(fonts.badge_fm))
    header_height = fonts.header_fm.height()
    show_headers = len(groups) > 1
    hint_height = fonts.label_fm.height()

    total_height = 0.0
    for i, group_rows in enumerate(measured):
        if show_headers:
            total_height += header_height + _HINT_ROW_SPACING
        total_height += len(group_rows) * row_height + (len(group_rows) - 1) * _HINT_ROW_SPACING
        if i < len(measured) - 1:
            total_height += _GROUP_SPACING
    total_height += _GROUP_SPACING + hint_height

    block_width = max_label_width + _LABEL_KEY_GAP + max_badge_width
    start_x = (widget.width() - block_width) / 2
    y = (widget.height() - total_height) / 2

    headers: list[tuple[str, QPointF]] = []
    rows: list[_PlacedRow] = []
    for group_idx, (group_name, group_rows) in enumerate(zip(groups.keys(), measured)):
        if show_headers:
            headers.append((group_name, QPointF(start_x, y + fonts.header_fm.ascent())))
            y += header_height + _HINT_ROW_SPACING
        for item, label, key_parts, badge_w in group_rows:
            rect = QRectF(
                start_x - _ROW_H_PAD,
                y - _HINT_ROW_SPACING / 2,
                block_width + 2 * _ROW_H_PAD,
                row_height + _HINT_ROW_SPACING,
            )
            rows.append(_PlacedRow(item=item, rect=rect, label=label, key_parts=key_parts, badge_width=badge_w))
            y += row_height + _HINT_ROW_SPACING
        y -= _HINT_ROW_SPACING
        if group_idx < len(measured) - 1:
            y += _GROUP_SPACING
    y += _GROUP_SPACING
    hint_x = (widget.width() - fonts.label_fm.horizontalAdvance(_DROP_HINT)) / 2
    return _WelcomeLayout(
        headers=headers,
        rows=rows,
        drop_hint=QPointF(hint_x, y + fonts.label_fm.ascent()),
        label_x=start_x,
        badge_right=start_x + block_width,
        minimum_size=QSize(
            ceil(max(block_width, fonts.label_fm.horizontalAdvance(_DROP_HINT)) + 2 * _ROW_H_PAD),
            ceil(total_height + 2 * _ROW_H_PAD),
        ),
    )


def _row_index_at(rows: list[_PlacedRow], pos: QPointF) -> int | None:
    """Return the index of the row containing *pos*, if any."""
    return next((index for index, row in enumerate(rows) if row.rect.contains(pos)), None)


def _draw_layout(painter: QPainter, widget: QWidget, layout: _WelcomeLayout, hovered: int | None) -> None:
    """Draw section headers, rows with key badges, the hover highlight, and the drop hint."""
    fonts = _fonts()
    palette = widget.palette()
    label_color = palette.color(QPalette.ColorRole.Text)
    muted_color = QColor(label_color)
    muted_color.setAlphaF(0.6)

    chip_colors = KeyChipColors.for_palette(palette)
    hover_color = QColor(palette.color(QPalette.ColorRole.Highlight))
    hover_color.setAlphaF(0.14)

    painter.setFont(fonts.header)
    painter.setPen(muted_color)
    for text, pos in layout.headers:
        painter.drawText(pos, text)

    for index, row in enumerate(layout.rows):
        if index == hovered:
            path = QPainterPath()
            path.addRoundedRect(row.rect, _ROW_RADIUS, _ROW_RADIUS)
            painter.fillPath(path, hover_color)
        center_y = row.rect.center().y()
        painter.setFont(fonts.label)
        painter.setPen(label_color)
        painter.drawText(
            QPointF(layout.label_x, center_y + (fonts.label_fm.ascent() - fonts.label_fm.descent()) / 2),
            row.label,
        )
        draw_key_chips(
            painter,
            fonts.badge,
            row.key_parts,
            layout.badge_right - row.badge_width,
            center_y - key_chips_height(fonts.badge_fm) / 2,
            chip_colors,
        )

    painter.setFont(fonts.label)
    painter.setPen(muted_color)
    painter.drawText(layout.drop_hint, _DROP_HINT)
