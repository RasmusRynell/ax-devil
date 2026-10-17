"""Plugin-host runtime contracts and discovery machinery."""

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.modules.background_jobs import JobContext, JobScope, LatestRequest

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
from .loader import ApplicationPluginLoader
from .playlist_resolvers import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PlaylistResolverPlugin,
    PlaylistResolverWidget,
)
from .registry import PluginRecord, PluginStatus, RuntimePluginRegistry

__all__ = [
    "ApplicationPluginLoader",
    "CancelToken",
    "Cancelled",
    "DECODER_PLUGIN_TYPE",
    "DecoderPlugin",
    "DecoderPluginDefinition",
    "FileToSceneDecoderDefinition",
    "HandlerTypeName",
    "JobContext",
    "JobScope",
    "LatestRequest",
    "PLUGIN_API_VERSION",
    "PLAYLIST_RESOLVER_PLUGIN_TYPE",
    "PayloadToSceneDecoderDefinition",
    "PlaylistResolverPlugin",
    "PlaylistResolverWidget",
    "PluginBase",
    "PluginDefinitionBase",
    "PluginRecord",
    "PluginStatus",
    "RuntimePluginRegistry",
    "get_file_decoder_definitions",
    "get_file_decoder_factory",
    "get_payload_decoder",
    "get_payload_filter_factory",
]
