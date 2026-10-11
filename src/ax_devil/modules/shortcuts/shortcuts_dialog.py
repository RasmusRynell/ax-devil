"""Keyboard shortcuts settings dialog."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ax_devil.modules.chrome import BaseDialog
from ax_devil.modules.chrome.appearance import follow_appearance
from ax_devil.modules.chrome.content_scroll_area import ContentScrollArea
from ax_devil.modules.chrome.key_chips import KeyChips
from ax_devil.modules.chrome.theme import StatusColor
from ax_devil.modules.chrome.tokens import Radius, Space, TextRole
from ax_devil.modules.shortcuts.shortcuts import ShortcutDefinition, ShortcutManager


class _ShortcutRow(QFrame):
    """One shortcut row: action name + key sequence editor + clear button."""

    def __init__(
        self,
        definition: ShortcutDefinition,
        current_sequence: QKeySequence | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.definition = definition
        self.setObjectName("ShortcutRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.XL, Space.XS, Space.M, Space.XS)
        layout.setSpacing(Space.M)

        self._name_label = QLabel(definition.display_name)
        self._name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._name_label, 1)

        # The keys show as chips; clicking them swaps in the editor until editing finishes.
        self._chips = KeyChips(current_sequence or QKeySequence(), "Not set", self)
        self._chips.setToolTip("Click to change")
        self._chips.clicked.connect(self.start_editing)
        layout.addWidget(self._chips)

        self._editor = QKeySequenceEdit(current_sequence or QKeySequence(), self)
        self._editor.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._editor.setClearButtonEnabled(True)
        self._editor.hide()
        self._editor.keySequenceChanged.connect(self._chips.set_key_sequence)
        self._editor.editingFinished.connect(self.stop_editing)
        self._editor.installEventFilter(self)
        layout.addWidget(self._editor)
        # The chips prefer the editor's size, so the row keeps its layout when editing starts.
        follow_appearance(self._chips, self._match_editor_size)
        self._update_accessible_name()
        self._editor.keySequenceChanged.connect(self._update_accessible_name)

    @property
    def editor(self) -> QKeySequenceEdit:
        """Return the key sequence editor widget."""
        return self._editor

    @property
    def chips(self) -> KeyChips:
        """Return the chips that show the keys while the row is not being edited."""
        return self._chips

    def start_editing(self) -> None:
        """Replace the chips with the editor and focus it."""
        self._chips.hide()
        self._editor.show()
        self._editor.setFocus()

    def stop_editing(self) -> None:
        """Show the entered keys as chips again, keeping keyboard focus on the row."""
        had_focus = self._editor.hasFocus()
        self._chips.show()
        if had_focus:
            self._chips.setFocus()
        self._editor.hide()

    def _match_editor_size(self) -> None:
        hint = self._editor.sizeHint()
        self._chips.set_preferred_width(hint.width())
        self._chips.setMinimumHeight(hint.height())

    def _update_accessible_name(self) -> None:
        keys = self._editor.keySequence().toString(QKeySequence.SequenceFormat.NativeText) or "not set"
        self._chips.setAccessibleName(f"{self.definition.display_name}: {keys}")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Stop editing when the editor loses focus, as when another row is clicked."""
        if watched is self._editor and event.type() == QEvent.Type.FocusOut and not self._editor.isHidden():
            self.stop_editing()
        return super().eventFilter(watched, event)

    @property
    def action_id(self) -> str:
        """Return the action id for this row."""
        return self.definition.action_id

    def key_sequence(self) -> QKeySequence:
        """Return the currently entered key sequence."""
        return self._editor.keySequence()

    def set_key_sequence(self, seq: QKeySequence) -> None:
        """Set the key sequence in the editor and its chips."""
        self._editor.setKeySequence(seq)
        self._chips.set_key_sequence(seq)

    def matches_filter(self, text: str) -> bool:
        """Return whether this row matches a search filter."""
        lower = text.lower()
        return (
            lower in self.definition.display_name.lower()
            or lower in self.definition.category.lower()
            or lower in self._editor.keySequence().toString().lower()
        )


