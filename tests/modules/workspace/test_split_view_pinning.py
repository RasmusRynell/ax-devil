import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractButton
from pytestqt.qtbot import QtBot

from ax_devil.modules.workspace.split_view import SplitDirection, SplitView
from ax_devil.modules.workspace.viewer_host import WorkspaceWidget
from ax_devil.modules.workspace.welcome_widget import WelcomeItem
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import PlainWorkspaceWidget


class _AttachAwareWidget(PlainWorkspaceWidget):
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
            self.welcome_visible_during_attach = self._split_view.welcome_widget().isVisible()


class _FailingAttachWidget(PlainWorkspaceWidget):
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


def _panes(split_view: SplitView) -> list[WorkspaceWidget]:
    """Return the open panes from left to right as the user sees them."""
    QCoreApplication.processEvents()
    widgets = [widget for widget in split_view.findChildren(WorkspaceWidget) if widget.isVisibleTo(split_view)]
    return sorted(widgets, key=lambda widget: widget.mapTo(split_view, QPoint(0, 0)).x())


def _make_split_view(qtbot: QtBot) -> SplitView:
    sv = SplitView()
    qtbot.addWidget(sv)
    sv.show()
    return sv


def _make_dummy(qtbot: QtBot) -> PlainWorkspaceWidget:
    w = PlainWorkspaceWidget()
    qtbot.addWidget(w)
    return w


def test_all_pinned_creates_new_split(qtbot: QtBot) -> None:
    """replace_or_open creates a new split when all existing widgets are pinned."""
    sv = _make_split_view(qtbot)
    pinned = [_make_dummy(qtbot), _make_dummy(qtbot)]
    for widget in pinned:
        sv.add_workspace_widget(widget)
        widget.set_pinned(True)

    newcomer = _make_dummy(qtbot)
    sv.replace_or_open(newcomer)

    assert _panes(sv) == [*pinned, newcomer]


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

    assert sv.get_focused_widget() is replacement
    assert _panes(sv) == [pinned, replacement]
    assert (pinned.cleanup_count, unpinned.cleanup_count, replacement.cleanup_count) == (0, 1, 0)
    assert replacement.attach_count == 1


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

    with pytest.raises(RuntimeError, match="attach failed"):
        sv.replace_or_open(failing)

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

    with pytest.raises(RuntimeError, match="attach failed"):
        split_view.replace_or_open(failing)

    assert existing.cleanup_count == 0
    assert failing.cleanup_count == 1
    assert split_view.get_focused_widget() is existing
    assert removed == []


def test_failed_first_attachment_cleans_candidate_and_restores_empty_workspace(qtbot: QtBot) -> None:
    """Failed first startup leaves the split view empty and ready for another widget."""
    split_view = _make_split_view(qtbot)
    failing = _LifecycleWidget(split_view, fail_attach=True)
    qtbot.addWidget(failing)

    with pytest.raises(RuntimeError, match="attach failed"):
        split_view.replace_or_open(failing)

    assert failing.cleanup_count == 1
    assert split_view.get_widget_count() == 0
    assert split_view.get_focused_widget() is None
    assert split_view.welcome_widget().isVisible()


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
    assert sv.welcome_widget().isVisible()

    w1 = _make_dummy(qtbot)
    sv.add_workspace_widget(w1)
    assert not sv.welcome_widget().isVisible()

    sv.remove_workspace_widget(w1)
    assert sv.welcome_widget().isVisible()


def test_parent_destruction_does_not_reenter_split_view_layout(qtbot: QtBot) -> None:
    """Destroying the pane host must not treat child destruction as an external removal."""
    sv = SplitView()
    sv.show()
    sv.add_workspace_widget(PlainWorkspaceWidget())

    sv.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


def _welcome_rows(split_view: SplitView) -> dict[str, WelcomeItem]:
    """Return the welcome rows a user can click, found through the widget's hit testing."""
    welcome = split_view.welcome_widget()
    rows: dict[str, WelcomeItem] = {}
    for y in range(welcome.height()):
        item = welcome.item_at(QPointF(welcome.width() / 2, y))
        if item is not None:
            rows[item.label] = item
    return rows


def test_welcome_rows_follow_shortcut_manager_bindings(qtbot: QtBot) -> None:
    """The welcome screen lists the main actions only with a shortcut manager, showing their current keys."""
    sv = _make_split_view(qtbot)
    sv.resize(900, 700)
    assert _welcome_rows(sv) == {}

    sm = make_shortcut_manager()
    sm.install(sv)
    sv.set_welcome_shortcut_manager(sm)
    sm.set_binding("app.add_video", QKeySequence("Ctrl+Shift+O"))

    rows = _welcome_rows(sv)
    assert {"Add Video", "Add Live Stream", "Add Playlist", "Keyboard Shortcuts", "Settings"} <= rows.keys()
    assert rows["Add Video"].keys == QKeySequence("Ctrl+Shift+O")


def test_open_to_side_splits_beside_focused_pane_and_keeps_both(qtbot: QtBot) -> None:
    """open_to_side adds a pinned pane next to the focused one; the next open replaces only the preview."""
    sv = _make_split_view(qtbot)
    preview = _make_dummy(qtbot)
    sv.replace_or_open(preview)

    side = _make_dummy(qtbot)
    sv.open_to_side(side)

    assert side.is_pinned() and not preview.is_pinned()
    assert _panes(sv) == [preview, side]

    replacement = _make_dummy(qtbot)
    sv.replace_or_open(replacement)

    assert _panes(sv) == [replacement, side]


def test_open_to_side_on_empty_view_adds_widget(qtbot: QtBot) -> None:
    """open_to_side on an empty SplitView opens the widget in the only pane."""
    sv = _make_split_view(qtbot)
    widget = _make_dummy(qtbot)

    sv.open_to_side(widget)

    assert sv.get_widget_count() == 1
    assert sv.get_focused_widget() is widget


def test_pin_button_and_pinned_state_stay_in_sync(qtbot: QtBot) -> None:
    """The header pin button pins the pane, and unpinning from code releases the button."""
    widget = _make_dummy(qtbot)
    widget.show()
    pin_button = next(button for button in widget.findChildren(QAbstractButton) if button.isCheckable())

    pin_button.click()
    assert widget.is_pinned()

    widget.set_pinned(False)
    assert not pin_button.isChecked()

    pin_button.click()
    assert widget.is_pinned()
