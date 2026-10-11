"""Tests for the centralized keyboard shortcut system."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QRect, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ax_devil.modules.chrome.key_chips import CHORD_BREAK, key_chip_parts
from ax_devil.modules.shortcuts.shortcuts import ShortcutDefinition, ShortcutManager
from ax_devil.modules.shortcuts.shortcuts_dialog import ShortcutsDialog, _ShortcutRow


@pytest.fixture()
def window(qtbot: QtBot) -> QWidget:
    """Let pytest-qt own the application and destroy each shortcut host."""
    w = QWidget()
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture()
def manager() -> ShortcutManager:
    return ShortcutManager()


def _make_definition(action_id: str = "test.action", key: str = "Ctrl+T") -> ShortcutDefinition:
    return ShortcutDefinition(action_id, "Test Action", "Test", QKeySequence(key))


class TestRegistration:
    def test_register_and_install(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        assert manager.get_action("test.action").shortcut().toString() == "Ctrl+T"
        assert manager.current_key_sequence("test.action") == QKeySequence("Ctrl+T")

    def test_default_shortcuts_do_not_share_keys(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register_defaults()
        manager.install(window)
        keys = [manager.get_action(d.action_id).shortcut().toString() for d in manager.definitions()]
        bound = [key for key in keys if key]
        assert bound and len(set(bound)) == len(bound)

    def test_duplicate_register_skipped(self, manager: ShortcutManager) -> None:
        defn = _make_definition()
        manager.register(defn)
        manager.register(defn)  # should not raise
        assert len(manager.definitions()) == 1

    def test_register_after_install_raises(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        with pytest.raises(RuntimeError, match="Cannot register"):
            manager.register(_make_definition("other.action", "Ctrl+O"))

    def test_install_twice_raises(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        with pytest.raises(RuntimeError, match="install.*twice"):
            manager.install(window)


class TestOverrides:
    def test_config_override_applied(self, window: QWidget) -> None:
        manager = ShortcutManager(config_overrides={"test.action": "Ctrl+X"})
        manager.register(_make_definition())
        manager.install(window)
        assert manager.get_action("test.action").shortcut().toString() == "Ctrl+X"

    def test_stale_override_pruned(self, window: QWidget) -> None:
        manager = ShortcutManager(config_overrides={"nonexistent.action": "Ctrl+Z"})
        manager.register(_make_definition())
        manager.install(window)
        overrides = manager.get_config_overrides()
        assert "nonexistent.action" not in overrides

    def test_default_taken_by_saved_override_is_left_unset(self, window: QWidget) -> None:
        """A default added later must not make the user's own binding of that key ambiguous."""
        manager = ShortcutManager(config_overrides={"other.action": "Ctrl+T"})
        manager.register(_make_definition())
        manager.register(_make_definition("other.action", "Ctrl+O"))
        manager.install(window)

        assert manager.get_action("other.action").shortcut().toString() == "Ctrl+T"
        assert manager.get_action("test.action").shortcut().isEmpty()
        assert manager.has_conflict(QKeySequence("Ctrl+T"), exclude_action_id="other.action") is None

    def test_empty_override_clears_shortcut(self, window: QWidget) -> None:
        manager = ShortcutManager(config_overrides={"test.action": ""})
        manager.register(_make_definition())
        manager.install(window)
        assert manager.get_action("test.action").shortcut().isEmpty()


