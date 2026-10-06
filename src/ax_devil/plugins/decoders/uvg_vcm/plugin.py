"""Built-in registration for native UVG-VCM file annotations."""

from ax_devil.modules.plugin_system import DecoderPlugin, FileToSceneDecoderDefinition
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .provider import UVGVCMSceneDataProvider

UVG_VCM = "UVG_VCM"


class UVGVCMPlugin(DecoderPlugin):
    """Expose UVG-VCM annotations to local videos and existing playlist resolvers."""

    @classmethod
    def plugin_id(cls) -> str:
        """Return the built-in plugin's identity."""
        return "uvg-vcm"

    @classmethod
    def display_name(cls) -> str:
        """Return the name used in plugin listings."""
        return "UVG-VCM"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        """Declare the Scene model emitted by this decoder."""
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        """Offer the native JSON provider wherever file overlays can be selected."""
        return (
            FileToSceneDecoderDefinition(
                handler_type=UVG_VCM,
                factory=UVGVCMSceneDataProvider,
                display_name="UVG-VCM",
                description="UVG-VCM v1.0 detection/tracking JSON with optional segmentation polygons.",
                file_extensions=(".json",),
            ),
        )


PLUGIN_CLASS = UVGVCMPlugin
