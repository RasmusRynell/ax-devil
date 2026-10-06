from PySide6.QtCore import QCoreApplication, QEvent
from pytestqt.qtbot import QtBot

from ax_devil.modules.shortcuts.shortcuts import ShortcutManager
from ax_devil.modules.workspace.split_view import SplitDirection, SplitView
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget


class _DummyWidget(WorkspaceWidget):
    """Minimal workspace widget for testing."""

    def _setup_widget_ui(self) -> None:
        pass

    def get_display_name(self) -> str:
        return "Dummy"


class _AttachAwareWidget(_DummyWidget):
    """Workspace widget that records split-view attachment notifications."""

    def __init__(self, split_view: SplitView | None = None) -> None:
        self._split_view = split_view
        self.attach_count = 0
        self.welcome_visible_during_attach: bool | None = None
        super().__init__()

    def on_workspace_attached(self) -> None:
        """Record that the widget has been attached to the workspace."""
        self.attach_count += 1
        if self._split_view is not None:
            self.welcome_visible_during_attach = self._split_view._welcome_widget.isVisible()


class _FailingAttachWidget(_DummyWidget):
    """Workspace widget that fails during post-attach startup."""

    def on_workspace_attached(self) -> None:
        """Raise a startup error."""
        raise RuntimeError("attach failed")


class _LifecycleWidget(_AttachAwareWidget):
    """Workspace widget that records attachment and cleanup calls."""

    def __init__(self, split_view: SplitView | None = None, *, fail_attach: bool = False) -> None:
        self.cleanup_count = 0
        self._fail_attach = fail_attach
        super().__init__(split_view)

    def on_workspace_attached(self) -> None:
        """Record attachment and optionally fail during startup."""
        super().on_workspace_attached()
        if self._fail_attach:
            raise RuntimeError("attach failed")

    def cleanup(self) -> None:
        """Record resource cleanup."""
        self.cleanup_count += 1


def _make_split_view(qtbot: QtBot) -> SplitView:
    sv = SplitView()
    qtbot.addWidget(sv)
    sv.show()
    return sv


def _make_dummy(qtbot: QtBot) -> _DummyWidget:
    w = _DummyWidget()
    qtbot.addWidget(w)
    return w


def test_replace_in_unpinned_leaf(qtbot: QtBot) -> None:
    """replace_or_open replaces an unpinned widget in-place."""
    sv = _make_split_view(qtbot)
    w1 = _make_dummy(qtbot)
    sv.add_workspace_widget(w1)
    assert sv.get_widget_count() == 1

    w2 = _make_dummy(qtbot)
    sv.replace_or_open(w2)
    assert sv.get_widget_count() == 1
    # The new widget should be present in a leaf
    leaves = sv._list_leaves()
    widgets = [leaf.get_widget() for leaf in leaves if leaf.get_widget() is not None]
    assert w2 in widgets
    assert w1 not in widgets


def test_pinned_leaf_is_skipped(qtbot: QtBot) -> None:
    """replace_or_open skips pinned widgets and creates a new split."""
    sv = _make_split_view(qtbot)
    w1 = _make_dummy(qtbot)
    sv.add_workspace_widget(w1)
    w1.set_pinned(True)

    w2 = _make_dummy(qtbot)
    sv.replace_or_open(w2)
    assert sv.get_widget_count() == 2


def test_all_pinned_creates_new_split(qtbot: QtBot) -> None:
    """replace_or_open creates a new split when all existing widgets are pinned."""
    sv = _make_split_view(qtbot)
    w1 = _make_dummy(qtbot)
    w2 = _make_dummy(qtbot)
    sv.add_workspace_widget(w1)
    sv.add_workspace_widget(w2)
    w1.set_pinned(True)
    w2.set_pinned(True)
    assert sv.get_widget_count() == 2

    w3 = _make_dummy(qtbot)
    sv.replace_or_open(w3)
    assert sv.get_widget_count() == 3


def test_mixed_panes_replace_only_the_unpinned_widget(qtbot: QtBot) -> None:
    """A pinned first pane survives replacement and only the displaced pane is cleaned."""
    sv = _make_split_view(qtbot)
    pinned, unpinned, replacement = (_LifecycleWidget(sv) for _ in range(3))
    for widget in (pinned, unpinned, replacement):
        qtbot.addWidget(widget)
    sv.add_workspace_widget(pinned)
    sv.add_workspace_widget(unpinned)
    pinned.set_pinned(True)

    sv.replace_or_open(replacement)

    assert [leaf.get_widget() for leaf in sv._list_leaves()] == [pinned, replacement]
    assert sv.get_widget_count() == 2
    assert sv.get_focused_widget() is replacement
    assert (pinned.cleanup_count, unpinned.cleanup_count, replacement.cleanup_count) == (0, 1, 0)
    assert replacement.attach_count == 1


