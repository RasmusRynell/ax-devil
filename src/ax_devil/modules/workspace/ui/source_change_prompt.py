"""The offer to reload a viewer whose source files changed.

A thin layer over ``ViewerWidget.reload``: it only asks, and holds no reload logic, so removing it leaves reloading to
whoever calls ``reload`` directly.
"""

from __future__ import annotations

from collections.abc import Callable

from ax_devil.modules.chrome.banner import Banner
from ax_devil.modules.workspace.ui.viewer_widget import ViewerWidget

_OFFER = "sourceChangeOffer"


def offer_reload(widget: ViewerWidget, reload: Callable[[], None]) -> None:
    """Ask in *widget*'s banner whether to reload it, and call *reload* if the user agrees.

    An offer already waiting stays as it is, so changes arriving one after another never stack; *reload* is called
    only when the user clicks, so it should take the newest data then. Dismissing removes the offer until the next
    change.
    """
    current = widget.banner()
    if current is not None and current.property(_OFFER):
        return
    banner = Banner("Source changed, reload current viewer?", "Reload", dismiss_text="Dismiss")
    banner.setProperty(_OFFER, True)

    def reload_now() -> None:
        widget.set_banner(None)
        reload()

    banner.action.clicked.connect(reload_now)
    if banner.dismiss is not None:
        banner.dismiss.clicked.connect(lambda: widget.set_banner(None))
    widget.set_banner(banner)
