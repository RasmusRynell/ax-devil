"""Scene decoder adapter for MOT Challenge CSV annotations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.file_data_provider.stores import SceneBuildResult
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene

from .decoder import (
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    MOTFileStats,
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
        self._file_stats: MOTFileStats | None = None

        super().__init__(
            file_path=file_path,
            decoder_factory=MOTChallengeDecoder,
            decoder_name="mot_csv",
            artifact_version=2,
            decode_options={"width": self._width, "height": self._height},
            supports_sequence_lookup=True,
            filter_config_factory=build_mot_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )

    def _build_scene_maps(
        self,
        payloads: Iterable[str],
        decoder: PayloadToSceneDecoder,
    ) -> SceneBuildResult:
        frame_payloads, stats = prepare_mot_frame_payloads(payloads, width=self._width, height=self._height)
        self._file_stats = stats

        return super()._build_scene_maps(frame_payloads, decoder)

    def _collect_additional_cache_metadata(self) -> dict[str, Any]:
        if self._file_stats is None:
            return {}
        return {"mot_file_stats": asdict(self._file_stats)}

    def _restore_additional_state_from_cache(self, raw_metadata: dict[str, Any]) -> None:
        stats_payload = raw_metadata.get("mot_file_stats")
        if not isinstance(stats_payload, dict):
            self._file_stats = None
            return
        try:
            self._file_stats = MOTFileStats(
                total_frames=int(stats_payload["total_frames"]),
                total_detections=int(stats_payload["total_detections"]),
                max_frame_index=int(stats_payload["max_frame_index"]),
            )
        except (KeyError, TypeError, ValueError):
            self._file_stats = None

    @property
    def file_stats(self) -> MOTFileStats | None:
        """Return summary statistics collected during the last parse, if available."""
        return self._file_stats
