"""Whole-document UVG-VCM provider using the existing frame-indexed Scene store."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ax_devil.modules.data_sources.file_data_provider.scene_decoder_file_provider import (
    SceneDecoderFileProvider,
    StorageMode,
)
from ax_devil.modules.data_sources.file_data_provider.stores import SceneBuildResult
from ax_devil.modules.filtering import FilterConfig
from ax_devil.modules.scene.decoding import PayloadToSceneDecoder
from ax_devil.modules.scene.model import Scene
from ax_devil.modules.settings.logging_config import get_logger

from .decoder import CLASS_NAMES, UVGVCMFrameDecoder, build_uvg_vcm_filter_config, iter_uvg_vcm_frames

logger = get_logger(__name__)


class UVGVCMSceneDataProvider(SceneDecoderFileProvider):
    """Load v1.0 detections and optional polygons by zero-based video frame index."""

    def __init__(self, file_path: str | Path) -> None:
        self._labels: tuple[str, ...] = ()
        self._duplicate_frames: list[int] = []
        super().__init__(
            file_path=file_path,
            decoder_factory=UVGVCMFrameDecoder,
            decoder_name="uvg_vcm",
            artifact_version=1,
            supports_sequence_lookup=True,
            filter_config_factory=self._build_filter_config,
            storage_mode=StorageMode.DERIVED_CACHE,
        )
        if self._duplicate_frames:
            logger.warning(
                f"{self.file_path.name}: duplicate UVG-VCM tracking IDs in source frames {self._duplicate_frames}; "
                "all affected detections are shown with frame-local ambiguous IDs; original IDs remain in attributes"
            )

    def _iter_payloads(self) -> Iterable[str]:
        return iter_uvg_vcm_frames(self.file_path)

    def _build_scene_maps(self, payloads: Iterable[str], decoder: PayloadToSceneDecoder) -> SceneBuildResult:
        scenes: list[tuple[int, Scene]] = []
        labels: set[str] = set()
        for frame_index, payload in enumerate(payloads):
            # A malformed record fails the load, rather than silently dropping an entire frame's overlays.
            scene = decoder.decode(payload)
            assert scene is not None
            scenes.append((frame_index, scene))
            labels.update(
                c.type for entity in scene.entities.values() for obs in entity.observations for c in obs.classification
            )
            if scene.debug["duplicate_track_ids"]:
                self._duplicate_frames.append(frame_index + 1)
        self._labels = tuple(sorted(labels))
        return self._build_scene_result_from_scenes(scenes, total_lines=len(scenes))

    def _collect_additional_cache_metadata(self) -> dict[str, Any]:
        return {"uvg_vcm": {"version": "1.0", "labels": list(self._labels), "duplicate_frames": self._duplicate_frames}}

    def _restore_additional_state_from_cache(self, raw_metadata: dict[str, Any]) -> None:
        metadata = raw_metadata["uvg_vcm"]
        labels = metadata["labels"]
        duplicates = metadata["duplicate_frames"]
        if (
            metadata["version"] != "1.0"
            or not isinstance(labels, list)
            or any(label not in CLASS_NAMES for label in labels)
            or not isinstance(duplicates, list)
            or any(type(frame) is not int or frame < 1 for frame in duplicates)
        ):
            raise ValueError("Invalid UVG-VCM cache metadata")
        self._labels = tuple(labels)
        self._duplicate_frames = list(duplicates)

    def _build_filter_config(self) -> FilterConfig:
        return build_uvg_vcm_filter_config(self._labels)
