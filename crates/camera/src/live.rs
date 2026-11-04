//! What a live connection hands out: items, status, reconnect policy and counters. The transports that fill these
//! (RTSP, MQTT, DataHub) own the connections; this is the contract the app reads.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime};

use bytes::Bytes;
use tokio::sync::mpsc::error::TrySendError;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::task::JoinHandle;
use tracing::warn;

use crate::error::CameraError;

/// When a frame or document was captured, on the camera's clock: nanoseconds since the Unix epoch.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct CaptureTime(pub i64);

/// The state of a live connection, published on a watch channel.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Status {
    /// The first connection is being made.
    Connecting,
    /// Data flows; `epoch` is the connection's number and grows by one with every reconnect.
    Live {
        /// The connection's number.
        epoch: u32,
    },
    /// The connection failed with a retryable error; the next attempt comes after `next_in`.
    Reconnecting {
        /// Why it failed.
        error: CameraError,
        /// How long until the next attempt.
        next_in: Duration,
    },
    /// A failure retrying cannot fix; the connection is over.
    Failed(CameraError),
    /// Closed on request.
    Closed,
}

/// How a connection retries: wait `first`, doubling up to `max`, and (for streams) give up on a connection that has
/// been silent for `silence`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Reconnect {
    /// The wait before the first retry.
    pub first: Duration,
    /// The longest wait between retries.
    pub max: Duration,
    /// How long a stream may deliver nothing before it counts as lost; `None` never gives up on silence.
    pub silence: Option<Duration>,
}

impl Reconnect {
    /// RTSP: retry quickly, 0.25 s to 2 s, and treat 5 s of silence as lost.
    pub const RTSP: Self = Self {
        first: Duration::from_millis(250),
        max: Duration::from_secs(2),
        silence: Some(Duration::from_secs(5)),
    };

    /// MQTT and DataHub: 0.25 s to 8 s; a quiet topic is normal, so silence is not an error.
    pub const MQTT: Self = Self {
        first: Duration::from_millis(250),
        max: Duration::from_secs(8),
        silence: None,
    };

    /// The wait before retry number `attempt` (0 is the first): `first` doubled each time, never over `max`.
    pub fn delay(&self, attempt: u32) -> Duration {
        let doubled = self
            .first
            .saturating_mul(2u32.saturating_pow(attempt.min(31)));
        doubled.min(self.max)
    }
}

/// The video codecs a camera streams.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VideoCodec {
    /// H.264 (AVC).
    H264,
    /// H.265 (HEVC).
    H265,
}

/// What a decoder needs to know about a video stream before its first frame (no decoding here).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CodecParams {
    /// The codec of every [`AccessUnit`].
    pub codec: VideoCodec,
    /// The parameter sets (SPS and PPS, and VPS for H.265) from the camera's description, Annex B. Empty when the
    /// camera gave none; they are then in the keyframes.
    pub parameter_sets: Bytes,
}

/// One video frame as Annex B bytes. A keyframe carries its parameter sets, so decoding can start at any keyframe.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AccessUnit {
    /// The connection this frame came from ([`Status::Live`]).
    pub epoch: u32,
    /// The stream's codec.
    pub codec: Arc<CodecParams>,
    /// Whether decoding can start here.
    pub keyframe: bool,
    /// When the camera captured it, if it said.
    pub capture: Option<CaptureTime>,
    /// The frame, Annex B.
    pub data: Bytes,
}

/// One ONVIF metadata document, unparsed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetadataDoc {
    /// The connection this document came from.
    pub epoch: u32,
    /// When the camera captured it, if it said. An AXIS Q1656 on AXIS OS 13.2 never does on metadata packets; the
    /// document's own `UtcTime` holds it then.
    pub capture: Option<CaptureTime>,
    /// The XML.
    pub xml: Bytes,
}

/// One MQTT message, or one DataHub sample (its topic and its `data` as JSON).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Message {
    /// The connection this message came from.
    pub epoch: u32,
    /// The topic it was published on.
    pub topic: String,
    /// The payload.
    pub payload: Bytes,
    /// When this machine received it.
    pub received: SystemTime,
}

