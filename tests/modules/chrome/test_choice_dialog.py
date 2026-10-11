"""The question dialog returns the answer of the button the user pressed, and None when it is dismissed."""

from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.choice_dialog import ChoiceDialog
from tests.helpers.widgets import button


def _dialog(qtbot: QtBot) -> ChoiceDialog[str]:
    dialog = ChoiceDialog("Title", "Question?", {"Save": "save", "Don't Save": "discard", "Cancel": "cancel"}, "save")
    qtbot.addWidget(dialog)
    return dialog


def test_each_button_gives_its_own_answer(qtbot: QtBot) -> None:
    for label, answer in (("Save", "save"), ("Don't Save", "discard"), ("Cancel", "cancel")):
        dialog = _dialog(qtbot)
        button(dialog, label).click()
        assert dialog.answer == answer


def test_dismissing_the_dialog_gives_no_answer(qtbot: QtBot) -> None:
    dialog = _dialog(qtbot)
    dialog.reject()
    assert dialog.answer is None
