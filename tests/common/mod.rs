//! The playback tests' backend, viewer and envelope reader.

use api_types::{
    CursorPoint, ErrorWire,
    playback::{ClientMessage, Direction, ServerMessage, Want},
};
use ax_devil::{AppState, access::Policy, app, video_sources::VideoSources};
use futures_util::{SinkExt, StreamExt};
use playback::{CachePool, Registry};
use sessions::{
    CursorOptions,
    session::{Command, ContentRef, Handle, Reply},
};
use std::{
    collections::BTreeSet,
    net::SocketAddr,
    ops::RangeInclusive,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::net::TcpStream;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};
use workspace_store::{DocCommand, Resolution};

/// Bounds every wait, so a broken backend fails the test instead of hanging it.
pub const WAIT: Duration = Duration::from_secs(20);
const SQUEEZED_BUFFER: u32 = 4096;
pub const MIB: usize = 1024 * 1024;

pub fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures/video")
        .join(name)
}

pub fn luma(sequence_id: u64) -> u8 {
    (sequence_id * 7 % 256) as u8
}

/// The backend: the real app on a local port, over private settings and caches.
pub struct Backend {
    squeezed: bool,
    _stop: tokio::sync::watch::Sender<bool>,
    pub address: SocketAddr,
    pub videos: Arc<VideoSources>,
    pub state: AppState,
    pub folder: tempfile::TempDir,
}

/// A video in a session of its own, ready to be streamed: whoever holds it can ask for tickets.
pub struct Added {
    pub owner: Handle<CursorPoint>,
    pub key: ContentRef,
}

impl Added {
    /// A new ticket for the video's stream.
    pub fn ticket(&self) -> String {
        match self.owner.command(Command::RequestTicket(self.key.clone())) {
            Ok(Reply::Ticket(ticket)) => ticket.to_string(),
            other => panic!("expected a ticket, got {other:?}"),
        }
    }
}

impl Backend {
    pub async fn start() -> Self {
        Self::start_with(false).await
    }

    /// A backend and viewers whose sockets hold only a few kilobytes, so a viewer that stops reading backs the
    /// backend up after a few frames.
    pub async fn start_squeezed() -> Self {
        Self::start_with(true).await
    }

