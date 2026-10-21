import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ax_devil.modules.video_player.engine.quick.surface import QuickSurface
from ax_devil.modules.workspace.ui.split_panes import SplitDirection
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget
from ax_devil.modules.workspace.ui.welcome_widget import WelcomeItem
from tests.drawing_helpers import PolylineCall, prepare_calls
from tests.helpers.shortcuts import make_shortcut_manager
from tests.helpers.workspace import PlainViewerWidget


class _AttachAwareWidget(PlainViewerWidget):
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


class _FailingAttachWidget(PlainViewerWidget):
    """Workspace widget that fails during post-attach startup."""

    def on_workspace_attached(self) -> None:
        """Raise a startup error."""
        raise RuntimeError("attach failed")


class _LifecycleWidget(_AttachAwareWidget):
    """Viewer widget that records attachment and cleanup calls."""

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


def _panes(split_view: SplitView) -> list[ViewerWidget]:
    """Return the open panes from left to right as the user sees them."""
    QCoreApplication.processEvents()
    widgets = [widget for widget in split_view.findChildren(ViewerWidget) if widget.isVisibleTo(split_view)]
    return sorted(widgets, key=lambda widget: widget.mapTo(split_view, QPoint(0, 0)).x())


def _make_split_view(qtbot: QtBot) -> SplitView:
    sv = SplitView()
    qtbot.addWidget(sv)
    sv.show()
    return sv


def _make_dummy(qtbot: QtBot) -> PlainViewerWidget:
    w = PlainViewerWidget()
    qtbot.addWidget(w)
    return w


def test_all_pinned_creates_new_split(qtbot: QtBot) -> None:
    """replace_or_open creates a new split when all existing widgets are pinned."""
    sv = _make_split_view(qtbot)
    pinned = [_make_dummy(qtbot), _make_dummy(qtbot)]
    for widget in pinned:
        sv.add_viewer_widget(widget)
        widget.set_pinned(True)

    newcomer = _make_dummy(qtbot)
    sv.replace_or_open(newcomer)

    assert _panes(sv) == [*pinned, newcomer]


def test_open_replaces_the_active_unpinned_widget(qtbot: QtBot) -> None:
    """An earlier preview survives when the user activates another preview to replace."""
    sv = _make_split_view(qtbot)
    earlier, active, replacement = (_LifecycleWidget(sv) for _ in range(3))
    for widget in (earlier, active, replacement):
        qtbot.addWidget(widget)
    sv.add_viewer_widget(earlier)
    sv.add_viewer_widget(active)
    QTest.mouseClick(earlier.get_header_widget(), Qt.MouseButton.LeftButton)
    QTest.mouseClick(active.get_header_widget(), Qt.MouseButton.LeftButton)

    sv.replace_or_open(replacement)

    assert sv.get_focused_widget() is replacement
    assert _panes(sv) == [earlier, replacement]
    assert (earlier.cleanup_count, active.cleanup_count, replacement.cleanup_count) == (0, 1, 0)
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
    removed: list[ViewerWidget] = []
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
    removed: list[ViewerWidget] = []
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
    split_view.add_viewer_widget(first)
    split_view.add_viewer_widget(second)
    target_leaf = split_view._leaf_for_widget(second)
    assert target_leaf is not None
    first_id = split_view.id_for_widget(first)
    assert first_id is not None

    split_view.handle_drop(target_leaf, first_id, SplitDirection.LEFT)

    assert first.attach_count == 1
    assert first.cleanup_count == 0


def test_welcome_visible_when_empty(qtbot: QtBot) -> None:
    """Welcome widget is visible when empty, hidden when widgets exist."""
    sv = _make_split_view(qtbot)
    assert sv.welcome_widget().isVisible()

    w1 = _make_dummy(qtbot)
    sv.add_viewer_widget(w1)
    assert not sv.welcome_widget().isVisible()

    sv.remove_viewer_widget(w1)
    assert sv.welcome_widget().isVisible()


def test_parent_destruction_does_not_reenter_split_view_layout(qtbot: QtBot) -> None:
    """Destroying the pane host must not treat child destruction as an external removal."""
    sv = SplitView()
    sv.show()
    sv.add_viewer_widget(PlainViewerWidget())

    sv.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()

    # The regression is a crash or exception raised while the host is destroyed; reaching here without one passes.
    assert not isValid(sv)


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
    """Opening while the side pane is active preserves the earlier preview and opens beside the pinned pane."""
    sv = _make_split_view(qtbot)
    preview = _make_dummy(qtbot)
    sv.replace_or_open(preview)
    sv.activateWindow()
    assert QTest.qWaitForWindowActive(sv)
    preview.findChildren(QAbstractButton)[0].setFocus()

    side = _make_dummy(qtbot)
    sv.open_to_side(side)

    assert side.is_pinned() and not preview.is_pinned()
    assert _panes(sv) == [preview, side]
    assert sv.get_focused_widget() is side
    assert side.hasFocus()

    replacement = _make_dummy(qtbot)
    sv.replace_or_open(replacement)

    assert _panes(sv) == [preview, side, replacement]
    assert not replacement.is_pinned()


