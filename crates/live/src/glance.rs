//! What the sidebar learns of a camera by asking it: a snapshot for a hover card, and whether the login saved with a
//! stream gets in. Both are the user's to start (a hover, a start or a rescan), answered from the last answer within a
//! cooldown so a pointer or a poll cannot hammer a camera, and go to hosts the routes have already seen a viewer open for.

use crate::{network::row, throttled::Throttled};
use api_types::live::NetworkRow;
use bytes::Bytes;
use camera::{Camera, Device, Head, Login, Web};
use playback::SharedFrame;
use std::{
    collections::hash_map::DefaultHasher,
    hash::{Hash, Hasher},
    net::Ipv4Addr,
    sync::Arc,
    time::Duration,
};
use tokio::sync::Semaphore;

/// How long a camera may take to answer a snapshot: hovering must not leave a request hanging.
pub const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(3);
/// How long an answer, or a failure, is kept before the camera is asked again.
const COOLDOWN: Duration = Duration::from_secs(10);

/// The longest side of a hover picture, in pixels.
const PICTURE_EDGE: u32 = 560;

/// What a snapshot is the same read for: the camera, its login and the head. The password is hashed in, so the key
/// tells logins apart without holding it.
fn snapshot_key(camera: &Camera, head: Head) -> String {
    let mut hasher = DefaultHasher::new();
    let login = camera
        .login
        .as_ref()
        .map(|login| (login.user.as_str(), login.password.expose()));
    (format!("{:?}", (&camera.host, &camera.web, head)), login).hash(&mut hasher);
    format!("camera/{:016x}", hasher.finish())
}

/// How many pictures are encoded at once. A frame asked for while all are busy has no picture; the next hover refresh
/// asks again, with a newer frame.
const ENCODING: usize = 2;

/// The JPEG of `frame` shrunk to the hover card's size; `None` when it cannot be encoded.
fn encode(frame: &SharedFrame) -> Option<Bytes> {
    match media::frame_jpeg(frame, PICTURE_EDGE) {
        Ok(jpeg) => Some(Bytes::from(jpeg.bytes)),
        Err(error) => {
            tracing::debug!("No picture: {error}");
            None
        }
    }
}

/// The snapshots and login checks the app has asked for, by key, and the pictures of open streams.
pub struct Glances {
    snapshots: Throttled<Bytes>,
    checks: Throttled<NetworkRow>,
    /// Admission to the encoder: a permit lasts as long as the encode, even when the one who asked has left.
    encoding: Arc<Semaphore>,
}

impl Default for Glances {
    fn default() -> Self {
        Self {
            snapshots: Throttled::new(COOLDOWN),
            checks: Throttled::new(COOLDOWN),
            encoding: Arc::new(Semaphore::new(ENCODING)),
        }
    }
}

impl Glances {
    /// The JPEG of a picture an open viewer holds, for the hover card. A frame is encoded once, however many ask while
    /// it is the newest; the work is on the blocking pool and at most `ENCODING` at a time. `None` when it cannot be
    /// encoded, or the encoder is busy: busy is not remembered, so a frame that stays the newest (a paused stream) is
    /// encoded when asked for again.
    pub async fn picture(&self, frame: &SharedFrame) -> Option<Bytes> {
        let (permits, shared) = (Arc::clone(&self.encoding), frame.clone());
        frame
            .preview(async move {
                let permit = permits.try_acquire_owned().ok()?;
                tokio::task::spawn_blocking(move || {
                    let _permit = permit;
                    encode(&shared)
                })
                .await
                .ok()
            })
            .await
    }

    /// Takes every encoding permit until the result is dropped, so a test can make the encoder busy.
    #[cfg(test)]
    pub(crate) fn hold_encoders(&self) -> tokio::sync::OwnedSemaphorePermit {
        let all = u32::try_from(ENCODING).expect("a small count");
        Arc::clone(&self.encoding)
            .try_acquire_many_owned(all)
            .expect("the encoder is idle")
    }

    /// The JPEG of `head` of `camera` (which carries its login and its timeout). Streams of one camera, login and head
    /// share one read and one cooldown. Digest only, so a wrong login is refused at once instead of being retried with
    /// Basic. `None` when the camera does not give one.
    pub async fn snapshot(&self, camera: Camera, head: Head) -> Option<Bytes> {
        self.snapshots
            .get(&snapshot_key(&camera, head), move || async move {
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