    async fn start_with(squeezed: bool) -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let registry = Registry::new(CachePool::new(256 * MIB));
        let videos = Arc::new(VideoSources::new(registry, &folder.path().join("cache")));
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let state = AppState::new(Arc::new(settings), Policy::Local, videos.clone(), stopping);
        let router = app(state.clone());
        let socket = tokio::net::TcpSocket::new_v4().unwrap();
        if squeezed {
            // Connections accepted from this socket inherit the small send buffer.
            socket.set_send_buffer_size(SQUEEZED_BUFFER).unwrap();
        }
        socket.bind("127.0.0.1:0".parse().unwrap()).unwrap();
        let listener = socket.listen(16).unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(
                listener,
                router.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await
        });
        Self {
            squeezed,
            _stop: stop,
            address,
            videos,
            state,
            folder,
        }
    }

    /// Adds the video to a new session of its own, with a viewer requested, and returns it once it resolved.
    pub async fn add(&self, path: &Path) -> Added {
        let owner = self
            .state
            .sessions
            .create(true, CursorOptions::default())
            .unwrap();
        let item = workspace::intake::video(path).unwrap();
        let id = item.id;
        let added = DocCommand::AddItems {
            items: vec![item],
            open: true,
        };
        owner.command(Command::Content(added)).unwrap();
        let key = tokio::time::timeout(WAIT, async {
            loop {
                let state = owner.state();
                if let Some(Resolution::Resolved {
                    content,
                    generation,
                }) = state.doc.workspace.resolutions.get(&id)
                {
                    break ContentRef {
                        content: content.id().clone(),
                        generation: *generation,
                    };
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("the video resolves");
        Added { owner, key }
    }

    pub async fn viewer(&self) -> Viewer {
        let url = format!("ws://{}/api/playback", self.address);
        if !self.squeezed {
            let (socket, _) = tokio_tungstenite::connect_async(url).await.unwrap();
            return Viewer {
                socket,
                close_code: None,
            };
        }
        let socket = tokio::net::TcpSocket::new_v4().unwrap();
        socket.set_recv_buffer_size(SQUEEZED_BUFFER).unwrap();
        let stream = socket.connect(self.address).await.unwrap();
        let (socket, _) = tokio_tungstenite::client_async(url, MaybeTlsStream::Plain(stream))
            .await
            .unwrap();
        Viewer {
            socket,
            close_code: None,
        }
    }

    /// Waits until no decode thread is left, which is what releasing the last viewer of a source must lead to.
    pub async fn wait_for_workers(&self, expected: usize) {
        tokio::time::timeout(WAIT, async {
            while self.videos.registry().running_workers() != expected {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap_or_else(|_| {
            panic!(
                "{} decode threads, expected {expected}",
                self.videos.registry().running_workers()
            )
        });
    }
}

/// One frame envelope, read with the offsets of the documented layout.
pub struct Frame {
    pub sequence_id: u64,
    pub timestamp_us: i64,
    pub flags: u32,
    pub width: u32,
    pub height: u32,
    pub layout: u8,
    pub plane_lengths: [u32; 3],
    pub planes: Vec<u8>,
}

impl Frame {
    /// The luma every sample of the Y plane has; fails when the picture is not flat.
    pub fn flat_luma(&self) -> u8 {
        let y = &self.planes[..self.plane_lengths[0] as usize];
        assert!(
            y.iter().all(|sample| *sample == y[0]),
            "frame {} is not flat",
            self.sequence_id
        );
        y[0]
    }
}

pub fn u32_at(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}

pub fn u64_at(bytes: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(bytes[at..at + 8].try_into().unwrap())
}

pub enum Event {
    Message(ServerMessage),
    Frame(Frame),
    Timeline(Vec<f64>),
    Closed,
}

/// The envelope head, checked: magic, version 1 and a payload that starts where `header_len` says.
pub fn parse_envelope(bytes: &[u8]) -> Event {
    assert_eq!(&bytes[..4], b"AXDV");
    assert_eq!(bytes[4], 1, "envelope version");
    let payload_at = u16::from_le_bytes([bytes[6], bytes[7]]) as usize;
    match bytes[5] {
        1 => {
            assert_eq!(payload_at, 64);
            let lengths = [u32_at(bytes, 52), u32_at(bytes, 56), u32_at(bytes, 60)];
            let planes = bytes[payload_at..].to_vec();
            assert_eq!(
                planes.len(),
                lengths.iter().sum::<u32>() as usize,
                "the message is exactly its planes"
            );
            Event::Frame(Frame {
                sequence_id: u64_at(bytes, 8),
                timestamp_us: u64_at(bytes, 16) as i64,
                flags: u32_at(bytes, 24),
                width: u32_at(bytes, 28),
                height: u32_at(bytes, 32),
                layout: bytes[36],
                plane_lengths: lengths,
                planes,
            })
        }
        3 => {
            assert_eq!(payload_at, 32);
            Event::Timeline(
                bytes[payload_at..]
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .map(|time| f64::from_le_bytes(*time))
                    .collect(),
            )
        }
        kind => panic!("unexpected envelope kind {kind}"),
    }
}

/// What `Opened` says about a video, and the Timeline that follows it.
pub struct Described {
    pub frame_count: u64,
    pub frame_bytes: u64,
    pub duration_us: i64,
    pub times: Vec<f64>,
}

pub struct Viewer {
    pub socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
    /// The code of the close frame the backend ended the socket with, once it did.
    pub close_code: Option<u16>,
}

impl Viewer {
    /// Sends text that is not a protocol message.
    pub async fn send_garbage(&mut self) {
        self.socket.send(Message::text("not json")).await.unwrap();
    }

    fn plain(&self) -> &TcpStream {
        let MaybeTlsStream::Plain(stream) = self.socket.get_ref() else {
            panic!("a plain socket");
        };
        stream
    }

    /// Waits, without reading anything, until the backend has filled this viewer's receive buffer and nothing more
    /// arrives: the bytes waiting are the same over several polls. A backend that has more to send is then stuck
    /// writing it.
    pub async fn wait_until_backed_up(&mut self) {
        let stream = self.plain();
        let mut buffer = [0u8; 16 * SQUEEZED_BUFFER as usize];
        tokio::time::timeout(WAIT, async {
            let (mut waiting, mut unchanged) = (0, 0);
            while unchanged < 5 {
                tokio::time::sleep(Duration::from_millis(20)).await;
                let now = stream.peek(&mut buffer).await.unwrap();
                unchanged = if now == waiting && now > 0 {
                    unchanged + 1
                } else {
                    0
                };
                waiting = now;
            }
        })
        .await
        .expect("the backend never backed up");
    }

    pub async fn send(&mut self, message: &ClientMessage) {
        let body = serde_json::to_string(message).unwrap();
        self.socket.send(Message::text(body)).await.unwrap();
    }

    pub async fn open(&mut self, ticket: &str, start_frame: Option<u64>) {
        self.send(&ClientMessage::Open {
            ticket: ticket.to_owned(),
            start_frame,
        })
        .await;
    }

    pub async fn want(&mut self, playhead: u64, ahead: u32, behind: u32, direction: Direction) {
        self.send(&ClientMessage::Want(Want {
            playhead,
            ahead,
            behind,
            direction,
        }))
        .await;
    }

    /// The next thing the backend sent; fails if nothing comes.
    pub async fn next(&mut self) -> Event {
        loop {
            let received = tokio::time::timeout(WAIT, self.socket.next())
                .await
                .expect("the backend sent nothing");
            match received {
                Some(Ok(Message::Text(body))) => {
                    return Event::Message(serde_json::from_str(&body).unwrap());
                }
                Some(Ok(Message::Binary(bytes))) => return parse_envelope(&bytes),
                Some(Ok(Message::Ping(_) | Message::Pong(_))) => {}
                Some(Ok(Message::Close(frame))) => {
                    self.close_code = frame.map(|frame| frame.code.into());
                    return Event::Closed;
                }
                Some(Err(_)) | None => return Event::Closed,
                Some(Ok(other)) => panic!("unexpected message {other:?}"),
            }
        }
    }

    /// Reads `Indexing` progress, then `Opened`, then the Timeline envelope, which must come right after it.
    pub async fn opened(&mut self) -> Described {
        let mut progress = Vec::new();
        let (frame_count, frame_bytes, duration_us) = loop {
            match self.next().await {
                Event::Message(ServerMessage::Indexing { progress: value }) => progress.push(value),
                Event::Message(ServerMessage::Opened {
                    protocol,
                    frame_count,
                    duration_us,
                    frame_bytes,
                }) => {
                    assert_eq!(protocol, 2);
                    break (frame_count, frame_bytes, duration_us);
                }
                _ => panic!("expected Indexing or Opened"),
            }
        };
        assert!(
            progress.windows(2).all(|pair| pair[0] <= pair[1]),
            "progress only grows: {progress:?}"
        );
        assert!(progress.iter().all(|value| (0.0..=1.0).contains(value)));
        let Event::Timeline(times) = self.next().await else {
            panic!("the Timeline envelope must follow Opened");
        };
        Described {
            frame_count,
            frame_bytes,
            duration_us,
            times,
        }
    }

    /// Reads frames until every id in `wanted` arrived, checking each against the picture the fixture holds for it.
    /// Returns the ids in arrival order. Frames outside `wanted`, such as the tail of an earlier window, are fine.
    pub async fn frames(&mut self, wanted: RangeInclusive<u64>) -> Vec<u64> {
        let mut arrived = Vec::new();
        let mut missing: BTreeSet<u64> = wanted.collect();
        while !missing.is_empty() {
            let Event::Frame(frame) = self.next().await else {
                panic!("expected frames, still missing {missing:?}");
            };
            assert_eq!(
                frame.flat_luma(),
                luma(frame.sequence_id),
                "frame {} shows another picture",
                frame.sequence_id
            );
            missing.remove(&frame.sequence_id);
            arrived.push(frame.sequence_id);
        }
        arrived
    }

    /// The `Error` that ends the session, then the end of the socket.
    pub async fn error(&mut self) -> ErrorWire {
        let Event::Message(ServerMessage::Error(error)) = self.next().await else {
            panic!("expected an Error");
        };
        assert!(
            matches!(self.next().await, Event::Closed),
            "the socket closes after Error"
        );
        error
    }
}
