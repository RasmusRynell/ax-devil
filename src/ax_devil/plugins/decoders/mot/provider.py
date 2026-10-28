"""Scene decoder adapter for MOT Challenge CSV annotations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ax_devil.core.atomic_file import open_replaceable_text
from ax_devil.modules.data_sources.file_data_provider.scene_catalog import SceneBuildResult
from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene

from .decoder import (
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    build_mot_filter_config,
    decode_mot_frame,
    prepare_mot_frame_payloads,
)

__all__ = ["MOTChallengeSceneDataProvider", "MOTChallengeDecoder"]


class MOTChallengeDecoder(PayloadToSceneDecoder):
    """Decoder for MOT Challenge frame payloads."""

    def decode(self, payload: Any) -> Scene | None:
        if payload is None:
            return None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            stripped = payload.strip()
            if not stripped:
                return None
            return decode_mot_frame(stripped)
        return decode_mot_frame(payload)


class MOTChallengeSceneDataProvider(SceneDecoderFileProvider):
    """Scene decoder file provider for MOT Challenge CSV annotations."""

    def __init__(
        self,
        file_path: str | Path,
        *,
        width: int = DEFAULT_FRAME_WIDTH,
        height: int = DEFAULT_FRAME_HEIGHT,
    ) -> None:
        self._width = int(width)
        self._height = int(height)

        super().__init__(
            file_path=file_path,
            decoder_factory=MOTChallengeDecoder,
            decoder_name="mot_csv",
            artifact_version=4,
            decode_options={"width": self._width, "height": self._height},
            supports_sequence_lookup=True,
            filter_config_factory=build_mot_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )

    def _iter_payloads(self) -> Iterable[str]:
        """Yield every row, blank ones included, so that row numbers are the file's line numbers."""
        with open_replaceable_text(self.file_path) as file_obj:
            yield from file_obj

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        frame_payloads, warning = prepare_mot_frame_payloads(payloads, width=self._width, height=self._height)
        result = super()._build_scene_maps(frame_payloads, decoder)
        return replace(result, warnings=(warning,) if warning is not None else ())
