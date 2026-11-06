//! The playback tests' backend and viewer; `envelope` reads what they receive.

// Each suite that includes this module uses its own part of it.
#![allow(dead_code)]

pub mod export_kit;

mod envelope;
pub use envelope::Event;
use envelope::parse_envelope;

use api_types::{
    CursorPoint, ErrorWire,
    playback::{ClientMessage, Direction, LaneInfo, PLAYBACK_PROTOCOL, ServerMessage, Want},
};
use ax_devil::{AppState, access::Policy, app, plugins::Plugins, video_sources::VideoSources};
use futures_util::{SinkExt, StreamExt};
use handler_api::Settings;
use handlers::Handlers;
use playback::{CachePool, Registry};
use sessions::{
    CursorOptions,
    session::{Command, ContentRef, Handle, Layout, Reply, View, ViewerId, ViewerView},
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
use workspace::{Content, ContentId, WorkspaceItem, intake::NewOverlay};
use workspace_store::{DocCommand, Resolution};

/// The image size in pixels the MOT files of these tests are measured in.
pub const MOT_SIZE: (u32, u32) = (1920, 1080);

/// The options of a MOT file measured in `width` by `height` pixels.
pub fn mot_options((width, height): (u32, u32)) -> Settings {
    Settings::from_iter([
        ("width".to_owned(), width.into()),
        ("height".to_owned(), height.into()),
    ])
}

/// Bounds every wait, so a broken backend fails the test instead of hanging it.
pub const WAIT: Duration = Duration::from_secs(20);
const SQUEEZED_BUFFER: u32 = 4096;
/// A tenth of the viewer's default 512 MiB frame budget, what `open` offers for Sample Items.
pub const SAMPLE_BUDGET: u64 = 53_687_091;
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

/// An item in a session of its own, ready to be streamed: whoever holds it can ask for tickets.
pub struct Added {
    pub owner: Handle<CursorPoint>,
    /// The stream of the item's first entry: the one entry of a video.
    pub key: ContentRef,
    /// What the item resolved to.
    pub content: Content,
}

impl Added {
    /// A new ticket for the first entry's stream.
    pub fn ticket(&self) -> String {
        self.ticket_for(&self.key.entry)
    }

    /// A new ticket for the stream of the entry with this id.
    pub fn ticket_for(&self, entry: &ContentId) -> String {
        let key = ContentRef {
            entry: entry.clone(),
            ..self.key.clone()
        };
        match self.owner.command(Command::RequestTicket(key)) {
            Ok(Reply::Ticket(ticket)) => ticket.to_string(),
            other => panic!("expected a ticket, got {other:?}"),
        }
    }
}

impl Backend {
    pub async fn start() -> Self {
        Self::start_with(false, Plugins::fixed(Handlers::built_in()), |_| {}).await
    }

    /// A backend and viewers whose sockets hold only a few kilobytes, so a viewer that stops reading backs the
    /// backend up after a few frames.
    pub async fn start_squeezed() -> Self {
        Self::start_with(true, Plugins::fixed(Handlers::built_in()), |_| {}).await
    }

    /// A backend whose catalogs are `catalogs`, which the test polls itself with `rescan`.
    pub async fn start_with_catalogs(catalogs: catalogs::Library) -> Self {
        Self::start_with(false, Plugins::fixed(Handlers::built_in()), |state| {
            state.catalogs = Arc::new(catalogs);
        })
        .await
    }

    /// A backend that decodes with `plugins`' handlers, over the state `configure` adjusts.
    pub async fn start_with(
        squeezed: bool,
        plugins: Arc<Plugins>,
        configure: impl FnOnce(&mut AppState),
    ) -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let registry = Registry::new(CachePool::new(256 * MIB));
        let cache = folder.path().join("cache");
        let videos = Arc::new(VideoSources::new(registry, &cache));
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let mut state = AppState::new(
            Arc::new(settings),
            Policy::Local,
            videos.clone(),
            stopping,
            plugins,
            &cache,
        );
        configure(&mut state);
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

    /// A MOT ground-truth file with `rows` (`<folder>/SEQ/gt/gt.txt`), measured in [`MOT_SIZE`].
    pub fn mot_file(&self, rows: &str) -> PathBuf {
        let sequence = self.folder.path().join("SEQ");
        std::fs::create_dir_all(sequence.join("gt")).unwrap();
        let gt = sequence.join("gt").join("gt.txt");
        std::fs::write(&gt, rows).unwrap();
        gt
    }

    /// Adds the video to a new session of its own, with a viewer requested, and returns it once it resolved.
    pub async fn add(&self, path: &Path) -> Added {
        self.add_with_overlays(path, &[]).await
    }

    /// Like [`Backend::add`], with one lane per overlay file, each read by the decoder its extension names. A `.txt`
    /// file is MOT, whose decoder needs the image size its boxes are measured in: [`MOT_SIZE`].
    pub async fn add_with_overlays(&self, path: &Path, overlays: &[PathBuf]) -> Added {
        let overlays: Vec<_> = overlays
            .iter()
            .map(|path| NewOverlay {
                path: path.clone(),
                handler: None,
                label: None,
                options: if path.extension().is_some_and(|extension| extension == "txt") {
                    mot_options(MOT_SIZE)
                } else {
                    Settings::new()
                },
            })
            .collect();
        let item =
            workspace::intake::video(path, &overlays, &self.state.plugins.handlers()).unwrap();
        self.add_item(item).await
    }

    /// Adds a playlist of `resolver` with `settings`, as the dialog would, and returns it once it resolved.
    pub async fn add_playlist(&self, resolver: &str, settings: Settings) -> Added {
        let item = workspace::intake::playlist(resolver, settings, &Handlers::built_in()).unwrap();
        self.add_item(item).await
    }

    pub async fn add_item(&self, item: WorkspaceItem) -> Added {
        let owner = self
            .state
            .sessions
            .create(true, true, CursorOptions::default())
            .unwrap();
        let id = item.id;
        let added = DocCommand::AddItems {
            items: vec![item],
            open: true,
        };
        owner.command(Command::Content(added)).unwrap();
        let (key, content) = tokio::time::timeout(WAIT, async {
            loop {
                let state = owner.state();
                match state.doc.workspace.resolutions.get(&id) {
                    Some(Resolution::Resolved {
                        content,
                        generation,
                    }) => {
                        let entry = content.entries()[0].id.clone();
                        let key = ContentRef {
                            content: content.id().clone(),
                            generation: *generation,
                            entry,
                            viewer: ViewerId(1),
                        };
                        break (key, content.clone());
                    }
                    Some(Resolution::Failed(error)) => panic!("the item failed: {error}"),
                    _ => tokio::time::sleep(Duration::from_millis(10)).await,
                }
            }
        })
        .await
        .expect("the item resolves");
        // The owner's window shows the item in viewer 1, which is what a watcher that follows streams.
        let viewer = ViewerView {
            item: id,
            pinned: false,
            clock: None,
            entry: None,
        };
        let showing = View {
            active: Some(key.viewer),
            viewers: [(key.viewer, viewer)].into(),
            layout: Some(Layout::Pane { viewer: key.viewer }),
            ..View::default()
        };
        owner.view(showing, 1).unwrap();
        Added {
            owner,
            key,
            content,
        }
    }

    pub async fn viewer(&self) -> Viewer {
        let url = format!("ws://{}/api/playback", self.address);
        if !self.squeezed {
            let (socket, _) = tokio_tungstenite::connect_async(url).await.unwrap();
            return Viewer {
                socket,
                close_code: None,
                cached: Vec::new(),
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
            cached: Vec::new(),
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

/// What `Opened` says about a video, and the Timeline that follows it.
pub struct Described {
    pub frame_count: u64,
    pub frame_bytes: u64,
    pub duration_us: i64,
    pub times: Vec<f64>,
    pub lanes: Vec<LaneInfo>,
}

pub struct Viewer {
    pub socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
    /// The code of the close frame the backend ended the socket with, once it did.
    pub close_code: Option<u16>,
    /// The backend's latest report of the frames it holds decoded, as inclusive runs.
    pub cached: Vec<[u64; 2]>,
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
            data_bytes: SAMPLE_BUDGET,
        })
        .await;
    }

    /// Asks for the frames in the window and the lanes' data for the same frames.
    pub async fn want(&mut self, playhead: u64, ahead: u32, behind: u32, direction: Direction) {
        self.want_data(playhead, (ahead, behind), (ahead, behind), direction)
            .await;
    }

    /// Asks for the frames in the first window (`ahead`, `behind`) and the data in the second.
    pub async fn want_data(
        &mut self,
        playhead: u64,
        (ahead, behind): (u32, u32),
        (data_ahead, data_behind): (u32, u32),
        direction: Direction,
    ) {
        self.send(&ClientMessage::Want(Want {
            playhead,
            ahead,
            behind,
            direction,
            data_ahead,
            data_behind,
        }))
        .await;
    }

    /// The next thing the backend sent, but for the reports of its cache, which come at any time: the latest is
    /// kept for [`cached_covering`](Self::cached_covering). Fails if nothing comes.
    pub async fn next(&mut self) -> Event {
        loop {
            match self.read().await {
                Event::Message(ServerMessage::Cached { ranges }) => self.cached = ranges,
                event => return event,
            }
        }
    }

    /// Waits until the backend's latest report of its cache has a run covering all of `frames`, whether the report
    /// came before or comes now; fails if none does.
    pub async fn cached_covering(&mut self, frames: RangeInclusive<u64>) {
        while !self
            .cached
            .iter()
            .any(|&[first, last]| first <= *frames.start() && last >= *frames.end())
        {
            if let Event::Message(ServerMessage::Cached { ranges }) = self.read().await {
                self.cached = ranges;
            }
        }
    }

    async fn read(&mut self) -> Event {
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
        let (frame_count, frame_bytes, duration_us, lanes) = loop {
            match self.next().await {
                Event::Message(ServerMessage::Indexing { progress: value }) => progress.push(value),
                Event::Message(ServerMessage::Opened {
                    protocol,
                    frame_count,
                    duration_us,
                    frame_bytes,
                    lanes,
                    ..
                }) => {
                    assert_eq!(protocol, PLAYBACK_PROTOCOL);
                    break (frame_count, frame_bytes, duration_us, lanes);
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
            lanes,
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
