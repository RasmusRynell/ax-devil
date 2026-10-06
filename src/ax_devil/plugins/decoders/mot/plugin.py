from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DecoderPlugin,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .decoder import build_mot_filter_config
from .provider import MOTChallengeSceneDataProvider

MOT_FILE = "MOT_FILE"


class MOTPlugin(DecoderPlugin):
    @classmethod
    def plugin_id(cls) -> str:
        return "mot"

    @classmethod
    def display_name(cls) -> str:
        return "MOT"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        return (
            FileToSceneDecoderDefinition(
                handler_type=MOT_FILE,
                factory=MOTChallengeSceneDataProvider,
                display_name="MOT Annotations",
                filter_factory=build_mot_filter_config,
                file_extensions=(".txt",),
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        return ()


PLUGIN_CLASS = MOTPlugin
