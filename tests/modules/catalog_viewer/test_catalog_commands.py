"""``ax-devil catalog``: what an agent editing a catalog relies on to check its work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ax_devil.modules.catalog_viewer.cli import catalog
from ax_devil.modules.scene.rendering import SceneRenderCatalogLoader, SceneRenderCatalogStore
from ax_devil.modules.scene.rendering.catalog import CatalogJsonDocument


@pytest.fixture
def config(tmp_path: Path) -> list[str]:
    """Return a --config option for a configuration that keeps catalogs in a folder of this test."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"version": "2.0", "storage": {"render_catalogs_dir": str(tmp_path / "catalogs")}}), encoding="utf-8"
    )
    return ["--config", str(path)]


def _new(name: str, config: list[str]) -> Path:
    result = CliRunner().invoke(catalog, ["new", name, *config])
    assert result.exit_code == 0, result.output
    return Path(result.output.strip())


def test_new_creates_an_editable_copy_that_list_shows(tmp_path: Path, config: list[str]) -> None:
    path = _new("Night shift", config)
    listed = CliRunner().invoke(catalog, ["list", *config])

    assert path.parent == tmp_path / "catalogs" and path.exists()
    assert "Night shift" in listed.output and str(path) in listed.output
    assert "* Standard" in listed.output


def test_check_passes_a_working_catalog_and_names_where_a_broken_one_fails(qtbot: QtBot, config: list[str]) -> None:
    runner = CliRunner()
    path = _new("Checked", config)

    working = runner.invoke(catalog, ["check", str(path), *config])
    path.write_text(path.read_text(encoding="utf-8").replace('"schema_version": 3', '"schema_version": 99', 1))
    broken = runner.invoke(catalog, ["check", str(path), *config])

    assert working.exit_code == 0, working.output
    assert "object types" in working.output
    assert broken.exit_code == 1
    assert "$.metadata.schema_version" in broken.output


def test_render_writes_the_sheets_the_viewer_shows(qtbot: QtBot, tmp_path: Path, config: list[str]) -> None:
    runner = CliRunner()
    out = tmp_path / "sheets"
    widgets = set(QApplication.topLevelWidgets())

    one = runner.invoke(catalog, ["render", "--out", str(out), "--sheet", "overview", *config])
    unknown = runner.invoke(catalog, ["render", "--out", str(out), "--sheet", "nothing", *config])

    assert one.exit_code == 0, one.output
    assert [path.name for path in out.iterdir()] == ["overview.png"]
    assert QImage(str(out / "overview.png")).width() == 1280
    assert unknown.exit_code == 1 and "overview" in unknown.output
    assert set(QApplication.topLevelWidgets()) == widgets


def test_render_releases_the_renderer_when_export_fails(
    qtbot: QtBot, tmp_path: Path, config: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error after native rendering must still destroy the temporary export surface."""
    from ax_devil.modules.video_player.engine.data_types import VideoFrameWithOverlays
    from ax_devil.modules.video_player.engine.quick.image_renderer import FrameImageRenderer

    render_frame = FrameImageRenderer.render_frame
    widgets = set(QApplication.topLevelWidgets())

    def fail_after_render(renderer: FrameImageRenderer, frame: VideoFrameWithOverlays) -> QImage:
        render_frame(renderer, frame)
        raise RuntimeError("Export interrupted")

    monkeypatch.setattr(FrameImageRenderer, "render_frame", fail_after_render)
    result = CliRunner().invoke(catalog, ["render", "--out", str(tmp_path / "sheets"), *config])

    assert result.exit_code == 1
    assert str(result.exception) == "Export interrupted"
    assert set(QApplication.topLevelWidgets()) == widgets


def test_render_reports_failed_png_write_and_releases_renderer(qtbot: QtBot, tmp_path: Path, config: list[str]) -> None:
    """A destination occupied by a directory must fail without reporting a successful export."""
    out = tmp_path / "sheets"
    target = out / "overview.png"
    target.mkdir(parents=True)
    widgets = set(QApplication.topLevelWidgets())

    result = CliRunner().invoke(catalog, ["render", "--out", str(out), "--sheet", "overview", *config])

    assert result.exit_code == 1
    assert str(target) in result.output
    assert target.is_dir()
    assert set(QApplication.topLevelWidgets()) == widgets


def test_check_uses_the_revision_read_before_an_editor_saves_again(
    qtbot: QtBot, config: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _new("Checked", config)
    read_document = SceneRenderCatalogLoader.read_document
    saved = False

    def read_then_save(catalog_path: Path) -> CatalogJsonDocument:
        nonlocal saved
        document = read_document(catalog_path)
        if catalog_path == path and not saved:
            saved = True
            path.write_text("{ unfinished save", encoding="utf-8")
        return document

    monkeypatch.setattr(SceneRenderCatalogLoader, "read_document", staticmethod(read_then_save))

    checked = CliRunner().invoke(catalog, ["check", str(path), *config])
    next_check = CliRunner().invoke(catalog, ["check", str(path), *config])

    assert checked.exit_code == 0, checked.output
    assert "object types" in checked.output
    assert next_check.exit_code == 1
    assert "does not load" in next_check.output


def test_use_makes_a_catalog_the_default(tmp_path: Path, config: list[str]) -> None:
    path = _new("Chosen", config)

    result = CliRunner().invoke(catalog, ["use", str(path), *config])

    assert result.exit_code == 0, result.output
    assert SceneRenderCatalogStore(tmp_path / "catalogs").list_catalogs_with_errors().default_path == path


def test_catalog_without_a_command_starts_the_app_with_the_viewer_open() -> None:
    started: list[str] = []

    result = CliRunner().invoke(catalog, [], obj={"run_catalog_viewer": lambda: started.append("viewer")})

    assert result.exit_code == 0, result.output
    assert started == ["viewer"]
