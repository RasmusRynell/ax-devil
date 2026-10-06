from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DecoderPlugin,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .common import build_adf_beta_filter_config
from .consolidated import ADFBetaConsolidatedJSONLDataProvider
from .frame import ADFBetaFrameDecoder, ADFBetaFrameJSONLDataProvider

ADF_BETA_FRAME = "ADF_BETA_FRAME"
ADF_BETA_CONSOLIDATED = "ADF_BETA_CONSOLIDATED"


class ADFBetaPlugin(DecoderPlugin):
    """Axis Analytics Data Format (ADF) beta decoder bundle."""

    @classmethod
    def plugin_id(cls) -> str:
        return "adf-v1-beta"

    @classmethod
    def display_name(cls) -> str:
        return "ADF v1 Beta"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        return (
            FileToSceneDecoderDefinition(
                handler_type=ADF_BETA_FRAME,
                factory=ADFBetaFrameJSONLDataProvider,
                display_name="ADF v1 Beta Frame",
                filter_factory=build_adf_beta_filter_config,
                file_extensions=(".jsonl", ".json"),
            ),
            FileToSceneDecoderDefinition(
                handler_type=ADF_BETA_CONSOLIDATED,
                factory=ADFBetaConsolidatedJSONLDataProvider,
                display_name="ADF v1 Beta Consolidated",
                filter_factory=build_adf_beta_filter_config,
                file_extensions=(".jsonl", ".json"),
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        return (
            PayloadToSceneDecoderDefinition(
                handler_type=ADF_BETA_FRAME,
                decoder_factory=ADFBetaFrameDecoder,
                display_name="ADF v1 Beta Frame",
                filter_factory=build_adf_beta_filter_config,
            ),
        )


PLUGIN_CLASS = ADFBetaPlugin
