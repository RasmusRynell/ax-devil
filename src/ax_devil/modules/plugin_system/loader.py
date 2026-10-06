"""Application-specific plugin discovery and loading."""

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ax_devil.modules.settings.logging_config import get_logger

from .base import PLUGIN_API_VERSION, PluginBase
from .decoder_plugins import DECODER_PLUGIN_TYPE, DecoderPlugin
from .playlist_resolvers import PLAYLIST_RESOLVER_PLUGIN_TYPE, PlaylistResolverPlugin
from .registry import PluginStatus, RuntimePluginRegistry

logger = get_logger(__name__)
AX_DEVIL_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PluginFamily:
    """Application-supported plugin family."""

    plugin_type: str
    plugin_base_class: type[Any]
    builtin_root: Path
    entrypoint_group: str


class ApplicationPluginLoader:
    """Application bootstrap loader for all supported plugin families."""

    PLUGIN_FAMILIES: tuple[PluginFamily, ...] = (
        PluginFamily(
            plugin_type=DECODER_PLUGIN_TYPE,
            plugin_base_class=DecoderPlugin,
            builtin_root=AX_DEVIL_ROOT / "plugins" / "decoders",
            entrypoint_group="ax_devil.decoder_plugins",
        ),
        PluginFamily(
            plugin_type=PLAYLIST_RESOLVER_PLUGIN_TYPE,
            plugin_base_class=PlaylistResolverPlugin,
            builtin_root=AX_DEVIL_ROOT / "plugins" / "playlist_resolvers",
            entrypoint_group="ax_devil.playlist_resolver_plugins",
        ),
    )
    _loaded: bool = False
    _loaded_entrypoints: set[Path] = set()

    @classmethod
    def load_all(cls) -> None:
        """Discover and register all configured plugin families once per process."""
        if cls._loaded:
            logger.debug("ApplicationPluginLoader.load_all has already run; skipping discovery.")
            return

        cls._loaded = True
        for family in cls.PLUGIN_FAMILIES:
            cls._load_builtins(family)
            cls._load_family_from_entrypoints(family)

    @classmethod
    def reload_plugins(cls) -> None:
        """Clear existing registrations and reload all plugin families."""
        logger.info("Reloading application plugins...")
        RuntimePluginRegistry.reset()
        cls._loaded = False
        cls._loaded_entrypoints.clear()
        cls.load_all()

    @classmethod
    def _load_builtins(cls, family: PluginFamily) -> None:
        directory = family.builtin_root
        if not directory.exists():
            logger.debug(f"Built-in plugin directory does not exist: {directory}")
            return

        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue

            entrypoint = (child / "plugin.py").resolve()
            if not entrypoint.is_file() or entrypoint in cls._loaded_entrypoints:
                continue

            cls._loaded_entrypoints.add(entrypoint)
            cls._import_builtin_and_register(family, entrypoint)

    @classmethod
    def _load_family_from_entrypoints(cls, family: PluginFamily) -> None:
        try:
            entrypoints = importlib.metadata.entry_points(group=family.entrypoint_group)
        except Exception as exc:
            logger.exception(f"Failed to discover plugin entry points for {family.entrypoint_group}: {exc}")
            return

        for index, entrypoint in enumerate(entrypoints):
            entrypoint_path = Path("<unknown>")
            origin = "unknown"
            plugin_id_hint = f"<entrypoint-{index}>"
            try:
                plugin_id_hint = entrypoint.name
                entrypoint_path = cls._entrypoint_path(entrypoint)
                origin = cls._entrypoint_origin(entrypoint)
                plugin_cls = entrypoint.load()
            except Exception as exc:
                logger.exception(f"Failed to load plugin entry point {plugin_id_hint!r}: {exc}")
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_id_hint,
                    entrypoint_path,
                    origin,
                    f"entry-point discovery failed: {exc}",
                )
                continue

            cls._register_plugin_class(
                family,
                plugin_cls,
                entrypoint_path,
                origin,
                plugin_id_hint=plugin_id_hint,
            )

    @classmethod
    def _import_builtin_and_register(cls, family: PluginFamily, entrypoint: Path) -> None:
        logger.debug(f"Loading {family.plugin_type} plugin from {entrypoint}")
        relative = entrypoint.parent.relative_to(family.builtin_root)
        module_path = ".".join(("ax_devil.plugins", f"{family.plugin_type}s", *relative.parts, "plugin"))
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            logger.exception(f"Failed to import built-in plugin {entrypoint}: {exc}")
            return

        cls._register_plugin_class(
            family,
            getattr(module, "PLUGIN_CLASS", None),
            entrypoint,
            "builtin",
            plugin_id_hint=entrypoint.stem,
        )

    @classmethod
    def _register_plugin_class(
        cls,
        family: PluginFamily,
        plugin_cls: object,
        entrypoint: Path,
        origin: str,
        *,
        plugin_id_hint: str,
    ) -> None:
        failure_id = plugin_id_hint
        failure_prefix = "plugin validation failed: "
        try:
            if not isinstance(plugin_cls, type) or not issubclass(plugin_cls, family.plugin_base_class):
                logger.warning(
                    f"Plugin {entrypoint} must expose PLUGIN_CLASS subclassing {family.plugin_base_class.__name__}"
                )
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_id_hint,
                    entrypoint,
                    origin,
                    "PLUGIN_CLASS missing or invalid",
                )
                return

            plugin_cls = cast(type[PluginBase], plugin_cls)

            api_declaring_class = next(base for base in plugin_cls.__mro__ if "required_api_version" in base.__dict__)
            if origin != "builtin" and api_declaring_class is PluginBase:
                error_message = "must explicitly declare required_api_version"
                logger.warning(f"Plugin {entrypoint} {error_message}")
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_id_hint,
                    entrypoint,
                    origin,
                    error_message,
                )
                return

            required_api_version = plugin_cls.required_api_version()
            if required_api_version != PLUGIN_API_VERSION:
                error_message = f"requires plugin API {required_api_version}, host provides {PLUGIN_API_VERSION}"
                logger.warning(f"Plugin {entrypoint} {error_message}")
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_cls.plugin_id(),
                    entrypoint,
                    origin,
                    error_message,
                )
                return

            if plugin_cls.plugin_type() != family.plugin_type:
                error_message = (
                    f"PLUGIN_CLASS declared plugin_type={plugin_cls.plugin_type()!r}, expected {family.plugin_type!r}"
                )
                logger.warning(f"Plugin {entrypoint} {error_message}")
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_cls.plugin_id(),
                    entrypoint,
                    origin,
                    error_message,
                )
                return

            try:
                existing = RuntimePluginRegistry.get_plugin(family.plugin_type, plugin_cls.plugin_id())
            except ValueError:
                existing = None
            if existing is not None and existing.status == PluginStatus.LOADED:
                logger.warning(
                    f"Duplicate plugin id {family.plugin_type}:{plugin_cls.plugin_id()} from {entrypoint}; "
                    f"keeping the plugin already loaded from {existing.origin}"
                )
                return

            validation_error = plugin_cls.validate_registration(RuntimePluginRegistry)
            if validation_error is not None:
                logger.warning(f"Plugin {entrypoint} {validation_error}")
                RuntimePluginRegistry.mark_failed(
                    family.plugin_type,
                    plugin_cls.plugin_id(),
                    entrypoint,
                    origin,
                    validation_error,
                )
                return

            failure_id = plugin_cls.plugin_id()
            failure_prefix = ""
            RuntimePluginRegistry.register_plugin(plugin_cls, entrypoint, origin)
            logger.debug(f"Registered plugin {plugin_cls.plugin_type()}:{plugin_cls.plugin_id()} from {entrypoint}")
        except Exception as exc:
            logger.exception(f"Failed to load plugin {entrypoint}: {exc}")
            RuntimePluginRegistry.mark_failed(
                family.plugin_type, failure_id, entrypoint, origin, f"{failure_prefix}{exc}"
            )

    @staticmethod
    def _entrypoint_path(entrypoint: importlib.metadata.EntryPoint) -> Path:
        distribution = entrypoint.dist
        if distribution is not None:
            return Path(str(distribution.locate_file("")))
        return Path(entrypoint.value.split(":", maxsplit=1)[0])

    @staticmethod
    def _entrypoint_origin(entrypoint: importlib.metadata.EntryPoint) -> str:
        distribution = entrypoint.dist
        if distribution is None:
            return f"package:{entrypoint.name}"
        return f"package:{distribution.name}"


__all__ = ["ApplicationPluginLoader", "PluginFamily"]