class TestRebinding:
    def test_set_binding_saves_only_differences_from_default(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        manager.set_binding("test.action", QKeySequence("Ctrl+Y"))
        assert manager.get_action("test.action").shortcut().toString() == "Ctrl+Y"
        assert "test.action" in manager.get_config_overrides()
        manager.set_binding("test.action", QKeySequence("Ctrl+T"))
        assert "test.action" not in manager.get_config_overrides()

    def test_set_binding_clear(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        manager.set_binding("test.action", None)
        assert manager.get_action("test.action").shortcut().isEmpty()
        assert manager.current_key_sequence("test.action") is None

    def test_set_binding_unknown_action_raises(self, manager: ShortcutManager, window: QWidget) -> None:
        manager.register(_make_definition())
        manager.install(window)
        with pytest.raises(KeyError, match="Unknown shortcut"):
            manager.set_binding("unknown.action", QKeySequence("Ctrl+Z"))


def test_has_conflict_reports_the_bound_action(manager: ShortcutManager, window: QWidget) -> None:
    manager.register(_make_definition("action.a", "Ctrl+A"))
    manager.register(_make_definition("action.b", "Ctrl+B"))
    manager.install(window)
    assert manager.has_conflict(QKeySequence("Ctrl+A")) == "action.a"
    assert manager.has_conflict(QKeySequence("Ctrl+A"), exclude_action_id="action.a") is None
    assert manager.has_conflict(QKeySequence("F12")) is None
    assert manager.has_conflict(QKeySequence()) is None


def test_reset_clears_overrides(manager: ShortcutManager, window: QWidget) -> None:
    manager.register(_make_definition())
    manager.install(window)
    manager.set_binding("test.action", QKeySequence("Ctrl+Y"))
    assert manager.get_config_overrides() != {}
    manager.reset_to_defaults()
    assert manager.get_config_overrides() == {}
    assert manager.get_action("test.action").shortcut().toString() == "Ctrl+T"


def _shortcut_dialog_row(dialog: ShortcutsDialog, action_id: str) -> _ShortcutRow:
    """Return the shortcut dialog row for *action_id*."""
    return next(row for row in dialog._all_rows if row.action_id == action_id)


class TestShortcutsDialog:
    def test_shortcut_list_fills_the_opening_dialog(self, qtbot: QtBot) -> None:
        """The opening dialog shows many shortcut rows rather than a sliver above empty space."""
        manager = ShortcutManager()
        manager.register_defaults()
        dialog = ShortcutsDialog(manager)
        qtbot.addWidget(dialog)
        dialog.show()
        QCoreApplication.processEvents()

        viewport = dialog._list_scroll.viewport()
        visible_rows = [
            row
            for row in dialog._all_rows
            if viewport.rect().contains(QRect(row.mapTo(viewport, row.rect().topLeft()), row.size()))
        ]
        assert len(visible_rows) >= 8
        assert dialog._list_scroll.height() > dialog.height() * 2 // 3

    def test_ok_does_not_apply_conflicting_bindings(self, qtbot: QtBot, window: QWidget) -> None:
        manager = ShortcutManager()
        manager.register(_make_definition("action.a", "Ctrl+A"))
        manager.register(_make_definition("action.b", "Ctrl+B"))
        manager.install(window)

        dialog = ShortcutsDialog(manager)
        qtbot.addWidget(dialog)
        row = _shortcut_dialog_row(dialog, "action.a")
        row.set_key_sequence(QKeySequence("Ctrl+B"))

        dialog._on_ok()

        assert manager.get_action("action.a").shortcut().toString() == "Ctrl+A"
        assert manager.get_action("action.b").shortcut().toString() == "Ctrl+B"
        assert "conflicts with" in dialog._conflict_label.text()
        assert dialog.result() == 0

    def test_ok_applies_valid_shortcut_swap(self, qtbot: QtBot, window: QWidget) -> None:
        manager = ShortcutManager()
        manager.register(_make_definition("action.a", "Ctrl+A"))
        manager.register(_make_definition("action.b", "Ctrl+B"))
        manager.install(window)

        dialog = ShortcutsDialog(manager)
        qtbot.addWidget(dialog)
        first = _shortcut_dialog_row(dialog, "action.a")
        second = _shortcut_dialog_row(dialog, "action.b")
        first.set_key_sequence(QKeySequence("Ctrl+B"))
        second.set_key_sequence(QKeySequence("Ctrl+A"))

        dialog._on_ok()

        assert manager.get_action("action.a").shortcut().toString() == "Ctrl+B"
        assert manager.get_action("action.b").shortcut().toString() == "Ctrl+A"
        assert dialog.result() == int(ShortcutsDialog.DialogCode.Accepted)

    def test_editing_unrelated_row_keeps_existing_conflict_visible(self, qtbot: QtBot, window: QWidget) -> None:
        manager = ShortcutManager()
        manager.register(_make_definition("action.a", "Ctrl+A"))
        manager.register(_make_definition("action.b", "Ctrl+B"))
        manager.register(_make_definition("action.c", "Ctrl+C"))
        manager.install(window)

        dialog = ShortcutsDialog(manager)
        qtbot.addWidget(dialog)
        first = _shortcut_dialog_row(dialog, "action.a")
        third = _shortcut_dialog_row(dialog, "action.c")
        first.set_key_sequence(QKeySequence("Ctrl+B"))
        dialog._on_editing_finished(first)
        assert "conflicts with" in dialog._conflict_label.text()

        third.set_key_sequence(QKeySequence("Ctrl+Y"))
        dialog._on_editing_finished(third)

        assert "conflicts with" in dialog._conflict_label.text()


def test_shortcut_rows_show_keys_as_chips_and_edit_on_click(qtbot: QtBot, window: QWidget) -> None:
    manager = ShortcutManager()
    manager.register(_make_definition("action.a", "Ctrl+Shift+A"))
    manager.install(window)
    dialog = ShortcutsDialog(manager)
    qtbot.addWidget(dialog)
    dialog.show()
    row = _shortcut_dialog_row(dialog, "action.a")
    assert row.chips.parts() == ["Ctrl", "Shift", "A"]
    assert row.editor.isHidden()

    row.chips.clicked.emit()
    assert not row.editor.isHidden() and row.chips.isHidden()

    row.editor.setKeySequence(QKeySequence("Ctrl++"))
    row.editor.editingFinished.emit()
    assert row.editor.isHidden() and not row.chips.isHidden()
    assert row.chips.parts() == ["Ctrl", "+"]


def test_shortcut_rows_are_editable_from_the_keyboard(qtbot: QtBot, window: QWidget) -> None:
    """Tab reaches each row's keys, Enter opens the editor, and focus returns to the row when editing ends."""
    manager = ShortcutManager()
    manager.register(_make_definition("action.a", "Ctrl+A"))
    manager.install(window)
    dialog = ShortcutsDialog(manager)
    qtbot.addWidget(dialog)
    dialog.show()
    row = _shortcut_dialog_row(dialog, "action.a")
    assert row.chips.focusPolicy() & Qt.FocusPolicy.TabFocus

    row.chips.setFocus()
    QTest.keyClick(row.chips, Qt.Key.Key_Return)
    assert not row.editor.isHidden()
    qtbot.waitUntil(row.editor.hasFocus)

    row.editor.editingFinished.emit()
    assert row.editor.isHidden()
    assert row.chips.hasFocus()


def test_key_chips_split_chords_and_name_keys_like_the_platform() -> None:
    assert key_chip_parts(QKeySequence("Ctrl+K, Ctrl+Shift+S")) == ["Ctrl", "K", CHORD_BREAK, "Ctrl", "Shift", "S"]
    assert key_chip_parts(QKeySequence("Ctrl++")) == ["Ctrl", "+"]
    assert key_chip_parts(QKeySequence("Ctrl+,")) == ["Ctrl", ","]
    assert key_chip_parts(QKeySequence()) == []
