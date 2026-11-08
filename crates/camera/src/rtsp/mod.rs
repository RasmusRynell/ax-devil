//! RTSP video and ONVIF metadata from an Axis camera, as raw bytes with their capture times.
//!
//! One task per stream owns the connection: it connects, reads, and reconnects with backoff. Retina does the RTSP
//! session (Digest login, SETUP, keepalive, interleaved TCP); the access units and metadata documents are assembled
//! here, because retina's depacketizers lose the RTP header extension that carries the capture time. Through the
//! camera's web server, `tunnel` and `relay` carry the RTSP messages and hand retina a loopback connection.

mod assemble;
mod endpoint;
mod failure;
mod keepalive;
mod metadata;
mod output;
mod packet;
mod relay;
mod session;
mod tunnel;

use std::fmt;
use std::sync::Arc;

use retina::client::SessionGroup;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::time::{sleep, timeout};
use tracing::info;
use url::Url;

use crate::error::ErrorKind;
use crate::live::{Counters, FeedOut, LiveStream, Reconnect, Status, Task};
use crate::target::{Camera, Head, Scheme};
use crate::vapix::Resolution;
use output::VideoOut;

pub use keepalive::retina_log_layer;

/// How many video frames may wait for the app: about a second at 30 frames per second.
const VIDEO_QUEUE: usize = 32;
/// How many metadata documents may wait for the app.
const METADATA_QUEUE: usize = 256;

/// Which door of the camera the stream comes through.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RtspRoute {
    /// The camera's RTSP port.
    Direct,
    /// The camera's web server (the camera's `web` port and scheme), for a network that blocks the RTSP port.
    WebApi,
}

/// What to stream. `Debug` leaves out the password of a login in `url`.
#[derive(Clone, PartialEq, Eq)]
pub struct StreamRequest {
    /// The video channel.
    pub head: Head,
    /// `None` for no video, `Some(None)` for the camera's default resolution, `Some(Some(size))` for `size`.
    pub video: Option<Option<Resolution>>,
    /// Whether to stream the scene metadata (`analytics=polygon`).
    pub metadata: bool,
    /// Where the generated stream URL points. Ignored when `url` is set: its scheme decides.
    pub route: RtspRoute,
    /// A stream URL to use instead of the one made from the camera's address and this request (`video` and
    /// `metadata` still pick what is played from it): `rtsp://`, or `http://` / `https://` to tunnel through a web
    /// server. A login in it is used instead of the camera's. Any other scheme, or none with a host, fails the stream
    /// for good as [`ErrorKind::InvalidTarget`], and `rtsps://` as [`ErrorKind::Unsupported`].
    pub url: Option<Url>,
    /// How to retry.
    pub reconnect: Reconnect,
}

impl fmt::Debug for StreamRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Every field is named, so a new one cannot be left out unnoticed.
        let Self {
            head,
            video,
            metadata,
            route,
            url,
            reconnect,
        } = self;
        f.debug_struct("StreamRequest")
            .field("head", head)
            .field("video", video)
            .field("metadata", metadata)
            .field("route", route)
            .field("url", &url.as_ref().map(endpoint::without_password))
            .field("reconnect", reconnect)
            .finish()
    }
}

/// Opens a stream and returns at once; the connection is made in the background (a tokio runtime must be running).
/// Watch [`LiveStream::status`] for how it goes. A failure retrying cannot fix ends in [`Status::Failed`].
///
/// The login answers the camera's challenge as retina does: a camera that offers only Basic on plain `rtsp://` gets
/// the password in cleartext (see the crate README).
///
/// A keepalive the camera refuses with 401 or 403 ends the stream only if the process installed [`retina_log_layer`]
/// in its `tracing` subscriber.
pub fn open_stream(camera: &Camera, request: StreamRequest) -> LiveStream {
    let (video_tx, video) = mpsc::channel(VIDEO_QUEUE);
    let (metadata_tx, metadata) = mpsc::channel(METADATA_QUEUE);
    let (status_tx, status) = watch::channel(Status::Connecting);
    let (stop_tx, stop) = oneshot::channel();
    let counters = Arc::new(Counters::default());
    let https = match &request.url {
        Some(url) => url.scheme() == "https",
        None => request.route == RtspRoute::WebApi && camera.web.scheme == Scheme::Https,
    };
    if https {
        info!("RTSP over HTTPS certificates are not verified; use this only on trusted networks");
    }
    let link = Link {
        camera: camera.clone(),
        request,
        status: status_tx,
        video: VideoOut::new(video_tx),
        metadata: FeedOut::new(metadata_tx),
        counters: Arc::clone(&counters),
        sessions: Arc::default(),
    };
    let handle = tokio::spawn(link.run(stop));
    LiveStream::new(
        video,
        metadata,
        status,
        Task::new(handle, stop_tx, counters),
    )
}

