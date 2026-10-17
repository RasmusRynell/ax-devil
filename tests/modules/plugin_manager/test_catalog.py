"""The Plugins view lists failed plugins, installed plugin packages and the built-in plugins from the registry."""

from __future__ import annotations

from ax_devil.modules.plugin_manager.catalog import (
    PluginEntry,
    PluginGroup,
    PluginPart,
    Reads,
    Section,
    plugin_catalog,
)
from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    PluginDefinitionBase,
    PluginRecord,
    PluginStatus,
)
from tests.helpers.plugin_records import file_handler, payload_handler, plugin_record

VERSIONS = {"yolo-detections": "0.2.1", "lab-experiments": "1.0.0"}


def _resolver(plugin_id: str, package: str | None, description: str | None = None) -> PluginRecord:
    return PluginRecord(
        definition=PluginDefinitionBase(
            plugin_type=PLAYLIST_RESOLVER_PLUGIN_TYPE,
            plugin_id=plugin_id,
            display_name=plugin_id,
            description=description,
        ),
        package=package,
        status=PluginStatus.LOADED,
    )


def _section(groups: tuple[PluginGroup, ...], section: Section) -> tuple[PluginEntry, ...]:
    return next((group.entries for group in groups if group.section is section), ())


def test_records_are_grouped_into_failed_installed_and_built_in() -> None:
    groups = plugin_catalog(
        [
            plugin_record("mot", None, files=(file_handler("MOT_FILE", "mot"),)),
            _resolver("folder_pair", None),
            plugin_record("yolo", "yolo-detections", files=(file_handler("YOLO", "yolo"),)),
            plugin_record("old", "old-model-decoder", error="requires plugin API 1, host provides 2"),
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
    assert [(entry.name, entry.detail, entry.requirement) for entry in _section(groups, Section.INSTALLED)] == [
        ("yolo-detections", "0.2.1", "yolo-detections>=0.2")
    ]
    assert [entry.name for entry in _section(groups, Section.BUILT_IN)] == ["mot", "folder_pair"]


def test_a_plugin_with_one_decoder_is_a_single_row_that_names_the_decoder_only_when_it_differs() -> None:
    groups = plugin_catalog(
        [
            plugin_record("mot", None, files=(file_handler("MOT_FILE", "MOT Annotations"),)),
            plugin_record("uvg", None, files=(file_handler("UVG", "uvg", "Detections with polygons"),)),
            _resolver("folder_pair", None, "Match videos and overlays by file name."),
        ],
        {},
        VERSIONS.get,
    )

    assert [(entry.name, entry.subtitle, entry.reads, entry.resolver, entry.parts) for entry in groups[0].entries] == [
        ("mot", "MOT Annotations", (Reads.FILE,), False, ()),
        ("uvg", "Detections with polygons", (Reads.FILE,), False, ()),
        ("folder_pair", "Match videos and overlays by file name.", (), True, ()),
    ]


def test_a_decoder_that_reads_files_and_live_messages_under_one_handler_type_is_one_part_reading_both() -> None:
    groups = plugin_catalog(
        [
            pluginplugin_record(
                "adf",
                None,
                files=(file_handler("ADF_FRAME", "ADF Frame"), file_handler("ADF_TRACKS", "ADF Tracks")),
                payloads=(payload_handler("ADF_FRAME", "ADF Frame"), payload_handler("ADF_LIVE_ONLY", "ADF Live")),
            )
        ],
        {},
        VERSIONS.get,
    )

    (entry,) = _section(groups, Section.BUILT_IN)
    assert entry.parts == (
        PluginPart("ADF Frame", reads=(Reads.FILE, Reads.LIVE)),
        PluginPart("ADF Tracks", reads=(Reads.FILE,)),
        PluginPart("ADF Live", reads=(Reads.LIVE,)),
    )


def test_a_package_lists_everything_it_provides_under_one_row_with_its_version() -> None:
    groups = plugin_catalog(
        [
            _resolver("lab_runs", "lab-experiments", "Load lab runs."),
            plugin_record("lab_tracks", "lab-experiments", files=(file_handler("LAB", "Lab tracks"),)),
            plugin_record("lab_boxes", "lab-experiments", files=(file_handler("BOX", "Lab boxes"),)),
        ],
        {"lab-experiments": "/home/me/lab-experiments"},
        VERSIONS.get,
    )

    (entry,) = _section(groups, Section.INSTALLED)
    assert (entry.name, entry.detail, entry.requirement) == ("lab-experiments", "1.0.0", "/home/me/lab-experiments")
    assert entry.parts == (
        PluginPart("lab_runs", "Load lab runs.", resolver=True),
        PluginPart("Lab tracks", reads=(Reads.FILE,)),
        PluginPart("Lab boxes", reads=(Reads.FILE,)),
    )


def test_a_package_with_a_failed_part_is_listed_as_failed_with_the_first_reason() -> None:
    groups = plugin_catalog(
        [
            plugin_record("yolo", "yolo-detections"),
            plugin_record("yolo_live", "yolo-detections", error="does not implement decode"),
            plugin_record("yolo_extra", "yolo-detections", error="PLUGIN_CLASS missing or invalid"),
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
        [plugin_record("pip_plugin", "pip-plugin"), plugin_record("mot", None, error="duplicate handler_type")],
        {},
        VERSIONS.get,
    )

    assert [(entry.name, entry.requirement) for entry in _section(groups, Section.INSTALLED)] == [("pip-plugin", None)]
    assert [(entry.name, entry.requirement) for entry in _section(groups, Section.FAILED)] == [("mot", None)]