def test_replace_when_empty(qtbot: QtBot) -> None:
    """replace_or_open on an empty SplitView adds the widget."""
    sv = _make_split_view(qtbot)
    assert sv.get_widget_count() == 0

    w1 = _make_dummy(qtbot)
    sv.replace_or_open(w1)
    assert sv.get_widget_count() == 1


def test_attach_hook_runs_after_widget_is_added(qtbot: QtBot) -> None:
    """SplitView notifies widgets after they are attached."""
    sv = _make_split_view(qtbot)
    widget = _AttachAwareWidget(sv)
    qtbot.addWidget(widget)

    sv.replace_or_open(widget)

    assert widget.attach_count == 1
    assert widget.welcome_visible_during_attach is False
    assert sv.get_focused_widget() is widget


def test_replace_restores_existing_widget_when_attach_fails(qtbot: QtBot) -> None:
    """replace_or_open restores the previous pane when replacement startup fails."""
    sv = _make_split_view(qtbot)
    existing = _make_dummy(qtbot)
    sv.replace_or_open(existing)
    failing = _FailingAttachWidget()
    qtbot.addWidget(failing)

    try:
        sv.replace_or_open(failing)
    except RuntimeError as exc:
        assert str(exc) == "attach failed"
    else:
        raise AssertionError("Expected attach failure")

    assert sv.get_widget_count() == 1
    assert sv.get_focused_widget() is existing
    assert existing.parent() is not None


def test_successful_replacement_cleans_and_emits_only_for_displaced_widget(qtbot: QtBot) -> None:
    """A replacement finalizes the old widget only after the new widget starts."""
    split_view = _make_split_view(qtbot)
    existing = _LifecycleWidget(split_view)
    replacement = _LifecycleWidget(split_view)
    qtbot.addWidget(existing)
    qtbot.addWidget(replacement)
    removed: list[WorkspaceWidget] = []
    split_view.widget_removed.connect(removed.append)

    split_view.replace_or_open(existing)
    split_view.replace_or_open(replacement)

    assert existing.cleanup_count == 1
    assert replacement.cleanup_count == 0
    assert replacement.attach_count == 1
    assert removed == [existing]


def test_failed_replacement_cleans_failure_without_removing_existing_widget(qtbot: QtBot) -> None:
    """Failed startup cleans the candidate and restores the existing pane without a removal event."""
    split_view = _make_split_view(qtbot)
    existing = _LifecycleWidget(split_view)
    failing = _LifecycleWidget(split_view, fail_attach=True)
    qtbot.addWidget(existing)
    qtbot.addWidget(failing)
    removed: list[WorkspaceWidget] = []
    split_view.widget_removed.connect(removed.append)
    split_view.replace_or_open(existing)

    try:
        split_view.replace_or_open(failing)
    except RuntimeError as exc:
        assert str(exc) == "attach failed"
    else:
        raise AssertionError("Expected attach failure")

    assert existing.cleanup_count == 0
    assert failing.cleanup_count == 1
    assert split_view.get_focused_widget() is existing
    assert removed == []


def test_failed_first_attachment_cleans_candidate_and_restores_empty_workspace(qtbot: QtBot) -> None:
    """Failed first startup leaves the split view empty and ready for another widget."""
    split_view = _make_split_view(qtbot)
    failing = _LifecycleWidget(split_view, fail_attach=True)
    qtbot.addWidget(failing)

    try:
        split_view.replace_or_open(failing)
    except RuntimeError as exc:
        assert str(exc) == "attach failed"
    else:
        raise AssertionError("Expected attach failure")

    assert failing.cleanup_count == 1
    assert split_view.get_widget_count() == 0
    assert split_view.get_focused_widget() is None
    assert split_view._welcome_widget.isVisible()


def test_moving_widget_between_leaves_does_not_restart_or_clean_it(qtbot: QtBot) -> None:
    """Rearranging an attached pane preserves its running widget lifecycle."""
    split_view = _make_split_view(qtbot)
    first = _LifecycleWidget(split_view)
    second = _LifecycleWidget(split_view)
    qtbot.addWidget(first)
    qtbot.addWidget(second)
    split_view.add_workspace_widget(first)
    split_view.add_workspace_widget(second)
    target_leaf = split_view._leaf_for_widget(second)
    assert target_leaf is not None
    first_id = split_view._id_for_widget(first)
    assert first_id is not None

    split_view._handle_drop(target_leaf, first_id, SplitDirection.LEFT)

    assert first.attach_count == 1
    assert first.cleanup_count == 0


