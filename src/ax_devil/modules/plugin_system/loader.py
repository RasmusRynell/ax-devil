"""Application-specific plugin discovery and loading."""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from packaging.utils import canonicalize_name

from ax_devil.modules.plugin_installation.entry_points import (
    DECODER_ENTRY_POINT_GROUP,
    PLAYLIST_RESOLVER_ENTRY_POINT_GROUP,
)
from ax_devil.modules.settings.logging_config import get_logger

from .base import PLUGIN_API_VERSION, PluginBase
from .decoder_plugins import DECODER_PLUGIN_TYPE, DecoderPlugin
from .playlist_resolvers import PLAYLIST_RESOLVER_PLUGIN_TYPE, PlaylistResolverPlugin
from .registry import PluginRecord, PluginStatus, RuntimePluginRegistry

logger = get_logger(__name__)


@dataclass(frozen=True)
class PluginFamily:
    """Application-supported plugin family."""

    plugin_type: str
    plugin_base_class: type[Any]
    builtin_package: str
    entrypoint_group: str


class ApplicationPluginLoader:
    """Application bootstrap loader for all supported plugin families."""

    PLUGIN_FAMILIES: tuple[PluginFamily, ...] = (
        PluginFamily(
            plugin_type=DECODER_PLUGIN_TYPE,
            plugin_base_class=DecoderPlugin,
            builtin_package="ax_devil.plugins.decoders",
            entrypoint_group=DECODER_ENTRY_POINT_GROUP,
        ),
        PluginFamily(
            plugin_type=PLAYLIST_RESOLVER_PLUGIN_TYPE,
            plugin_base_class=PlaylistResolverPlugin,
            builtin_package="ax_devil.plugins.playlist_resolvers",
            entrypoint_group=PLAYLIST_RESOLVER_ENTRY_POINT_GROUP,
        ),
    )
    _loaded: bool = False

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
        cls._warn_about_failures()

    @classmethod
    def _load_builtins(cls, family: PluginFamily) -> None:
        """Import every ``<family package>.<name>.plugin`` module, in name order, and register its plugin."""
        package = importlib.import_module(family.builtin_package)
        package_dir = Path(package.__path__[0])
        for module_info in sorted(pkgutil.iter_modules(package.__path__), key=lambda info: info.name):
            if module_info.ispkg:
                cls._import_builtin_and_register(family, module_info.name, package_dir / module_info.name / "plugin.py")

    @classmethod
    def _load_family_from_entrypoints(cls, family: PluginFamily) -> None:
        try:
            entrypoints = importlib.metadata.entry_points(group=family.entrypoint_group)
        except Exception as exc:
            logger.exception(f"Failed to discover plugin entry points for {family.entrypoint_group}: {exc}")
            return

        for index, entrypoint in enumerate(entrypoints):
            entrypoint_path = Path("<unknown>")
            plugin_id_hint = entrypoint.name or f"<entrypoint-{index}>"
            package: str = canonicalize_name(plugin_id_hint)  # fallback when the distribution metadata is unreadable
            try:
                package = cls._entrypoint_package(entrypoint)
                entrypoint_path = cls._entrypoint_path(entrypoint)
                plugin_cls = entrypoint.load()
            except Exception as exc:
                reason = f"entry-point discovery failed: {exc}"
                if isinstance(exc, ImportError) and (exc.name or "").startswith("ax_devil."):
                    reason = f"incompatible with this ax-devil version: {exc}"
                cls._fail(family, plugin_id_hint, package, reason, exc)
                continue

            cls._register_plugin_class(
                family,
                plugin_cls,
                entrypoint_path,
                package,
                plugin_id_hint=plugin_id_hint,
            )

    @classmethod
    def _import_builtin_and_register(cls, family: PluginFamily, name: str, entrypoint: Path) -> None:
        logger.debug(f"Loading {family.plugin_type} plugin from {entrypoint}")
        try:
            module = importlib.import_module(f"{family.builtin_package}.{name}.plugin")
        except Exception as exc:
            cls._fail(family, name, None, f"import failed: {exc}", exc)
            return

        cls._register_plugin_class(
            family,
            getattr(module, "PLUGIN_CLASS", None),
            entrypoint,
            None,
            plugin_id_hint=name,
        )

    @classmethod
    def _register_plugin_class(
        cls,
        family: PluginFamily,
        plugin_cls: object,
        entrypoint: Path,
        package: str | None,
        *,
        plugin_id_hint: str,
    ) -> None:
        failure_id = plugin_id_hint
        failure_prefix = "plugin validation failed: "
        try:
            if not isinstance(plugin_cls, type) or not issubclass(plugin_cls, family.plugin_base_class):
                cls._fail(family, plugin_id_hint, package, "PLUGIN_CLASS missing or invalid")
                return

            plugin_cls = cast(type[PluginBase], plugin_cls)

            unimplemented = sorted(getattr(plugin_cls, "__abstractmethods__", ()))
            if unimplemented:
                reason = f"does not implement {', '.join(unimplemented)}"
                cls._fail(family, plugin_cls.plugin_id(), package, reason)
                return

            required_api_version = plugin_cls.required_api_version()
            if required_api_version != PLUGIN_API_VERSION:
                reason = f"requires plugin API {required_api_version}, host provides {PLUGIN_API_VERSION}"
                cls._fail(family, plugin_cls.plugin_id(), package, reason)
                return

            if plugin_cls.plugin_type() != family.plugin_type:
                declared = plugin_cls.plugin_type()
                reason = f"PLUGIN_CLASS declared plugin_type={declared!r}, expected {family.plugin_type!r}"
                cls._fail(family, plugin_cls.plugin_id(), package, reason)
                return

            try:
                existing = RuntimePluginRegistry.get_plugin(family.plugin_type, plugin_cls.plugin_id())
            except ValueError:
                existing = None
            if existing is not None and existing.status == PluginStatus.LOADED:
                logger.warning(
                    f"Duplicate plugin id {family.plugin_type}:{plugin_cls.plugin_id()} from {entrypoint}; "
                    f"keeping the plugin already loaded from {existing.source}"
                )
                return

            validation_error = plugin_cls.validate_registration(RuntimePluginRegistry)
            if validation_error is not None:
                cls._fail(family, plugin_cls.plugin_id(), package, validation_error)
                return

            failure_id = plugin_cls.plugin_id()
            failure_prefix = ""
            RuntimePluginRegistry.register_plugin(plugin_cls, package)
            logger.debug(f"Registered plugin {plugin_cls.plugin_type()}:{plugin_cls.plugin_id()} from {entrypoint}")
        except Exception as exc:
            cls._fail(family, failure_id, package, f"{failure_prefix}{exc}", exc)

    @staticmethod
    def _fail(
        family: PluginFamily,
        plugin_id: str,
        package: str | None,
        reason: str,
        exc: Exception | None = None,
    ) -> None:
        """Record a plugin that cannot load; ``_warn_about_failures`` reports it once per source."""
        logger.debug(f"Plugin {family.plugin_type}:{plugin_id} failed to load: {reason}", exc_info=exc)
        RuntimePluginRegistry.mark_failed(family.plugin_type, plugin_id, package, reason)

    @staticmethod
    def _warn_about_failures() -> None:
        """Log one warning per source (a package, or the built-in plugins) whose plugins failed to load."""
        failed_by_source: dict[str, list[PluginRecord]] = {}
        for record in RuntimePluginRegistry.list_plugins():
            if record.status is PluginStatus.FAILED:
                failed_by_source.setdefault(record.source, []).append(record)
        for source, records in failed_by_source.items():
            first = records[0].definition
            logger.warning(
                f"{len(records)} plugin(s) from {source} failed to load, e.g. "
                f"{first.plugin_type}:{first.plugin_id}: {records[0].error} (see the Plugins view)"
            )

    @staticmethod
    def _entrypoint_path(entrypoint: importlib.metadata.EntryPoint) -> Path:
        distribution = entrypoint.dist
        if distribution is not None:
            return Path(str(distribution.locate_file("")))
        return Path(entrypoint.value.split(":", maxsplit=1)[0])

    @staticmethod
    def _entrypoint_package(entrypoint: importlib.metadata.EntryPoint) -> str:
        """Return the normalized name of the distribution that provides *entrypoint*."""
        distribution = entrypoint.dist
        return canonicalize_name(entrypoint.name if distribution is None else distribution.name)


__all__ = ["ApplicationPluginLoader", "PluginFamily"]
