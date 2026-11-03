//! DataHub samples from an Axis camera over its WebSocket API: a session token from the device API, one subscription
//! to one topic and channel, and a task that reconnects with backoff, like the RTSP and MQTT ones.

mod protocol;
mod socket;

use std::sync::Arc;
use std::time::{Duration, SystemTime};

use bytes::Bytes;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::time::sleep;
use tracing::info;

use crate::device::Device;
use crate::error::CameraError;
use crate::live::{Counters, FeedOut, LiveMessages, Message, Reconnect, Status, Task};
use crate::target::{Camera, Scheme};
use protocol::{LIST_TOPICS, SUBSCRIBE, Sample, protocol};
use socket::{Early, Socket};

/// How many samples may wait for the app.
const QUEUE: usize = 256;

impl Device {
    /// The topics the camera's DataHub offers, in the order it lists them.
    pub async fn datahub_topics(&self) -> Result<Vec<String>, CameraError> {
        let socket = Socket::open(self).await;
        let mut socket = socket?;
        let result = socket
            .call(LIST_TOPICS, &serde_json::json!({}), &mut Early::default())
            .await;
        socket.close().await;
        let topics = result?["topics"].as_array().cloned();
        let topics = topics.ok_or_else(|| protocol("the DataHub topics are not a list"))?;
        Ok(topics
            .iter()
            .filter_map(|topic| topic["name"].as_str().filter(|name| !name.is_empty()))
            .map(str::to_owned)
            .collect())
    }
}

/// Subscribes to `topic` on `channel` (from 1; the scene overlay is `com.axis.scene.frame.v1` on channel 1) and
/// returns at once; the work is done in the background (a tokio runtime must be running). Each sample's `data` is
/// delivered as JSON in a [`Message`]. Watch [`LiveMessages::status`] for how it goes.
///
/// A connection that fails for a reason a retry can fix is made again after a backoff, and its [`Status::Live`] epoch
/// is one higher. A rejected login, a redirect and a camera that breaks the protocol end in [`Status::Failed`]. A
/// quiet topic is not an error. The camera is not changed, so dropping the result without [`LiveMessages::close`]
/// loses nothing.
pub fn open_datahub(camera: &Camera, topic: &str, channel: u32) -> LiveMessages {
    let (messages_tx, messages) = mpsc::channel(QUEUE);
    let (status_tx, status) = watch::channel(Status::Connecting);
    let (stop_tx, stop) = oneshot::channel();
    let counters = Arc::new(Counters::default());
    if camera.web.scheme == Scheme::Https {
        info!("DataHub HTTPS/WSS certificates are not verified; use this only on trusted networks");
    }
    let link = Link {
        topic: topic.to_owned(),
        channel,
        device: Device::new(camera),
        reconnect: Reconnect::MQTT,
        status: status_tx,
        out: FeedOut::new(messages_tx),
        counters: Arc::clone(&counters),
        epoch: None,
        attempt: 0,
    };
    let handle = tokio::spawn(link.run(stop));
    LiveMessages::new(messages, status, Task::new(handle, stop_tx, counters)).without_cleanup()
}

/// Everything the subscription task owns.
struct Link {
    topic: String,
    channel: u32,
    /// A camera that cannot be addressed fails the first attempt like any other terminal failure.
    device: Result<Device, CameraError>,
    reconnect: Reconnect,
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
        let end = self.keep_connected(&mut stop).await;
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
            let next_in: Duration = self.reconnect.delay(self.attempt);
            self.attempt += 1;
            self.status
                .send_replace(Status::Reconnecting { error, next_in });
            tokio::select! {
                () = sleep(next_in) => {}
                _ = &mut *stop => return Status::Closed,
            }
        }
    }

    /// One connection: token, upgrade, subscribe, then samples until it fails. `Ok` when the subscription is to end
    /// (the app closed it or dropped its end).
    async fn connect_and_read(
        &mut self,
        stop: &mut oneshot::Receiver<()>,
    ) -> Result<(), CameraError> {
        let device = self.device.as_ref().map_err(Clone::clone)?;
        let mut early = Early::default();
        let opened = tokio::select! {
            opened = Self::subscribe(device, &self.topic, self.channel, &mut early) => opened,
            _ = &mut *stop => return Ok(()),
        };
        let mut socket = opened?;
        // The dropped ones were the oldest, so their gap comes ahead of the samples kept.
        self.out.lose(early.dropped, &self.counters);
        let epoch = self.epoch.map_or(0, |last| last + 1);
        if epoch > 0 {
            Counters::add(&self.counters.reconnects, 1);
        }
        self.epoch = Some(epoch);
        self.attempt = 0;
        self.status.send_replace(Status::Live { epoch });
        let result = loop {
            let sample = match early.samples.pop_front() {
                Some(sample) => Ok(sample),
                None => tokio::select! {
                    sample = socket.next_sample() => sample,
                    _ = &mut *stop => break Ok(()),
                },
            };
            match sample {
                Ok(sample) => {
                    if self
                        .out
                        .send(message(epoch, sample), &self.counters)
                        .is_err()
                    {
                        break Ok(());
                    }
                }
                Err(error) => break Err(error),
            }
        };
        socket.close().await;
        result
    }

    /// The connection with its subscription confirmed.
    async fn subscribe(
        device: &Device,
        topic: &str,
        channel: u32,
        early: &mut Early,
    ) -> Result<Socket, CameraError> {
        let mut socket = Socket::open(device).await?;
        let params = protocol::subscribe_params(topic, channel);
        match socket.call(SUBSCRIBE, &params, early).await {
            Ok(result)
                if result["subscriptionId"]
                    .as_str()
                    .is_some_and(|id| !id.is_empty()) =>
            {
                Ok(socket)
            }
            Ok(_) => Err(protocol("the DataHub subscription has no id")),
            Err(error) => Err(error),
        }
    }
}

fn message(epoch: u32, sample: Sample) -> Message {
    Message {
        epoch,
        topic: sample.topic,
        payload: Bytes::from(sample.data.to_string()),
        received: SystemTime::now(),
    }
}