def test_welcome_visible_when_empty(qtbot: QtBot) -> None:
    """Welcome widget is visible when empty, hidden when widgets exist."""
    sv = _make_split_view(qtbot)
    assert sv._welcome_widget.isVisible()

    w1 = _make_dummy(qtbot)
    sv.add_workspace_widget(w1)
    assert not sv._welcome_widget.isVisible()

    sv.remove_workspace_widget(w1)
    assert sv._welcome_widget.isVisible()


def test_parent_destruction_does_not_reenter_split_view_layout(qtbot: QtBot) -> None:
    """Destroying the pane host must not treat child destruction as an external removal."""
    sv = SplitView()
    sv.show()
    sv.add_workspace_widget(_DummyWidget())

    sv.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def test_welcome_hints_from_shortcut_manager(qtbot: QtBot) -> None:
    """Welcome widget resolves grouped hints from ShortcutManager definitions."""
    sv = _make_split_view(qtbot)
    sm = ShortcutManager()
    sm.register_defaults()
    sm.install(sv)

    sv.set_welcome_shortcut_manager(sm)
    groups = sv._welcome_widget._resolve_grouped_hints()

    assert "Open" in groups
    assert "Configure" in groups
    open_labels = [h.label for h in groups["Open"]]
    assert "Add Video" in open_labels
    assert "Add Live Stream" in open_labels
    assert "Add Playlist" in open_labels
    config_labels = [h.label for h in groups["Configure"]]
    assert "Keyboard Shortcuts" in config_labels
    assert "Settings" in config_labels


def test_welcome_hints_empty_without_manager(qtbot: QtBot) -> None:
    """Welcome widget returns no hints when no ShortcutManager is set."""
    sv = _make_split_view(qtbot)
    groups = sv._welcome_widget._resolve_grouped_hints()
    assert len(groups) == 0


def test_welcome_hints_reflect_rebinding(qtbot: QtBot) -> None:
    """Welcome widget reflects rebound shortcuts."""
    from PySide6.QtGui import QKeySequence

    sv = _make_split_view(qtbot)
    sm = ShortcutManager()
    sm.register_defaults()
    sm.install(sv)

    sv.set_welcome_shortcut_manager(sm)
    sm.set_binding("app.add_video", QKeySequence("Ctrl+Shift+O"))

    groups = sv._welcome_widget._resolve_grouped_hints()
    video_hint = next(h for h in groups["Open"] if h.label == "Add Video")
    assert video_hint.keys == "Ctrl+Shift+O"


def test_open_to_side_splits_beside_focused_pane_and_keeps_both(qtbot: QtBot) -> None:
    """open_to_side adds a pinned pane next to the focused one; the next open replaces only the preview."""
    sv = _make_split_view(qtbot)
    preview = _make_dummy(qtbot)
    sv.replace_or_open(preview)

    side = _make_dummy(qtbot)
    sv.open_to_side(side)

    assert sv.get_widget_count() == 2
    assert side.is_pinned() and not preview.is_pinned()
    assert [leaf.get_widget() for leaf in sv._list_leaves()] == [preview, side]

    replacement = _make_dummy(qtbot)
    sv.replace_or_open(replacement)

    assert [leaf.get_widget() for leaf in sv._list_leaves()] == [replacement, side]


def test_open_to_side_on_empty_view_adds_widget(qtbot: QtBot) -> None:
    """open_to_side on an empty SplitView opens the widget in the only pane."""
    sv = _make_split_view(qtbot)
    widget = _make_dummy(qtbot)

    sv.open_to_side(widget)

    assert sv.get_widget_count() == 1
    assert sv.get_focused_widget() is widget


def test_header_shows_preview_state_and_pin_toggles_it(qtbot: QtBot) -> None:
    """Unpinned panes show an italic preview title; the pin button pins and restores the regular title."""
    widget = _make_dummy(qtbot)
    widget.show()
    title = widget._title_label

    assert title.font().italic()
    assert widget._pin_button.width() >= 24 and widget._close_button.width() >= 24
    assert not widget._pin_button.icon().isNull() and not widget._close_button.icon().isNull()

    widget._pin_button.click()

    assert widget.is_pinned()
    assert not title.font().italic()

    widget.set_pinned(False)

    assert not widget._pin_button.isChecked()
    assert title.font().italic()
