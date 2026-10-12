"""What a view of the activity bar is: the contract between a view's owner and the window that hosts it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import SignalInstance
from PySide6.QtWidgets import QWidget

from ax_devil.modules.chrome.icons import Icon


class ViewGroup(Enum):
    """Where a view's button sits on the activity bar. Groups run top to bottom, set apart by a rule.

    Views of the workspace come first; ``APP`` views are about the application rather than the workspace, so they sit
    at the bottom, above the app-wide actions.
    """

    WORKSPACE = "workspace"
    """Everything the workspace holds."""
    SOURCES = "sources"
    """One kind of what it holds, with a way to add more of it."""
    REVIEW = "review"
    """Notes made about what it holds."""
    APP = "app"
    """The application itself."""

    @property
    def at_bottom(self) -> bool:
        """Return whether this group's buttons sit at the bottom of the bar."""
        return self is ViewGroup.APP


@dataclass(frozen=True)
class SidebarView:
    """A view the sidebar can show: what its button looks like, where it sits, and the widget it shows.

    The owner of the concept builds the widget; the window only hosts it. Views have a unique *view_id*. A view that
    can need attention gives a *warning* that returns what to show on its button, empty for none, and a
    *warning_changed* signal that fires when that changes.
    """

    view_id: str
    title: str
    icon: Icon
    group: ViewGroup
    widget: QWidget
    warning: Callable[[], str] = lambda: ""
    warning_changed: SignalInstance | None = None