/// What a lane that must not lose data silently delivers: the item, or a marker that `lost` items were dropped here.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Feed<T> {
    /// An item.
    Item(T),
    /// `lost` items were dropped because the reader fell behind; shown as a lane error.
    Gap {
        /// How many.
        lost: u64,
    },
}

/// The app has dropped its end; nothing more is wanted.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Gone;

/// A queue that must not lose anything silently: what does not fit becomes a [`Feed::Gap`] ahead of the next item
/// that does.
pub(crate) struct FeedOut<T> {
    tx: mpsc::Sender<Feed<T>>,
    lost: u64,
}

impl<T> FeedOut<T> {
    pub(crate) fn new(tx: mpsc::Sender<Feed<T>>) -> Self {
        Self { tx, lost: 0 }
    }

    /// Notes that `n` items never reached the queue (packet loss, say).
    pub(crate) fn lose(&mut self, n: u64, counters: &Counters) {
        self.lost += n;
        Counters::add(&counters.feed_lost, n);
    }

    /// Queues `item`, after the gap that precedes it if there is one; when either does not fit, the item is lost.
    pub(crate) fn send(&mut self, item: T, counters: &Counters) -> Result<(), Gone> {
        if self.lost > 0 {
            match self.tx.try_send(Feed::Gap { lost: self.lost }) {
                Ok(()) => self.lost = 0,
                Err(TrySendError::Full(_)) => {
                    self.lose(1, counters);
                    return Ok(());
                }
                Err(TrySendError::Closed(_)) => return Err(Gone),
            }
        }
        match self.tx.try_send(Feed::Item(item)) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => {
                self.lose(1, counters);
                Ok(())
            }
            Err(TrySendError::Closed(_)) => Err(Gone),
        }
    }
}

/// Counters of a live connection since it was opened.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    /// Reconnects so far.
    pub reconnects: u64,
    /// Video frames discarded because the reader lagged or the frame was damaged (everything up to the next keyframe).
    pub video_dropped: u64,
    /// Metadata documents or messages lost, to overflow or to packet loss; each loss also reaches the reader as a
    /// [`Feed::Gap`].
    pub feed_lost: u64,
}

/// The shared counters behind [`Stats`].
#[derive(Debug, Default)]
pub(crate) struct Counters {
    pub(crate) reconnects: AtomicU64,
    pub(crate) video_dropped: AtomicU64,
    pub(crate) feed_lost: AtomicU64,
}

impl Counters {
    /// Adds `n` to `counter`.
    pub(crate) fn add(counter: &AtomicU64, n: u64) {
        counter.fetch_add(n, Ordering::Relaxed);
    }

    fn snapshot(&self) -> Stats {
        Stats {
            reconnects: self.reconnects.load(Ordering::Relaxed),
            video_dropped: self.video_dropped.load(Ordering::Relaxed),
            feed_lost: self.feed_lost.load(Ordering::Relaxed),
        }
    }
}

/// The task behind a live connection: stopped by [`close`](Task::close), aborted when dropped.
#[derive(Debug)]
pub(crate) struct Task {
    stop: Option<oneshot::Sender<()>>,
    handle: Option<JoinHandle<()>>,
    counters: Arc<Counters>,
}

impl Task {
    /// Wraps a spawned `handle` that ends when `stop` fires.
    pub(crate) fn new(
        handle: JoinHandle<()>,
        stop: oneshot::Sender<()>,
        counters: Arc<Counters>,
    ) -> Self {
        Self {
            stop: Some(stop),
            handle: Some(handle),
            counters,
        }
    }

    /// Whether the task is still working.
    fn running(&self) -> bool {
        self.handle
            .as_ref()
            .is_some_and(|handle| !handle.is_finished())
    }

    /// Asks the task to stop and waits until it has.
    async fn close(&mut self) {
        if let Some(stop) = self.stop.take() {
            // The task may have ended already; then there is nothing to stop.
            let _ = stop.send(());
        }
        if let Some(handle) = self.handle.take() {
            // A task that panicked or was cancelled is just as finished.
            let _ = handle.await;
        }
    }
}

impl Drop for Task {
    fn drop(&mut self) {
        if let Some(handle) = &self.handle {
            handle.abort();
        }
    }
}

