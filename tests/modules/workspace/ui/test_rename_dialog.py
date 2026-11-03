"""Asking for a new name tells a cancelled dialog from a name kept or restored to its default."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.ui.rename_dialog import RenameDialog, ask_new_name


def _answer(monkeypatch: pytest.MonkeyPatch, text: str, *, accept: bool = True) -> list[str]:
    """Answer the dialog with *text*, and return the names it was seeded with."""
    seeds: list[str] = []

    def enter(dialog: RenameDialog) -> int:
        seeds.append(dialog.name())
        dialog._name_edit.setText(text)
        return RenameDialog.DialogCode.Accepted if accept else RenameDialog.DialogCode.Rejected

    monkeypatch.setattr(RenameDialog, "exec", enter)
    return seeds


@pytest.mark.parametrize(
    ("entered", "accept", "expected"),
    [
        ("Gate", False, None),
        ("  North gate ", True, "North gate"),
        ("lot.mp4", True, ""),
        ("", True, ""),
    ],
)
def test_the_answer_is_none_when_cancelled_empty_for_the_default_name_and_else_the_name(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, entered: str, accept: bool, expected: str | None
) -> None:
    seeds = _answer(monkeypatch, entered, accept=accept)

    assert ask_new_name(None, "Gate", "lot.mp4") == expected
    assert seeds == ["Gate"]
