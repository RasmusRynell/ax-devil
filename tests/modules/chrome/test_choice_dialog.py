"""The question dialog returns the answer of the button the user pressed, and None when it is dismissed."""

from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.choice_dialog import ChoiceDialog
from ax_devil.modules.chrome.chrome_window import ChromeWindow
from tests.helpers.dialogs import content_scroll
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


def test_only_a_button_ends_the_question(qtbot: QtBot) -> None:
    parent = ChromeWindow(use_custom_frame=True)
    qtbot.addWidget(parent)
    dialog = ChoiceDialog("Title", "Question?", {"Yes": True, "No": False}, True, parent)
    dialog.show()
    close_button = dialog.findChild(QWidget, "AxDevilCloseButton")
    assert close_button is not None and not close_button.isVisible()
    dialog.reject()  # Escape
    dialog.close()
    assert dialog.isVisible()
    assert dialog.answer is None


def test_a_long_notice_scrolls_with_its_button_in_view(qtbot: QtBot) -> None:
    """A notice taller than the screen scrolls instead of clipping, and OK stays visible."""
    dialog = ChoiceDialog(
        "Open Content", "\n".join(f"Clip {index}: could not open" for index in range(200)), {"OK": True}, True
    )
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()
    assert content_scroll(dialog).verticalScrollBar().maximum() > 0
    assert dialog.rect().contains(button(dialog, "OK").geometry())
