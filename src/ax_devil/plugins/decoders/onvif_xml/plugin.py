from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DecoderPlugin,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .decoder import build_onvif_filter_config
from .provider import ONVIFDecoder, ONVIFXMLDataProvider

ONVIF_XML = "ONVIF_XML"


class ONVIFXMLPlugin(DecoderPlugin):
    @classmethod
    def plugin_id(cls) -> str:
        return "axis-onvif-xml"

    @classmethod
    def display_name(cls) -> str:
        return "Axis ONVIF XML"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        return (
            FileToSceneDecoderDefinition(
                handler_type=ONVIF_XML,
                factory=ONVIFXMLDataProvider,
                display_name="Axis ONVIF XML",
                filter_factory=build_onvif_filter_config,
                file_extensions=(".xml",),
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        return (
            PayloadToSceneDecoderDefinition(
                handler_type=ONVIF_XML,
                decoder_factory=ONVIFDecoder,
                display_name="Axis ONVIF XML",
                filter_factory=build_onvif_filter_config,
            ),
        )


PLUGIN_CLASS = ONVIFXMLPlugin