/// A live RTSP connection: video, metadata and status. Dropping it ends the connection at once; [`close`] ends it
/// and waits.
///
/// [`close`]: LiveStream::close
#[derive(Debug)]
pub struct LiveStream {
    /// Video frames.
    pub video: mpsc::Receiver<AccessUnit>,
    /// Metadata documents.
    pub metadata: mpsc::Receiver<Feed<MetadataDoc>>,
    /// The connection's state.
    pub status: watch::Receiver<Status>,
    task: Task,
}

impl LiveStream {
    pub(crate) fn new(
        video: mpsc::Receiver<AccessUnit>,
        metadata: mpsc::Receiver<Feed<MetadataDoc>>,
        status: watch::Receiver<Status>,
        task: Task,
    ) -> Self {
        Self {
            video,
            metadata,
            status,
            task,
        }
    }

    /// Ends the connection and waits until it is closed; the status is then [`Status::Closed`].
    pub async fn close(mut self) {
        self.task.close().await;
    }

    /// The counters since the stream was opened.
    pub fn stats(&self) -> Stats {
        self.task.counters.snapshot()
    }
}

/// A live subscription to messages, MQTT or DataHub, with its status. An MQTT subscription holds a publisher on the
/// camera and the camera's MQTT client settings, and [`close`] gives both back before it returns; dropping it without
/// closing ends the connection but cannot, and logs a warning. A DataHub subscription holds nothing on the camera.
///
/// [`close`]: LiveMessages::close
#[derive(Debug)]
pub struct LiveMessages {
    /// Messages.
    pub messages: mpsc::Receiver<Feed<Message>>,
    /// The connection's state.
    pub status: watch::Receiver<Status>,
    task: Task,
    /// Whether a drop without `close` leaves something behind on the camera.
    restores: bool,
}

impl LiveMessages {
    pub(crate) fn new(
        messages: mpsc::Receiver<Feed<Message>>,
        status: watch::Receiver<Status>,
        task: Task,
    ) -> Self {
        Self {
            messages,
            status,
            task,
            restores: true,
        }
    }

    /// Marks a subscription that leaves nothing on the camera, so dropping it needs no warning.
    pub(crate) fn without_cleanup(mut self) -> Self {
        self.restores = false;
        self
    }

    /// Ends the subscription. For MQTT it removes the publisher this created on the camera and restores the camera's MQTT client
    /// settings, and waits until that is done; the status is then [`Status::Closed`], or [`Status::Failed`] when the
    /// camera could not be restored. A subscription still being set up on the camera is finished first, so this can
    /// take up to a few of the camera's timeouts.
    pub async fn close(mut self) {
        self.task.close().await;
    }

    /// The counters since the subscription was opened.
    pub fn stats(&self) -> Stats {
        self.task.counters.snapshot()
    }
}

impl Drop for LiveMessages {
    fn drop(&mut self) {
        if self.restores && self.task.running() {
            warn!(
                "an MQTT subscription was dropped without close(): a publisher may be left on the camera"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delay_doubles_up_to_the_cap() {
        let r = Reconnect::RTSP;
        let waits: Vec<u128> = (0..5).map(|n| r.delay(n).as_millis()).collect();
        assert_eq!(waits, [250, 500, 1000, 2000, 2000]);
        assert_eq!(Reconnect::MQTT.delay(u32::MAX), Duration::from_secs(8));
    }

    #[test]
    fn an_overflowing_feed_delivers_a_gap_before_the_next_item() {
        let counters = Counters::default();
        let (tx, mut rx) = mpsc::channel(2);
        let mut out = FeedOut::new(tx);
        for n in 0..4 {
            out.send(n, &counters).unwrap();
        }
        assert_eq!(counters.feed_lost.load(Ordering::Relaxed), 2);
        assert_eq!(rx.try_recv().unwrap(), Feed::Item(0));
        assert_eq!(rx.try_recv().unwrap(), Feed::Item(1));
        out.send(4, &counters).unwrap();
        assert_eq!(rx.try_recv().unwrap(), Feed::Gap { lost: 2 });
        assert_eq!(rx.try_recv().unwrap(), Feed::Item(4));
    }

    #[test]
    fn a_dropped_reader_is_reported() {
        let (tx, rx) = mpsc::channel(1);
        drop(rx);
        let mut out = FeedOut::new(tx);
        assert_eq!(out.send(1, &Counters::default()), Err(Gone));
    }
}
