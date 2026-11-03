//! MQTT analytics from an Axis camera: a lease on the camera's publisher (`lease.rs`) and a subscription to its topic
//! on the broker (`session.rs`), kept up by one task that reconnects with backoff.

mod camera;
mod lease;
mod session;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::sync::oneshot::error::TryRecvError;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::time::sleep;
use tracing::warn;

use crate::device::Device;
use crate::error::{CameraError, ErrorKind};
use crate::live::{Counters, FeedOut, LiveMessages, Message, Reconnect, Status, Task};
use crate::target::{Broker, Camera};
use crate::vapix::SourceKey;
use lease::{Lease, Plan};

pub use camera::Publisher;

/// What every client id and topic this crate makes for a camera starts with.
const CLIENT_PREFIX: &str = "ax-devil-";

/// What the client ids of the Python `ax-devil-mqtt` this crate replaces start with. That client never restored the
/// camera, and its empty password reads back masked, so its leftovers would otherwise be refused as unrestorable.
const LEGACY_CLIENT_PREFIX: &str = "ax-devil/temp/";

/// How many messages may wait for the app.
const QUEUE: usize = 256;

/// The cameras (host and web port) that have a live subscription in this process. A camera has one MQTT client
/// configuration, so a second subscription would overwrite the first one's.
static LEASED: Mutex<Vec<(String, u16)>> = Mutex::new(Vec::new());

/// A camera's place in [`LEASED`]; dropping it frees the camera for the next subscription.
struct Claim(String, u16);

impl Claim {
    /// Takes `camera`, or fails with [`ErrorKind::Busy`] when a subscription already holds it.
    fn take(camera: &Camera) -> Result<Self, CameraError> {
        let key = (camera.host.to_string(), camera.web.port);
        let mut leased = LEASED.lock().unwrap_or_else(PoisonError::into_inner);
        if leased.contains(&key) {
            return Err(CameraError::new(
                ErrorKind::Busy,
                "another subscription in this process holds the camera's MQTT client",
            ));
        }
        leased.push(key.clone());
        Ok(Self(key.0, key.1))
    }
}

impl Drop for Claim {
    fn drop(&mut self) {
        let mut leased = LEASED.lock().unwrap_or_else(PoisonError::into_inner);
        leased.retain(|(host, port)| (host, port) != (&self.0, &self.1));
    }
}

/// Subscribes to the analytics `key` of `camera` through `broker` and returns at once; the work is done in the
/// background (a tokio runtime must be running). Watch [`LiveMessages::status`] for how it goes. A failure retrying
/// cannot fix ends in [`Status::Failed`].
///
/// The camera is made to publish the data source to the broker: its MQTT client is pointed at the broker and a
/// publisher is created unless a matching one exists. [`LiveMessages::close`], a terminal failure, and the end of the
/// task all remove what was created and restore the camera's MQTT client as it was. A camera has one MQTT client, so
/// while this process has a subscription to it, another `open_mqtt` for the same camera fails with
/// [`ErrorKind::Busy`] and leaves the camera alone.
pub fn open_mqtt(
    camera: &Camera,
    broker: &Broker,
    key: SourceKey,
    reconnect: Reconnect,
) -> LiveMessages {
    let (messages_tx, messages) = mpsc::channel(QUEUE);
    let (status_tx, status) = watch::channel(Status::Connecting);
    let (stop_tx, stop) = oneshot::channel();
    let counters = Arc::new(Counters::default());
    let name = format!(
        "{CLIENT_PREFIX}{:016x}",
        fingerprint(&format!("{}:{}:{}", key.0, camera.host, camera.web.port))
    );
    let plan = Plan {
        broker: broker.clone(),
        client_id: name.clone(),
        publisher: Publisher {
            id: String::new(),
            source: key,
            topic: format!("ax-devil/temp/{name}"),
            qos: 0,
            retain: false,
            use_topic_prefix: false,
        },
    };
    let topic = plan.publisher.topic.clone();
    // A camera that cannot be addressed, or is held already, fails the first attempt like any other terminal failure.
    let (claim, lease) = match Claim::take(camera) {
        Ok(claim) => (
            Some(claim),
            Device::new(camera).map(|device| Lease::new(device, plan)),
        ),
        Err(error) => (None, Err(error)),
    };
    let link = Link {
        topic,
        reader_id: unique(&format!("{name}-reader")),
        broker: broker.clone(),
        timeout: camera.timeout,
        reconnect,
        claim,
        lease,
        status: status_tx,
        out: FeedOut::new(messages_tx),
        counters: Arc::clone(&counters),
        epoch: None,
        attempt: 0,
    };
    let handle = tokio::spawn(link.run(stop));
    LiveMessages::new(messages, status, Task::new(handle, stop_tx, counters))
}

