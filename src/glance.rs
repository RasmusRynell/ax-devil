//! What the sidebar learns of a camera by asking it: a snapshot for a hover card, and whether the login saved with a
//! stream gets in. Both are the user's to start (a hover, a start or a rescan), answered from the last answer within a
//! cooldown so a pointer or a poll cannot hammer a camera, and go to hosts the routes have already vouched for.

use crate::{network::row, throttled::Throttled};
use api_types::live::NetworkRow;
use bytes::Bytes;
use camera::{Camera, Device, Head, Login, Web};
use std::{net::Ipv4Addr, time::Duration};

/// How long a camera may take to answer a snapshot: hovering must not leave a request hanging.
pub const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(3);
/// How long an answer, or a failure, is kept before the camera is asked again.
const COOLDOWN: Duration = Duration::from_secs(10);

/// The snapshots and login checks the app has asked for, by key.
pub struct Glances {
    snapshots: Throttled<Bytes>,
    checks: Throttled<NetworkRow>,
}

impl Default for Glances {
    fn default() -> Self {
        Self {
            snapshots: Throttled::new(COOLDOWN),
            checks: Throttled::new(COOLDOWN),
        }
    }
}

impl Glances {
    /// The JPEG of `head` of `camera` (which carries its login and its timeout), kept under `key`. Digest only, so a
    /// wrong login is refused at once instead of being retried with Basic. `None` when the camera does not give one.
    pub async fn snapshot(&self, key: &str, camera: Camera, head: Head) -> Option<Bytes> {
        self.snapshots
            .get(key, move || async move {
                let device = Device::new(&camera).ok()?.digest_only();
                match device.snapshot(head).await {
                    Ok(jpeg) => Some(jpeg),
                    Err(error) => {
                        tracing::debug!("No snapshot: {error}");
                        None
                    }
                }
            })
            .await
    }

    /// What signing in to the camera at `addr` on `web` with `login` finds, kept under `key`; `None` when no Axis
    /// camera answered. Nothing is sent to a host that does not answer as a camera, and only as Digest.
    pub async fn check(
        &self,
        key: &str,
        addr: Ipv4Addr,
        web: Web,
        login: Login,
    ) -> Option<NetworkRow> {
        self.checks
            .get(key, move || async move {
                camera::check(addr, web, login)
                    .await
                    .map(|found| row(&found))
            })
            .await
    }
}
