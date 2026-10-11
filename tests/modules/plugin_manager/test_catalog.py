"""The Plugins view lists failed plugins, installed plugin packages and the built-in plugins from the registry."""

from __future__ import annotations

from pathlib import Path

from ax_devil.modules.plugin_manager.catalog import PluginEntry, PluginGroup, Section, plugin_catalog
from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginDefinitionBase,
    PluginRecord,
    PluginStatus,
)

VERSIONS = {"yolo-detections": "0.2.1", "lab-experiments": "1.0.0"}


def _record(
    plugin_id: str, origin: str, plugin_type: str = DECODER_PLUGIN_TYPE, error: str | None = None
) -> PluginRecord:
    return PluginRecord(
        definition=PluginDefinitionBase(plugin_type=plugin_type, plugin_id=plugin_id, display_name=plugin_id),
        entrypoint=Path(f"/plugins/{plugin_id}"),
        origin=origin,
        status=PluginStatus.FAILED if error else PluginStatus.LOADED,
        error=error,
    )


def _section(groups: tuple[PluginGroup, ...], section: Section) -> tuple[PluginEntry, ...]:
    return next((group.entries for group in groups if group.section is section), ())


def test_records_are_grouped_into_failed_installed_and_built_in() -> None:
    groups = plugin_catalog(
        [
            _record("mot", "builtin"),
            _record("cvat", "builtin"),
            _record("folder_pair", "builtin", PLAYLIST_RESOLVER_PLUGIN_TYPE),
            _record("yolo", "package:yolo-detections"),
            _record("old", "package:old-model-decoder", error="requires plugin API 1, host provides 2"),
        ],
        {"yolo-detections": "yolo-detections>=0.2", "old-model-decoder": "old-model-decoder"},
        VERSIONS.get,
    )

    assert [group.section for group in groups] == [Section.FAILED, Section.INSTALLED, Section.BUILT_IN]
    assert _section(groups, Section.FAILED) == (
        PluginEntry(
            Section.FAILED,
            "old-model-decoder",
            subtitle="Requires plugin API 1, host provides 2",
            requirement="old-model-decoder",
        ),
    )
    assert _section(groups, Section.INSTALLED) == (
        PluginEntry(
            Section.INSTALLED, "yolo-detections", subtitle="0.2.1 · Decoder", requirement="yolo-detections>=0.2"
        ),
    )
    assert [(entry.name, entry.detail) for entry in _section(groups, Section.BUILT_IN)] == [
        ("Decoders", "2"),
        ("Playlist resolvers", "1"),
    ]


def test_a_package_lists_everything_it_provides_once_and_an_editable_source_is_marked() -> None:
    groups = plugin_catalog(
        [
            _record("lab_runs", "package:Lab_Experiments", PLAYLIST_RESOLVER_PLUGIN_TYPE),
            _record("lab_tracks", "package:Lab_Experiments"),
            _record("lab_boxes", "package:Lab_Experiments"),
        ],
        {"lab-experiments": "/home/me/lab-experiments"},
        VERSIONS.get,
    )

    (entry,) = _section(groups, Section.INSTALLED)
    assert entry.name == "lab-experiments"
    assert entry.subtitle == "1.0.0 · 2 Decoders · Playlist resolver · Editable"
    assert entry.requirement == "/home/me/lab-experiments"


def test_a_package_with_a_failed_part_is_listed_as_failed_with_the_first_reason() -> None:
    groups = plugin_catalog(
        [
            _record("yolo", "package:yolo-detections"),
            _record("yolo_live", "package:yolo-detections", error="does not implement decode"),
            _record("yolo_extra", "package:yolo-detections", error="PLUGIN_CLASS missing or invalid"),
        ],
        {"yolo-detections": "yolo-detections"},
        VERSIONS.get,
    )

    assert [(entry.name, entry.subtitle) for entry in _section(groups, Section.FAILED)] == [
        ("yolo-detections", "Does not implement decode")
    ]
    assert _section(groups, Section.INSTALLED) == ()


def test_a_selected_package_that_loaded_nothing_is_still_listed_for_removal() -> None:
    groups = plugin_catalog([], {"new-plugin": "new-plugin==1.0"}, VERSIONS.get)

    assert _section(groups, Section.INSTALLED) == (
        PluginEntry(Section.INSTALLED, "new-plugin", subtitle="Not loaded", requirement="new-plugin==1.0"),
    )


def test_only_packages_in_the_selection_can_be_changed() -> None:
    """A package installed some other way, or a failed built-in plugin, is listed without update or remove."""
    groups = plugin_catalog(
        [_record("pip_plugin", "package:pip-plugin"), _record("mot", "builtin", error="duplicate handler_type")],
        {},
        VERSIONS.get,
    )

    assert [(entry.name, entry.requirement) for entry in _section(groups, Section.INSTALLED)] == [("pip-plugin", None)]
    assert [(entry.name, entry.requirement) for entry in _section(groups, Section.FAILED)] == [("mot", None)]
