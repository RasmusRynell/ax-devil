from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DecoderPlugin,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .common import build_adf_frame_v1_filter_config
from .consolidated import ADFFrameV1ConsolidatedDataProvider
from .frame import ADFFrameV1DataProvider, ADFFrameV1Decoder

ADF_V1_FRAME = "ADF_V1_FRAME"
ADF_V1_CONSOLIDATED = "ADF_V1_CONSOLIDATED"


class ADFV1Plugin(DecoderPlugin):
    """Axis ADF Frame v1 decoder bundle."""

    @classmethod
    def plugin_id(cls) -> str:
        return "adf-v1"

    @classmethod
    def display_name(cls) -> str:
        return "Axis ADF v1"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        return (
            FileToSceneDecoderDefinition(
                handler_type=ADF_V1_FRAME,
                factory=ADFFrameV1DataProvider,
                display_name="ADF v1 Frame",
                filter_factory=build_adf_frame_v1_filter_config,
                file_extensions=(".jsonl", ".json"),
            ),
            FileToSceneDecoderDefinition(
                handler_type=ADF_V1_CONSOLIDATED,
                factory=ADFFrameV1ConsolidatedDataProvider,
                display_name="ADF v1 Consolidated Tracks",
                filter_factory=build_adf_frame_v1_filter_config,
                file_extensions=(".jsonl", ".json"),
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        return (
            PayloadToSceneDecoderDefinition(
                handler_type=ADF_V1_FRAME,
                decoder_factory=ADFFrameV1Decoder,
                display_name="ADF v1 Frame",
                filter_factory=build_adf_frame_v1_filter_config,
            ),
        )


PLUGIN_CLASS = ADFV1Plugin
