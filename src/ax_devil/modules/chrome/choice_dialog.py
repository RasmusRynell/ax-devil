"""A question the user must answer with one of a few labeled buttons, shown as a regular app dialog."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Generic, TypeVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from ax_devil.modules.chrome.base_dialog import BaseDialog

_AnswerT = TypeVar("_AnswerT")


class ChoiceDialog(BaseDialog, Generic[_AnswerT]):
    """Ask *text* and offer one button per label in *choices*; the chosen label's answer is `answer`.

    There is no close button and Escape does nothing: every way out is one of the buttons.
    """

    def __init__(
        self,
        title: str,
        text: str,
        choices: Mapping[str, _AnswerT],
        default: _AnswerT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, title=title, scroll_content=False, closable=False)
        self.setMinimumHeight(0)
        self.answer: _AnswerT | None = None
        message = QLabel(text)
        message.setWordWrap(True)
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.add_content_widget(message)
        for label, answer in choices.items():
            # `clicked` passes its checked state first, so the answer is bound by keyword.
            self.add_button(
                label, lambda _checked=False, answer=answer: self._choose(answer), is_default=answer == default
            )
        self._button_layout.addStretch()  # The base layout leads with a stretch; a trailing one centers the row.

    def reject(self) -> None:
        """Ignore Escape; only a button ends the question."""

    def _choose(self, answer: _AnswerT) -> None:
        self.answer = answer
        self.accept()


def ask(parent: QWidget | None, title: str, text: str, choices: Mapping[str, _AnswerT], default: _AnswerT) -> _AnswerT:
    """Show a question and return the chosen label's answer."""
    with ChoiceDialog(title, text, choices, default, parent) as dialog:
        dialog.exec()
        assert dialog.answer is not None
        return dialog.answer
