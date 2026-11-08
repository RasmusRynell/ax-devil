//! RTSP video and ONVIF metadata from an Axis camera, as raw bytes with their capture times.
//!
//! One task per stream owns the connection: it connects, reads, and reconnects with backoff. Retina does the RTSP
//! session (Digest login, SETUP, keepalive, interleaved TCP); the access units and metadata documents are assembled
//! here, because retina's depacketizers lose the RTP header extension that carries the capture time.

mod assemble;
mod keepalive;
mod metadata;
mod output;
mod packet;
mod session;

use std::sync::Arc;

use retina::client::SessionGroup;
use tokio::sync::{mpsc, oneshot, watch};
use tokio::time::{sleep, timeout};
use url::Url;

use crate::error::{CameraError, ErrorKind};
use crate::live::{Counters, FeedOut, LiveStream, Reconnect, Status, Task};
use crate::target::{Camera, Head};
use crate::vapix::Resolution;
use output::VideoOut;

pub use keepalive::retina_log_layer;

/// How many video frames may wait for the app: about a second at 30 frames per second.
const VIDEO_QUEUE: usize = 32;
/// How many metadata documents may wait for the app.
const METADATA_QUEUE: usize = 256;

/// What to stream.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StreamRequest {
    /// The video channel.
    pub head: Head,
    /// `None` for no video, `Some(None)` for the camera's default resolution, `Some(Some(size))` for `size`.
    pub video: Option<Option<Resolution>>,
    /// Whether to stream the scene metadata (`analytics=polygon`).
    pub metadata: bool,
    /// A stream URL to use instead of the one made from the camera's address and this request (`video` and
    /// `metadata` still pick what is played from it). It must hold no login: the login always comes from the
    /// [`Camera`], so a URL with one is [`ErrorKind::InvalidTarget`].
    pub url: Option<Url>,
    /// How to retry.
    pub reconnect: Reconnect,
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

/// The URL of the stream. It holds no login: that goes to the RTSP client apart from it.
fn media_url(camera: &Camera, request: &StreamRequest) -> Result<Url, CameraError> {
    if request.video.is_none() && !request.metadata {
        return Err(CameraError::new(
            ErrorKind::InvalidTarget,
            "the request asks for neither video nor metadata",
        ));
    }
    if let Some(url) = &request.url {
        return custom_url(url);
    }
    let mut url = Url::parse(&format!(
        "rtsp://{}:{}/axis-media/media.amp",
        camera.host, camera.rtsp_port
    ))
    .map_err(|_| {
        CameraError::new(
            ErrorKind::InvalidTarget,
            "the camera address is not a valid URL",
        )
    })?;
    {
        let mut query = url.query_pairs_mut();
        query.append_pair("camera", &request.head.number().to_string());
        query.append_pair("audio", "0");
        match request.video {
            None => {
                query.append_pair("video", "0");
            }
            Some(size) => {
                if let Some(size) = size {
                    query.append_pair("resolution", &size.to_string());
                }
                // Asks for each frame's capture time in the RTP header extension.
                query.append_pair("onvifreplayext", "1");
            }
        }
        if request.metadata {
            query.append_pair("analytics", "polygon");
        }
    }
    Ok(url)
}

/// `url` as the stream URL: RTSP, and without a login, which is the camera's to give.
fn custom_url(url: &Url) -> Result<Url, CameraError> {
    if url.scheme() == "rtsps" {
        // Retina speaks plain RTSP only; trying would retry forever as unreachable.
        return Err(CameraError::new(
            ErrorKind::Unsupported,
            "rtsps:// streams are not supported; use an rtsp:// URL",
        ));
    }
    if url.scheme() != "rtsp" || url.host().is_none() {
        return Err(CameraError::new(
            ErrorKind::InvalidTarget,
            "the stream URL is not an rtsp:// URL with a host",
        ));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(CameraError::new(
            ErrorKind::InvalidTarget,
            "the stream URL holds a login; the camera's login is used instead",
        ));
    }
    Ok(url.clone())
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::target::{Host, Login, Secret};

    fn camera() -> Camera {
        let mut camera = Camera::new(Host::parse("192.0.2.7").unwrap());
        camera.login = Some(Login {
            user: "root".into(),
            password: Secret::new("hunter2-secret"),
        });
        camera
    }

    fn request(video: Option<Option<Resolution>>, metadata: bool) -> StreamRequest {
        StreamRequest {
            head: Head::FIRST,
            video,
            metadata,
            url: None,
            reconnect: Reconnect::RTSP,
        }
    }

    #[test]
    fn the_url_asks_for_what_is_wanted_and_holds_no_login() {
        let size = Resolution {
            width: 1280,
            height: 720,
        };
        let url = media_url(&camera(), &request(Some(Some(size)), true)).unwrap();
        assert_eq!(
            url.as_str(),
            "rtsp://192.0.2.7:554/axis-media/media.amp?camera=1&audio=0&resolution=1280x720&onvifreplayext=1&analytics=polygon"
        );
        let url = media_url(&camera(), &request(None, true)).unwrap();
        assert_eq!(
            url.query(),
            Some("camera=1&audio=0&video=0&analytics=polygon")
        );
        let error = media_url(&camera(), &request(None, false)).unwrap_err();
        assert_eq!(error.kind, ErrorKind::InvalidTarget);
    }

    #[test]
    fn a_custom_url_replaces_the_generated_one_but_never_carries_a_login() {
        let mut custom = request(Some(None), false);
        custom.url = Url::parse("rtsp://192.0.2.8:8554/other?x=1").ok();
        assert_eq!(
            media_url(&camera(), &custom).unwrap().as_str(),
            "rtsp://192.0.2.8:8554/other?x=1"
        );
        for bad in [
            "rtsp://root@192.0.2.8/s",
            "rtsp://root:hunter2-secret@192.0.2.8/s",
            "rtsp://:hunter2-secret@192.0.2.8/s",
            "http://192.0.2.8/s",
        ] {
            custom.url = Url::parse(bad).ok();
            let error = media_url(&camera(), &custom).unwrap_err();
            assert_eq!(error.kind, ErrorKind::InvalidTarget, "{bad}");
            assert!(!error.to_string().contains("hunter2"));
        }
    }

    #[test]
    fn rtsps_is_a_terminal_failure() {
        let mut custom = request(Some(None), false);
        custom.url = Url::parse("rtsps://192.0.2.8/s").ok();
        let error = media_url(&camera(), &custom).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Unsupported);
        assert!(!error.kind.retryable());
    }

    /// A camera that never answers in time is retried; the waits between attempts double up to the cap. (A zero
    /// timeout fails each attempt without I/O, which paused time cannot wait for.)
    #[tokio::test(start_paused = true)]
    async fn a_failed_attempt_is_retried_with_doubling_waits() {
        let mut camera = camera();
        camera.timeout = Duration::ZERO;
        let mut stream = open_stream(&camera, request(Some(None), false));
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