/// Everything the subscription task owns.
struct Link {
    topic: String,
    /// The subscriber's client id; unique per subscription, because a broker drops the older of two connections
    /// that share one.
    reader_id: String,
    broker: Broker,
    timeout: Duration,
    reconnect: Reconnect,
    /// The camera's place in [`LEASED`], freed (with the task) once the camera has been given back.
    claim: Option<Claim>,
    lease: Result<Lease, CameraError>,
    status: watch::Sender<Status>,
    out: FeedOut<Message>,
    counters: Arc<Counters>,
    /// The epoch of the connection that is live, or was last.
    epoch: Option<u32>,
    /// Failed attempts since the last live connection.
    attempt: u32,
}

impl Link {
    async fn run(mut self, mut stop: oneshot::Receiver<()>) {
        let mut end = self.keep_connected(&mut stop).await;
        // Whatever the end, the camera is given back before it is announced. A failure to do that outranks the end.
        if let Ok(lease) = &mut self.lease
            && let Err(error) = lease.release().await
        {
            if let Status::Failed(first) = &end {
                warn!(error = %first, "the subscription had already failed");
            }
            end = Status::Failed(error);
        }
        self.claim = None;
        self.status.send_replace(end);
    }

    /// Connects, and reconnects after every failure retrying can fix. Returns the status that ends the subscription.
    async fn keep_connected(&mut self, stop: &mut oneshot::Receiver<()>) -> Status {
        loop {
            let error = match self.connect_and_read(stop).await {
                Ok(()) => return Status::Closed,
                Err(error) if !error.kind.retryable() => return Status::Failed(error),
                Err(error) => error,
            };
            let next_in = self.reconnect.delay(self.attempt);
            self.attempt += 1;
            self.status
                .send_replace(Status::Reconnecting { error, next_in });
            tokio::select! {
                () = sleep(next_in) => {}
                _ = &mut *stop => return Status::Closed,
            }
        }
    }

    /// One connection: the lease if the camera is not leased yet, then the broker until it fails. `Ok` when the
    /// subscription is to end (the app closed it or dropped its end).
    ///
    /// Taking the lease is never cut short by a stop: a request the camera has accepted cannot be called back, so
    /// the lease is finished first and released after, in order. Each call is bounded by the camera's timeout.
    async fn connect_and_read(
        &mut self,
        stop: &mut oneshot::Receiver<()>,
    ) -> Result<(), CameraError> {
        let lease = self.lease.as_mut().map_err(|error| error.clone())?;
        if !lease.held() {
            lease.acquire().await?;
        }
        if !matches!(stop.try_recv(), Err(TryRecvError::Empty)) {
            return Ok(());
        }
        tokio::select! {
            result = session::read(self) => result,
            _ = stop => Ok(()),
        }
    }
}

/// A stable 64-bit FNV-1a hash: the same camera (host and web port) and data source always get the same topic, so a publisher left
/// behind by a crash is found again and reused.
fn fingerprint(text: &str) -> u64 {
    text.bytes().fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
        (hash ^ u64::from(byte)).wrapping_mul(0x0100_0000_01b3)
    })
}

/// `base` with a suffix that is different every time, in this process and between runs.
fn unique(base: &str) -> String {
    static COUNT: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| since.as_nanos());
    format!("{base}-{nanos:x}-{}", COUNT.fetch_add(1, Ordering::Relaxed))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_never_repeat() {
        assert_ne!(unique("a"), unique("a"));
    }
}
