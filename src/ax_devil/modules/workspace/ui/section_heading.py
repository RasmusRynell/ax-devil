"""The heading every sidebar view gives a browser section: its caption, and the section's action beside it."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ax_devil.modules.chrome.list_row import muted_label
from ax_devil.modules.chrome.section_caption import SectionCaption
from ax_devil.modules.chrome.small_button import SmallButton
from ax_devil.modules.chrome.theme import SEE_THROUGH
from ax_devil.modules.chrome.tokens import Space
from ax_devil.modules.workspace.ui.browser_rows import WorkspaceBrowserSection


def _hidden_note(hidden: int) -> str:
    """Return what a caption adds for *hidden* rows the list leaves out, or nothing for none."""
    return f"{hidden} filtered out" if hidden else ""


def section_accessible_text(section: WorkspaceBrowserSection, hidden: int) -> str:
    """Return what a screen reader says for the caption of *section*'s heading: the same title, count and note."""
    counted = f"{section.kind.title}, {len(section.rows)}"
    note = _hidden_note(hidden)
    return f"{counted}, {note}" if note else counted


def heading_look(section: WorkspaceBrowserSection, hidden: int, folded: bool | None) -> tuple[object, ...]:
    """Return the values ``SectionHeading`` draws or binds; a heading can be kept while they stay equal."""
    return (section.kind.title, len(section.rows) or None, section.mark_color, hidden, folded, section.action)


class SectionHeading(QWidget):
    """The heading of *section*: its title and row count, then how many of its rows the list leaves out (*hidden*),
    marked in its kind's color or muted, with a fold arrow when *folded* is given, and its action as a button at the
    right.

    A section without rows has no count, and says its kind's empty text under the caption, *text_indent* in. The
    button never folds the section. ``look`` is what ``heading_look`` returned for what the heading shows.
    """

    def __init__(
        self, section: WorkspaceBrowserSection, text_indent: int, *, hidden: int = 0, folded: bool | None = None
    ) -> None:
        super().__init__()
        self.setStyleSheet(SEE_THROUGH)
        self.look = heading_look(section, hidden, folded)
        kind = section.kind
        action = section.action
        caption = SectionCaption(
            kind.title, len(section.rows) or None, section.mark_color.color, note=_hidden_note(hidden), folded=folded
        )
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, Space.M if action is not None else 0, 0)
        line.setSpacing(0)
        line.addWidget(caption, 1)
        if action is not None:
            button = SmallButton(action.text, action.icon)
            button.setFixedWidth(button.sizeHint().width())
            button.setToolTip(action.tooltip)
            button.clicked.connect(lambda _checked=False: action.run())
            line.addWidget(button)
        says_empty = not section.rows and bool(kind.empty_text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.S if says_empty else 0)
        layout.setSpacing(0)
        layout.addLayout(line)
        if says_empty:
            note = muted_label(kind.empty_text)
            note.setContentsMargins(text_indent, 0, 0, 0)
            layout.addWidget(note)
