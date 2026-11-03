"""HUD painting helpers for display widget overlays and background text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.theme import VIDEO_CANVAS, VIDEO_CANVAS_TEXT
from ax_devil.modules.chrome.tokens import Space, TextRole


@dataclass(frozen=True, slots=True)
class _InfoOverlaySection:
    title: str
    rows: tuple[tuple[str, Any], ...]


class HudPainter:
    """Static helpers for display HUD rendering."""

    @staticmethod
    def draw_background_content(painter: QPainter, widget: QWidget, background_text: str) -> None:
        painter.fillRect(widget.rect(), VIDEO_CANVAS)
        painter.setFont(widget.font())
        painter.setPen(VIDEO_CANVAS_TEXT)

        padding = 20
        text_rect = widget.rect().adjusted(padding, padding, -padding, -padding)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, background_text)

    @staticmethod
    def draw_info_overlay(
        painter: QPainter,
        widget: QWidget,
        stats: dict[str, Any],
        *,
        background_alpha: int,
    ) -> None:
        bg_color = widget.palette().color(QPalette.ColorRole.Window)
        bg_color.setAlpha(background_alpha)
        tab_color = widget.palette().color(QPalette.ColorRole.AlternateBase)
        border_color = widget.palette().color(QPalette.ColorRole.Mid)
        text_color = widget.palette().color(QPalette.ColorRole.Text)
        font = TextRole.MONO_SMALL.font()
        painter.setFont(font)
        metrics = QFontMetrics(font)
        line_height = metrics.height() + Space.XS
        padding = Space.M
        tab_padding_x = Space.M
        tab_height = line_height + Space.S
        row_gap = Space.S
        section_gap = Space.M
        max_width = widget.width() - 2 * (Space.M + padding)
        max_height = widget.height() - 2 * (Space.M + padding)

        # Format and elide to the widget first; rows past its height are cut, the last one kept standing for them.
        shown: list[tuple[str, list[str]]] = []
        content_height = 0
        cut = False
        for section in HudPainter._split_info_overlay_sections(stats):
            gap = section_gap if shown else 0
            rows_top = content_height + gap + tab_height + row_gap
            room = (max_height - rows_top) // line_height
            if room < 1:
                cut = True
                break
            rows = [
                metrics.elidedText(HudPainter._format_row(key, value), Qt.TextElideMode.ElideRight, max_width)
                for key, value in section.rows
            ]
            if len(rows) > room:
                rows = rows[:room]
                cut = True
            title = metrics.elidedText(section.title, Qt.TextElideMode.ElideRight, max_width - 2 * tab_padding_x)
            shown.append((title, rows))
            content_height = rows_top + line_height * len(rows)
            if cut:
                break
        if cut and shown:
            shown[-1][1][-1] = "…"

        content_width = max(
            (
                max(metrics.horizontalAdvance(title) + tab_padding_x * 2, *(metrics.horizontalAdvance(r) for r in rows))
                for title, rows in shown
            ),
            default=0,
        )
        rect_width = content_width + padding * 2
        rect_height = max(tab_height, content_height) + padding * 2
        painter.fillRect(Space.M, Space.M, rect_width, rect_height, bg_color)

        x_offset = Space.M + padding
        y_offset = Space.M + padding
        painter.setPen(text_color)
        for title, rows in shown:
            tab_width = metrics.horizontalAdvance(title) + tab_padding_x * 2
            painter.fillRect(x_offset, y_offset, tab_width, tab_height, tab_color)
            painter.setPen(border_color)
            painter.drawRect(x_offset, y_offset, tab_width, tab_height)
            painter.setPen(text_color)
            painter.drawText(x_offset + tab_padding_x, y_offset + 3 + metrics.ascent(), title)
            y_offset += tab_height + row_gap

            for row in rows:
                painter.drawText(x_offset, y_offset + metrics.ascent(), row)
                y_offset += line_height

            y_offset += section_gap

    @staticmethod
    def _split_info_overlay_sections(stats: dict[str, Any]) -> tuple[_InfoOverlaySection, ...]:
        sections: list[_InfoOverlaySection] = []
        current_title = "Info"
        current_rows: list[tuple[str, Any]] = []

        for key, value in stats.items():
            if value is None:
                if current_rows:
                    sections.append(_InfoOverlaySection(title=current_title, rows=tuple(current_rows)))
                    current_rows = []
                current_title = key
                continue
            current_rows.append((key, value))

        if current_rows:
            sections.append(_InfoOverlaySection(title=current_title, rows=tuple(current_rows)))
        return tuple(sections)

    @staticmethod
    def _format_row(key: str, value: Any) -> str:
        return f"{key}: {value}"
