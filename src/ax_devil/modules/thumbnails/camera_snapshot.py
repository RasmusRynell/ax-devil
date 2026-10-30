"""One snapshot from a camera, for the hover card of a stream that is not playing."""

from __future__ import annotations

from PySide6.QtGui import QImage

from ax_devil.core.cancellation import CancelToken
from ax_devil.core.device_connection import DeviceConnection
from ax_devil.modules.thumbnails.scaling import THUMBNAIL_EDGE_PX, fit

SNAPSHOT_TIMEOUT_S = 3.0
"""How long the camera may take; hovering must not leave a request hanging."""


def read_snapshot(connection: DeviceConnection, camera_head: int, token: CancelToken) -> QImage:
    """Return a JPEG snapshot of camera head *camera_head*, fitted to the thumbnail size; raise when it fails.

    HTTP Digest is forced, so a wrong login is refused at once instead of being retried with Basic.
    """
    from ax_devil_device_api import Client  # Imported here so importing the package stays light.
    from ax_devil_device_api.core.config import AuthMethod

    token.raise_if_cancelled()
    config = connection.device_config()
    config.auth_method = AuthMethod.DIGEST
    config.timeout = SNAPSHOT_TIMEOUT_S
    with Client(config) as client:
        data = client.media.get_snapshot(camera_head=camera_head)
    token.raise_if_cancelled()
    image = QImage.fromData(data)
    if image.isNull():
        raise ValueError("The camera's snapshot is not a JPEG picture.")
    return fit(image, THUMBNAIL_EDGE_PX)
