"""Doubles and lookups the Add Content dialog tests share."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Any

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.core import LiveOverlayMode
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.ui.add_content.add_live_stream_dialog import AddLiveStreamDialog
from tests.helpers.forms import form_field
from tests.helpers.workspace import FakeResolutionContext


def dialog_context() -> FakeResolutionContext:
    return FakeResolutionContext(WorkspaceIntake(DialogOptionProvider()))


def ok_button(dialog: QWidget) -> QPushButton:
    return next(button for button in dialog.findChildren(QPushButton) if button.text() == "OK")


def shows_text(dialog: QWidget, text: str) -> bool:
    return any(label.text() == text for label in dialog.findChildren(QLabel))


def set_overlay_mode(dialog: AddLiveStreamDialog, mode: LiveOverlayMode) -> None:
    combo = form_field(dialog, "Overlay Mode", QComboBox)
    combo.setCurrentIndex(combo.findData(mode.value))


def select_data(combo: QComboBox, data: str) -> None:
    combo.setCurrentIndex(combo.findData(data))


class DialogOptionProvider:
    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_FILE", display_name="Test File"),)

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        return (WorkspaceDecoderOption(handler_type="TEST_HANDLER", display_name="Test Handler"),)


def use_defaults(monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any], resolved: dict[str, Any]) -> None:
    """Replace the dialog's config lookup; the dialog has no other seam for configured defaults."""
    monkeypatch.setattr(AddLiveStreamDialog, "_load_defaults", lambda self: (raw, resolved))


@dataclass
class Discovery:
    """Device responses and discovery jobs held until finished, leaving the dialog's discovery lifecycle intact."""

    qtbot: QtBot
    sources: tuple[str, ...] = ("other/source", "analytics/source")
    topics: tuple[str, ...] = ("other.topic", "com.axis.scene.frame.v1")
    jobs: list[tuple[Event, list[bool]]] = field(default_factory=list)  # Each job's go-ahead and its delivery.

    def finish(self) -> None:
        """Let the oldest held request reach the device and wait until the dialog has its answer."""
        go, delivered = self.jobs.pop(0)
        go.set()
        self.qtbot.waitUntil(lambda: bool(delivered))

    def release_all(self) -> None:
        for go, _delivered in self.jobs:
            go.set()

    def release_cancelled(self) -> None:
        """Release obsolete work; a cancelled job no longer delivers an answer."""
        go, _delivered = self.jobs.pop(0)
        go.set()