class _CategorySection(QWidget):
    """A category header + its shortcut rows."""

    def __init__(self, category: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[_ShortcutRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QLabel(category)
        self._header.setObjectName("CategoryHeader")
        TextRole.CAPTION.apply(self._header)
        layout.addWidget(self._header)

        self._row_container = QWidget(self)
        self._row_layout = QVBoxLayout(self._row_container)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(0)
        layout.addWidget(self._row_container)

    def add_row(self, row: _ShortcutRow) -> None:
        """Add a shortcut row to this category."""
        self._rows.append(row)
        self._row_layout.addWidget(row)

    @property
    def rows(self) -> list[_ShortcutRow]:
        """Return all rows in this category."""
        return self._rows

    def apply_filter(self, text: str) -> bool:
        """Show/hide rows matching filter. Return True if any row visible."""
        any_visible = False
        for row in self._rows:
            visible = row.matches_filter(text) if text else True
            row.setVisible(visible)
            if visible:
                any_visible = True
        self.setVisible(any_visible)
        return any_visible


class ShortcutsDialog(BaseDialog):
    """Dialog for viewing and rebinding keyboard shortcuts."""

    def __init__(self, shortcut_manager: ShortcutManager, parent: QWidget | None = None) -> None:
        super().__init__(
            parent=parent,
            title="Keyboard Shortcuts",
            modal=True,
            scroll_content=False,
        )
        self._shortcut_manager = shortcut_manager
        self._definitions = shortcut_manager.definitions()
        self._sections: list[_CategorySection] = []
        self._all_rows: list[_ShortcutRow] = []

        follow_appearance(self, self._apply_styles)
        self._setup_content()
        self._setup_buttons()

    def _apply_styles(self) -> None:
        """Apply palette-aware styles."""
        error = StatusColor.ERROR.color(self.palette()).name()
        stylesheet = f"""
            QLabel#CategoryHeader {{
                padding: {Space.M}px {Space.M}px {Space.XS}px {Space.M}px;
                color: palette(placeholder-text);
                background: palette(window);
                border-bottom: 1px solid palette(mid);
            }}
            QFrame#ShortcutRow {{
                border-bottom: 1px solid palette(mid);
            }}
            QFrame#ShortcutRow:hover {{
                background: palette(alternate-base);
            }}
            QLineEdit#SearchBar {{
                padding: {Space.S}px {Space.M}px;
                border: 1px solid palette(mid);
                border-radius: {Radius.CONTROL}px;
            }}
            QLineEdit#SearchBar:focus {{
                border-color: palette(link);
            }}
            QLabel#ConflictLabel {{
                color: {error};
                padding: {Space.XS}px {Space.M}px;
            }}
        """
        self.setStyleSheet(stylesheet)

    def _setup_content(self) -> None:
        """Build the search bar and category-grouped shortcut list."""
        self._search = QLineEdit(self)
        self._search.setObjectName("SearchBar")
        self._search.setPlaceholderText("Search shortcuts")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        self._content_layout.addWidget(self._search)

        scroll_content = QWidget()
        self._list_layout = QVBoxLayout(scroll_content)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        categories: dict[str, list[ShortcutDefinition]] = {}
        for defn in self._definitions:
            categories.setdefault(defn.category, []).append(defn)

        for category, defs in categories.items():
            section = _CategorySection(category, scroll_content)
            for defn in defs:
                current = self._shortcut_manager.current_key_sequence(defn.action_id)
                row = _ShortcutRow(defn, current, section)
                row.editor.editingFinished.connect(lambda r=row: self._on_editing_finished(r))
                section.add_row(row)
                self._all_rows.append(row)
            self._sections.append(section)
            self._list_layout.addWidget(section)

        self._list_layout.addStretch()
        self._list_scroll = ContentScrollArea(scroll_content)
        self._content_layout.addWidget(self._list_scroll, 1)

        self._conflict_label = QLabel("")
        self._conflict_label.setObjectName("ConflictLabel")
        self._conflict_label.setWordWrap(True)
        self._content_layout.addWidget(self._conflict_label)

    def _setup_buttons(self) -> None:
        """Add Reset, OK, and Cancel buttons."""
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_reset)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        self._button_layout.insertWidget(0, reset_btn)
        self._button_layout.addWidget(ok_btn)
        self._button_layout.addWidget(cancel_btn)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        """Filter visible rows by search text."""
        for section in self._sections:
            section.apply_filter(text)

    def _on_editing_finished(self, row: _ShortcutRow) -> None:
        """Check for conflicts after a shortcut edit."""
        conflict_id = self._conflicting_action_id(row)
        if conflict_id is not None:
            self._show_conflict(row, conflict_id)
            return

        remaining_conflict = self._first_conflict()
        if remaining_conflict is not None:
            conflict_row, existing_conflict_id = remaining_conflict
            self._show_conflict(conflict_row, existing_conflict_id)
        else:
            self._conflict_label.setText("")

    def _on_reset(self) -> None:
        """Reset all shortcuts to defaults."""
        self._conflict_label.setText("")
        for row in self._all_rows:
            row.set_key_sequence(row.definition.default_key_sequence or QKeySequence())

    def _on_ok(self) -> None:
        """Apply changed bindings and close."""
        conflict = self._first_conflict()
        if conflict is not None:
            row, conflict_id = conflict
            self._show_conflict(row, conflict_id)
            row.start_editing()
            return

        for row in self._all_rows:
            new_str = row.key_sequence().toString()
            current = self._shortcut_manager.current_key_sequence(row.action_id)
            current_str = current.toString() if current else ""
            if new_str != current_str:
                self._shortcut_manager.set_binding(
                    row.action_id, row.key_sequence() if not row.key_sequence().isEmpty() else None
                )
        self.accept()

    def _conflicting_action_id(self, row: _ShortcutRow) -> str | None:
        """Return another row's action id when *row* duplicates its non-empty shortcut."""
        new_str = row.key_sequence().toString()
        if not new_str:
            return None
        for other in self._all_rows:
            if other is row:
                continue
            if other.key_sequence().toString() == new_str:
                return other.action_id
        return None

    def _first_conflict(self) -> tuple[_ShortcutRow, str] | None:
        """Return the first duplicate shortcut row and conflicting action id."""
        for row in self._all_rows:
            conflict_id = self._conflicting_action_id(row)
            if conflict_id is not None:
                return row, conflict_id
        return None

    def _show_conflict(self, row: _ShortcutRow, conflict_id: str) -> None:
        """Show a conflict message for *row* against *conflict_id*."""
        name = self._shortcut_manager.get_definition(conflict_id).display_name
        self._conflict_label.setText(f"⚠ '{row.key_sequence().toString()}' conflicts with '{name}'")
