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
from .registry import PluginRecord, PluginStatus, RuntimePluginRegistry

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
        cls._warn_about_failures()

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
                reason = f"entry-point discovery failed: {exc}"
                if isinstance(exc, ImportError) and (exc.name or "").startswith("ax_devil."):
                    reason = f"incompatible with this ax-devil version: {exc}"
                cls._fail(family, plugin_id_hint, entrypoint_path, origin, reason, exc)
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
            cls._fail(family, entrypoint.parent.name, entrypoint, "builtin", f"import failed: {exc}", exc)
            return

        cls._register_plugin_class(
            family,
            getattr(module, "PLUGIN_CLASS", None),
            entrypoint,
            "builtin",
            plugin_id_hint=entrypoint.parent.name,
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
                cls._fail(family, plugin_id_hint, entrypoint, origin, "PLUGIN_CLASS missing or invalid")
                return

            plugin_cls = cast(type[PluginBase], plugin_cls)

            api_declaring_class = next(base for base in plugin_cls.__mro__ if "required_api_version" in base.__dict__)
            if origin != "builtin" and api_declaring_class is PluginBase:
                cls._fail(family, plugin_id_hint, entrypoint, origin, "must explicitly declare required_api_version")
                return

            required_api_version = plugin_cls.required_api_version()
            if required_api_version != PLUGIN_API_VERSION:
                reason = f"requires plugin API {required_api_version}, host provides {PLUGIN_API_VERSION}"
                cls._fail(family, plugin_cls.plugin_id(), entrypoint, origin, reason)
                return

            unimplemented = sorted(getattr(plugin_cls, "__abstractmethods__", ()))
            if unimplemented:
                reason = f"does not implement {', '.join(unimplemented)}"
                cls._fail(family, plugin_cls.plugin_id(), entrypoint, origin, reason)
                return

            if plugin_cls.plugin_type() != family.plugin_type:
                declared = plugin_cls.plugin_type()
                reason = f"PLUGIN_CLASS declared plugin_type={declared!r}, expected {family.plugin_type!r}"
                cls._fail(family, plugin_cls.plugin_id(), entrypoint, origin, reason)
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
                cls._fail(family, plugin_cls.plugin_id(), entrypoint, origin, validation_error)
                return

            failure_id = plugin_cls.plugin_id()
            failure_prefix = ""
            RuntimePluginRegistry.register_plugin(plugin_cls, entrypoint, origin)
            logger.debug(f"Registered plugin {plugin_cls.plugin_type()}:{plugin_cls.plugin_id()} from {entrypoint}")
        except Exception as exc:
            cls._fail(family, failure_id, entrypoint, origin, f"{failure_prefix}{exc}", exc)

    @staticmethod
    def _fail(
        family: PluginFamily,
        plugin_id: str,
        entrypoint: Path,
        origin: str,
        reason: str,
        exc: Exception | None = None,
    ) -> None:
        """Record a plugin that cannot load; ``_warn_about_failures`` reports it once per origin."""
        logger.debug(f"Plugin {family.plugin_type}:{plugin_id} from {origin} failed to load: {reason}", exc_info=exc)
        RuntimePluginRegistry.mark_failed(family.plugin_type, plugin_id, entrypoint, origin, reason)

    @staticmethod
    def _warn_about_failures() -> None:
        """Log one warning per origin whose plugins failed to load, naming the first failure."""
        failed_by_origin: dict[str, list[PluginRecord]] = {}
        for record in RuntimePluginRegistry.list_plugins():
            if record.status is PluginStatus.FAILED:
                failed_by_origin.setdefault(record.origin, []).append(record)
        for origin, records in failed_by_origin.items():
            first = records[0].definition
            logger.warning(
                f"{len(records)} plugin(s) from {origin} failed to load, e.g. "
                f"{first.plugin_type}:{first.plugin_id}: {records[0].error} (see the Plugins view)"
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