def test_open_to_side_on_empty_view_adds_widget(qtbot: QtBot) -> None:
    """open_to_side on an empty SplitView opens the widget in the only pane."""
    sv = _make_split_view(qtbot)
    widget = _make_dummy(qtbot)

    sv.open_to_side(widget)

    assert sv.get_widget_count() == 1
    assert sv.get_focused_widget() is widget


def test_removing_active_pane_selects_a_survivor_and_ignores_removed_activation(qtbot: QtBot) -> None:
    """Closing the active pane leaves a usable target; late events cannot reactivate the removed viewer."""
    sv = _make_split_view(qtbot)
    first, second, replacement = (_make_dummy(qtbot) for _ in range(3))
    sv.add_viewer_widget(first)
    sv.add_viewer_widget(second)
    sv.remove_viewer_widget(second)
    sv.focus_widget(second)
    sv.replace_or_open(replacement)
    assert _panes(sv) == [replacement]


@pytest.mark.parametrize("fail", [False, True])
def test_reloading_an_inactive_pane_preserves_keyboard_target(qtbot: QtBot, fail: bool) -> None:
    """Refreshing another pane must keep keyboard input and ordinary Open on the user's active pane."""
    sv = _make_split_view(qtbot)
    old, active = (_make_dummy(qtbot) for _ in range(2))
    sv.add_viewer_widget(old)
    sv.add_viewer_widget(active)
    sv.activateWindow()
    assert QTest.qWaitForWindowActive(sv)
    button = active.findChildren(QAbstractButton)[0]
    button.setFocus()
    QCoreApplication.processEvents()
    assert button.hasFocus()
    assert sv.get_focused_widget() is active
    candidate = _LifecycleWidget(fail_attach=fail)

    if fail:
        with pytest.raises(RuntimeError, match="attach failed"):
            sv.replace_viewer_widget(old, candidate)
        assert not candidate.isWindow(), "A failed pane must not become a separate application window."
    else:
        sv.replace_viewer_widget(old, candidate)
    QCoreApplication.processEvents()
    assert button.hasFocus()
    assert sv.get_focused_widget() is active


def test_no_active_pane_opens_without_replacing_any_existing_viewer(qtbot: QtBot) -> None:
    """A missing active target must never select an unrelated preview for replacement."""
    sv = _make_split_view(qtbot)
    existing, newcomer = (_make_dummy(qtbot) for _ in range(2))
    sv.add_viewer_widget(existing)
    sv._set_focused_widget(None)
    sv.replace_or_open(newcomer)
    assert _panes(sv) == [existing, newcomer]


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


class _SurfaceWidget(PlainViewerWidget):
    """Viewer widget hosting a video surface, as a playing viewer does."""

    def _setup_widget_ui(self) -> None:
        self.surface = QuickSurface()
        self.get_content_layout().addWidget(self.surface)


def _present_polyline(surface: QuickSurface) -> None:
    target = QRectF(0, 0, 200, 100)
    drawing = prepare_calls([PolylineCall(((0.1, 0.1), (0.9, 0.5), (0.3, 0.9)))], surface.drawing_buffer(target, 100))
    surface.set_overlays(drawing, 1, target, 100, None)
    QCoreApplication.processEvents()


def test_collapsing_a_split_keeps_the_moved_pane_overlays_usable(qtbot: QtBot) -> None:
    """Dragging a pane so its split collapses must not break overlay drawing in the pane that moved up."""
    sv = _make_split_view(qtbot)
    sv.resize(900, 600)
    left = _make_dummy(qtbot)
    hosting = _SurfaceWidget()
    dragged = _make_dummy(qtbot)
    qtbot.addWidget(hosting)
    for widget in (left, hosting, dragged):
        sv.add_viewer_widget(widget)
    QCoreApplication.processEvents()  # Let the layout give the surface a size.
    _present_polyline(hosting.surface)
    assert hosting.surface._overlay._drawing.vertices.size
    left_leaf = sv._leaf_for_widget(left)
    dragged_id = sv.id_for_widget(dragged)
    assert left_leaf is not None and dragged_id is not None

    sv.handle_drop(left_leaf, dragged_id, SplitDirection.TOP)

    _present_polyline(hosting.surface)
    assert isValid(hosting.surface._overlay)
    assert hosting.surface._overlay._drawing.vertices.size
