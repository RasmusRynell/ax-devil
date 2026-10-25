"""The Live view with its scanner, login checker and resolver doubles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton
from pytestqt.qtbot import QtBot

from ax_devil.core.cancellation import Cancelled, CancelToken
from ax_devil.core.device_connection import DeviceConnection, DeviceProtocol
from ax_devil.modules.background_jobs import BackgroundJobs
from ax_devil.modules.chrome.menu_button import MenuButton
from ax_devil.modules.device_discovery.network_scan import DiscoveredCamera, Login
from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.workspace.core import ItemResolution, Workspace, WorkspaceItem
from ax_devil.modules.workspace.ui.item_resolver import ItemResolver
from ax_devil.modules.workspace.ui.live_view import LiveView
from ax_devil.modules.workspace.ui.live_view_controller import LiveViewController
from ax_devil.modules.workspace.ui.plugin_intake import default_resolution_context
from ax_devil.modules.workspace.ui.split_view import SplitView
from ax_devil.modules.workspace.ui.workspace_store import WorkspaceStore
from tests.helpers.section_tree import press, row, row_action, row_texts, shown_rows, texts

LIVE_VIEWER = "ax_devil.modules.video_viewer.live_video_viewer.LiveVideoViewerWidget"


class HeldResolver(ItemResolver):
    """Runs work at once, or, while *hold* is set, keeps it until ``finish`` as work still under way would be."""

    def __init__(self) -> None:
        super().__init__(default_resolution_context(), in_background=False)
        self.hold = False
        self._held: list[tuple[WorkspaceItem, Callable[[ItemResolution], None], CancelToken]] = []

    def resolve(self, item: WorkspaceItem, done: Callable[[ItemResolution], None]) -> Callable[[], None]:
        """Resolve immediately unless this test is holding item delivery."""
        if not self.hold:
            return super().resolve(item, done)
        token = CancelToken()
        self._held.append((item, done, token))
        return token.cancel

    def finish(self) -> None:
        """Finish held items unless their owner cancelled them."""
        held, self._held = self._held, []
        for item, done, token in held:
            try:
                done(ItemResolution.of(item, self.context, token))
            except Cancelled:
                pass


@dataclass
class Scanner:
    """A network scan that reports *cameras* one by one, or fails with *error*, and records how it was called; it
    waits while *gate* is clear, as a scan still under way would.

    *on_found* runs after each report on the scan's worker thread, so it must not touch widgets.
    """

    cameras: list[DiscoveredCamera] = field(default_factory=list)
    error: Exception | None = None
    calls: int = 0
    targets: tuple[str, ...] = ()
    logins: tuple[Login, ...] = ()
    low_impact: bool = False
    prefer: DeviceProtocol = DeviceProtocol.HTTPS
    on_found: Callable[[], None] = lambda: None
    gate: Event = field(default_factory=Event)
    token: CancelToken | None = None

    def __post_init__(self) -> None:
        self.gate.set()

    def __call__(
        self,
        targets: tuple[str, ...],
        logins: tuple[Login, ...],
        found: Callable[[DiscoveredCamera], None],
        token: CancelToken,
        low_impact: bool = False,
        prefer: DeviceProtocol = DeviceProtocol.HTTPS,
    ) -> list[DiscoveredCamera]:
        self.calls += 1
        self.token = token
        self.targets = targets
        self.logins = logins
        self.low_impact = low_impact
        self.prefer = prefer
        assert self.gate.wait(5)
        if self.error is not None:
            raise self.error
        for camera in self.cameras:
            found(camera)
            self.on_found()
        return self.cameras


@dataclass
class Checker:
    """Signing in to a workspace camera with its own login, answering from *cameras* by host, and recording the calls;
    a host not listed does not answer; it waits while *gate* is clear."""

    cameras: dict[str, DiscoveredCamera] = field(default_factory=dict)
    calls: list[tuple[DeviceConnection, Login]] = field(default_factory=list)
    gate: Event = field(default_factory=Event)

    def __post_init__(self) -> None:
        self.gate.set()

    def __call__(self, connection: DeviceConnection, login: Login, token: CancelToken) -> DiscoveredCamera | None:
        self.calls.append((connection, login))
        assert self.gate.wait(5)
        return self.cameras.get(connection.host)


@dataclass
class Live:
    qtbot: QtBot
    view: LiveView
    store: WorkspaceStore
    center: SplitView
    scanner: Scanner
    jobs: BackgroundJobs
    resolutions: HeldResolver
    controller: LiveViewController
    checker: Checker
    add_stream_requests: list[None]
    """One entry for each time the Add stream button asked to add a camera."""

    def wait_for_scan(self) -> None:
        """Wait until the scan under way has finished and the view shows its outcome."""
        self.qtbot.waitUntil(lambda: self.button("Rescan").isEnabled())

    def open_workspace(self, *items: WorkspaceItem) -> None:
        """Make *items* the workspace, opening nothing, as restoring one does."""
        self.store.replace_workspace(Workspace(items=items))

    def rows(self) -> dict[str, str]:
        """Return each listed row's title and its action, empty for none, in order."""
        return {row_texts(row)[0]: row_action(row) for row in shown_rows(self.view.tree)}

    def detail(self, title: str) -> str:
        """Return the detail shown under the row named *title*, with its unbreakable spaces plain."""
        return row(self.view.tree, title).row.detail.replace("\u00a0", " ")

    def press(self, title: str) -> None:
        """Press the action of the row titled *title*."""
        press(self.view.tree, title)

    def button(self, text: str) -> QPushButton:
        """Return the button labelled *text*, leaving out one the tree replaced and hid but has not deleted yet."""
        [button] = [
            button
            for button in self.view.findChildren(QPushButton)
            if button.text() == text and button.isVisibleTo(self.view)
        ]
        return button

    def ip_range(self) -> QLineEdit:
        return self._field("IP range")

    def _field(self, placeholder: str) -> QLineEdit:
        [found] = [edit for edit in self.view.findChildren(QLineEdit) if edit.placeholderText().startswith(placeholder)]
        return found

    def search(self, text: str) -> None:
        self._field("Search").setText(text)

    def choose(self, button: str, entry: str) -> None:
        """Pick *entry* in the menu of the menu button *button*."""
        [menu_button] = [found for found in self.view.findChildren(MenuButton) if found.text() == button]
        menu = menu_button.menu()
        assert menu is not None
        [action] = [action for action in menu.actions() if action.text() == entry]
        action.trigger()

    def sign_in_choice(self) -> QCheckBox:
        [choice] = self.view.findChildren(QCheckBox)
        return choice

    def save_logins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *logins: Login) -> None:
        """Make *logins* the ones Settings gives the scan, as saving Settings does."""
        monkeypatch.setattr(ConfigManager, "_instance", None)
        config = ConfigManager()
        config.set_config_path(tmp_path / "config.json")
        config.set("storage", {key: str(tmp_path / key) for key in config.get_raw("storage")})
        defaults = config.get_raw("defaults")
        defaults["network_scan"]["credentials"] = Login.config_value(logins)
        config.set("defaults", defaults)
        self.controller.refresh_defaults()

    def texts(self) -> list[str]:
        return texts(self.view)


def scan(live: Live, *cameras: DiscoveredCamera) -> None:
    live.scanner.cameras = list(cameras)
    live.button("Rescan").click()
    live.wait_for_scan()