/// Everything the connection task owns.
struct Link {
    camera: Camera,
    request: StreamRequest,
    status: watch::Sender<Status>,
    video: VideoOut,
    metadata: FeedOut<crate::live::MetadataDoc>,
    counters: Arc<Counters>,
    /// Every session of this stream, so a close can wait for their TEARDOWN.
    sessions: Arc<SessionGroup>,
}

impl Link {
    async fn run(mut self, stop: oneshot::Receiver<()>) {
        let end = tokio::select! {
            end = self.keep_connected() => end,
            _ = stop => {
                // Retina's TEARDOWN starts when the session is dropped, which the select has just done. Wait for
                // its one attempt on the open connection to end, so the camera is told before close() returns.
                // That is all that is guaranteed: the attempt may have failed, or the wait may have timed out.
                // Through a tunnel the relay is detached and carries it on.
                let _ = timeout(self.camera.timeout, self.sessions.await_teardown()).await;
                Status::Closed
            }
        };
        self.status.send_replace(end);
    }

    /// Connects, and reconnects after every failure retrying can fix. Returns the status that ends the stream.
    async fn keep_connected(&mut self) -> Status {
        let mut attempt = 0;
        let mut epoch: Option<u32> = None;
        loop {
            let outcome = match self.open().await {
                Ok(opened) => {
                    let current = epoch.map_or(0, |last| last + 1);
                    epoch = Some(current);
                    if current > 0 {
                        Counters::add(&self.counters.reconnects, 1);
                    }
                    self.status.send_replace(Status::Live { epoch: current });
                    self.read(opened, current, &mut attempt).await
                }
                Err(error) => Err(error),
            };
            let error = match outcome {
                Ok(()) => return Status::Closed,
                // A stream the camera does not have yet (404) is retried too: it may be starting up.
                Err(error) if !(error.kind.retryable() || error.kind == ErrorKind::NotFound) => {
                    return Status::Failed(error);
                }
                Err(error) => error,
            };
            let next_in = self.request.reconnect.delay(attempt);
            attempt += 1;
            self.status
                .send_replace(Status::Reconnecting { error, next_in });
            sleep(next_in).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::target::{Host, Login, Secret};

    /// A camera that never answers in time is retried; the waits between attempts double up to the cap. (A zero
    /// timeout fails each attempt without I/O, which paused time cannot wait for.)
    #[tokio::test(start_paused = true)]
    async fn a_failed_attempt_is_retried_with_doubling_waits() {
        let mut camera = Camera::new(Host::parse("192.0.2.7").unwrap());
        camera.login = Some(Login {
            user: "root".into(),
            password: Secret::new("hunter2-secret"),
        });
        camera.timeout = Duration::ZERO;
        let request = StreamRequest {
            head: Head::FIRST,
            video: Some(None),
            metadata: false,
            route: RtspRoute::Direct,
            url: None,
            reconnect: Reconnect::RTSP,
        };
        let mut stream = open_stream(&camera, request);
        let mut waits = Vec::new();
        while waits.len() < 5 {
            stream.status.changed().await.unwrap();
            if let Status::Reconnecting { error, next_in } = &*stream.status.borrow() {
                assert_eq!(error.kind, ErrorKind::TimedOut);
                waits.push(*next_in);
            }
        }
        let ms = |n| Duration::from_millis(n);
        assert_eq!(waits, [ms(250), ms(500), ms(1000), ms(2000), ms(2000)]);
        assert_eq!(stream.stats().reconnects, 0, "no connection was ever live");
        stream.close().await;
    }
}
