"""A sheet, the example frame the catalog viewer and ``ax-devil catalog render`` draw, and how it is drawn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF, QImage

from ax_devil.modules.catalog_viewer.street import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
)
from ax_devil.modules.scene.model import (
    Scene,
)
from ax_devil.modules.scene.rendering import CachedSceneOverlay, SceneRenderCatalog
from ax_devil.modules.scene.rendering.template_runtime.values import CatalogDiagnostic
from ax_devil.modules.video_player.engine.data_types import VideoFrame, VideoFrameWithOverlays, VideoOverlayData
from ax_devil.modules.video_player.engine.drawing import Paint
from ax_devil.modules.video_player.engine.quick.preparation import DrawingBuffer
from ax_devil.modules.video_player.engine.quick.prepared import DrawingSettings, PreparedDrawing
from ax_devil.modules.video_player.engine.render_context import RenderContext


@dataclass(frozen=True, slots=True)
class Caption:
    """Text naming the example below it, with an optional smaller second line.

    Position and width are normalized; text longer than the width is shortened with an ellipsis.
    """

    x: float
    y: float
    text: str
    detail: str = ""
    width: float = 0.25


@dataclass(frozen=True, slots=True)
class Sheet:
    """One example frame: its tab title, what it shows, the Scene, the captions and the footage it is drawn over."""

    key: str
    title: str
    description: str
    scene: Scene
    captions: tuple[Caption, ...]
    background: Callable[[], QImage]


def sheet_frame(sheet: Sheet, catalog: SceneRenderCatalog) -> VideoFrameWithOverlays:
    """Return *sheet* drawn with *catalog* as a frame for the video renderers, with hover details on its objects."""
    frame = VideoFrame(image=sheet.background(), timestamp=0.0, frame_id=0)
    overlays = VideoOverlayData(
        drawing_generator=SheetDrawing(sheet, catalog).prepare,
        timestamp=0.0,
        interaction_provider=CachedSceneOverlay(scene=sheet.scene, catalog=catalog),
    )
    return VideoFrameWithOverlays(frame=frame, overlays=overlays)


def drawing_errors(sheet: Sheet, catalog: SceneRenderCatalog) -> tuple[CatalogDiagnostic, ...]:
    """Draw *sheet* with *catalog* off screen and return the errors drawing reported, such as a failed value check."""
    drawing = SheetDrawing(sheet, catalog)
    context = RenderContext.create(FRAME_WIDTH, FRAME_HEIGHT)
    drawing.prepare(context, DrawingBuffer(DrawingSettings.for_context(context)))
    return drawing.diagnostics


class SheetDrawing:
    """Draw a sheet: its captions as fixed-size text on dark plates, then the catalog's drawing of its Scene.

    Captions are drawn like overlay text rather than into the background image, so they stay sharp at every window
    size and zoom. Their plates keep them readable over bright footage.
    """

    def __init__(self, sheet: Sheet, catalog: SceneRenderCatalog) -> None:
        self._sheet = sheet
        self._catalog = catalog
        self.diagnostics: tuple[CatalogDiagnostic, ...] = ()

    def prepare(self, context: RenderContext, buffer: DrawingBuffer) -> PreparedDrawing:
        """Return the finished drawing for *context*; drawing errors are kept in ``diagnostics``."""
        lines = [self._texts(context, line) for line in _CAPTION_LINES]
        self._plates(context, buffer, lines)
        for line, texts in zip(_CAPTION_LINES, lines):
            self._captions(context, buffer, line, texts)
        diagnostics: list[CatalogDiagnostic] = []
        self._catalog.render_scene(self._sheet.scene, context, buffer, diagnostics=diagnostics)
        self.diagnostics = tuple(diagnostics)
        return buffer.finish()

    def _texts(self, context: RenderContext, line: _CaptionLine) -> list[tuple[str, float]]:
        """Return each caption's text on *line*, shortened to fit, and its width in pixels; empty when it has none."""
        metrics = QFontMetricsF(line.font())
        texts = []
        for caption in self._sheet.captions:
            text = metrics.elidedText(
                line.text(caption), Qt.TextElideMode.ElideRight, caption.width * context.width - 20
            )
            texts.append((text, metrics.horizontalAdvance(text) if text else 0.0))
        return texts

    def _plates(self, context: RenderContext, buffer: DrawingBuffer, lines: list[list[tuple[str, float]]]) -> None:
        shown = [index for index in range(len(self._sheet.captions)) if any(line[index][0] for line in lines)]
        if not shown:
            return
        captions = [self._sheet.captions[index] for index in shown]
        widths = np.array([max(line[index][1] for line in lines) + 12 for index in shown])
        heights = np.array(
            [
                max(line.top + line.size + 6 for line, texts in zip(_CAPTION_LINES, lines) if texts[index][0]) - 2
                for index in shown
            ],
            dtype=np.float64,
        )
        buffer.boxes(
            np.array([caption.x + 4 / context.width for caption in captions]),
            np.array([caption.y + 4 / context.height for caption in captions]),
            widths / context.width,
            heights / context.height,
            Paint(*_PLATE, 0.0, False, *_PLATE, 168, radius=4 / context.scale_factor),
        )

    def _captions(
        self, context: RenderContext, buffer: DrawingBuffer, line: _CaptionLine, texts: list[tuple[str, float]]
    ) -> None:
        shown = [(caption, text) for caption, (text, _width) in zip(self._sheet.captions, texts) if text]
        if not shown:
            return
        buffer.texts(
            np.array([caption.x + 10 / context.width for caption, _text in shown]),
            np.array([caption.y + line.top / context.height for caption, _text in shown]),
            [text for _caption, text in shown],
            ["top-left"] * len(shown),
            Paint(*line.color, font_family=_CAPTION_FONT, font_size=line.size / context.scale_factor),
        )


@dataclass(frozen=True, slots=True)
class _CaptionLine:
    """One line of every caption: which text it shows, and its size, color and distance from the caption's top."""

    detail: bool
    size: int
    top: int
    color: tuple[int, int, int]

    def text(self, caption: Caption) -> str:
        return caption.detail if self.detail else caption.text

    def font(self) -> QFont:
        font = QFont(_CAPTION_FONT)
        font.setPixelSize(self.size)
        return font


_CAPTION_FONT = "Arial"
_CAPTION_LINES = (_CaptionLine(False, 14, 8, (226, 230, 236)), _CaptionLine(True, 11, 27, (160, 167, 178)))
_PLATE = (16, 18, 22)
