"""Plugin-host runtime contracts and discovery machinery."""

from .base import PLUGIN_API_VERSION, PluginBase, PluginDefinitionBase
from .decoder_plugins import (
    DECODER_PLUGIN_TYPE,
    DecoderPlugin,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    HandlerTypeName,
    PayloadToSceneDecoderDefinition,
    get_file_decoder_definitions,
    get_file_decoder_factory,
    get_payload_decoder,
    get_payload_filter_factory,
)
from .loader import ApplicationPluginLoader, PluginFamily
from .playlist_resolvers import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PlaylistResolverPlugin,
    PlaylistResolverWidget,
)
from .registry import PluginRecord, PluginStatus, RuntimePluginRegistry

__all__ = [
    "ApplicationPluginLoader",
    "DECODER_PLUGIN_TYPE",
    "DecoderPlugin",
    "DecoderPluginDefinition",
    "FileToSceneDecoderDefinition",
    "HandlerTypeName",
    "PLUGIN_API_VERSION",
    "PLAYLIST_RESOLVER_PLUGIN_TYPE",
    "PayloadToSceneDecoderDefinition",
    "PlaylistResolverPlugin",
    "PlaylistResolverWidget",
    "PluginBase",
    "PluginDefinitionBase",
    "PluginFamily",
    "PluginRecord",
    "PluginStatus",
    "RuntimePluginRegistry",
    "get_file_decoder_definitions",
    "get_file_decoder_factory",
    "get_payload_decoder",
    "get_payload_filter_factory",
]
