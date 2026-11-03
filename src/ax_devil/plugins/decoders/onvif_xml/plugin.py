"""ONVIF XML decoder."""

from ax_devil.modules.plugin_system import PLUGIN_API_VERSION, DecoderSpec, LineDecoderPlugin
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION, Scene

from .decoder import build_onvif_filter_config, decode_onvif_xml

ONVIF_XML = "ONVIF_XML"


class ONVIFXMLPlugin(LineDecoderPlugin):
    """Expose ONVIF XML through the shared line decoder runtime."""

    spec = DecoderSpec(
        id="axis-onvif-xml",
        name="Axis ONVIF XML",
        handler=ONVIF_XML,
        api=PLUGIN_API_VERSION,
        scene=SCENE_MODEL_VERSION,
        extensions=(".xml",),
        artifact_version=1,
        decoder_name="onvif_xml",
        supports_sequence_lookup=True,
        independent_records=True,
        filter_factory=build_onvif_filter_config,
    )

    def decode(self, payload: str) -> Scene | None:
        """Decode one ONVIF document, or no Scene for frameless metadata."""
        return decode_onvif_xml(payload)


PLUGIN_CLASS = ONVIFXMLPlugin
