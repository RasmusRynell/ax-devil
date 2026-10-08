"""Exercise installed resources and desktop startup without importing the source checkout."""

from __future__ import annotations

import faulthandler
import importlib.metadata
import sys
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

import ax_devil
from ax_devil.app import create_app
from ax_devil.modules.chrome.icons import Icon
from ax_devil.modules.plugin_system import ApplicationPluginLoader, get_payload_decoder
from ax_devil.modules.plugin_system.validate import validate_plugins
from ax_devil.modules.settings.paths import DEFAULT_CONFIG_PATH


def main() -> None:
    """Validate a wheel install and close its real main window after entering the event loop."""
    expected_version, plugin_version = sys.argv[1:]
    assert importlib.metadata.version("ax-devil") == expected_version
    assert Path(ax_devil.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    ApplicationPluginLoader.load_all()
    assert get_payload_decoder("ONVIF_XML") is not None
    if plugin_version != "none":
        assert importlib.metadata.version("ax-devil-smoke-plugin") == plugin_version
        validate_plugins(["ax-devil-smoke-plugin"])
        assert get_payload_decoder("EXAMPLE_FRAME").decode({"frame": 42}).time_slice.start == 42
    else:
        try:
            importlib.metadata.distribution("ax-devil-smoke-plugin")
        except importlib.metadata.PackageNotFoundError:
            pass
        else:
            raise AssertionError("Plugin leaked into the base environment")

    application = create_app()
    original_exec = QApplication.exec
    entered = False

    def checked_exec() -> int:
        from PySide6.QtCore import QTimer

        nonlocal entered
        entered = True
        window = application.main_window
        assert window is not None and window.isVisible()
        assert files("ax_devil.resources").joinpath("icons").joinpath("LICENSE").is_file()
        for icon in Icon:
            image = icon.icon().pixmap(16, 16).toImage()
            assert any(image.pixelColor(x, y).alpha() for y in range(16) for x in range(16)), icon.name

        QTimer.singleShot(0, window.close)
        return int(original_exec())

    with patch.object(QApplication, "exec", staticmethod(checked_exec)):
        assert application.run() == 0
    assert entered
    assert DEFAULT_CONFIG_PATH.is_file()
    assert DEFAULT_CONFIG_PATH.is_relative_to(Path.home())


if __name__ == "__main__":
    faulthandler.dump_traceback_later(15, exit=True)
    try:
        main()
    except Exception:
        # The app installs a GUI exception hook; failed probes must report to pytest.
        sys.__excepthook__(*sys.exc_info())
        sys.exit(1)
    finally:
        faulthandler.cancel_dump_traceback_later()
