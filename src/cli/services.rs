//! What both backends start from: the services their routes are adapters over, the stop signal, and the quit.

use super::catalog_folder;
use crate::paths::Paths;
use ax_devil::{
    AppState,
    access::Policy,
    live_sources::LiveSources,
    network::{self, Network},
    plugins::Plugins,
    session::{Launch, LaunchQueue, WorkspaceFiles, WorkspaceStorage},
    video_cache,
    video_sources::VideoSources,
};
use axum::Router;
use catalogs::Library;
use std::{
    error::Error,
    future::IntoFuture,
    net::SocketAddr,
    path::PathBuf,
    process::ExitCode,
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    task::{Context, Poll, Waker},
    time::Duration,
};
use tokio::sync::watch;
use workspace_files::{Recents, RecoveryFolder};

/// How long open connections, such as the sockets, get to close after Ctrl+C before the backend exits anyway.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// How long a quit waits for the live connections to close, which removes the temporary MQTT publishers from the
/// cameras.
const LIVE_SHUTDOWN: Duration = Duration::from_secs(3);

/// The exit status of a run that Ctrl+C ended: 128 plus SIGINT, as a shell reports an interrupted program.
const INTERRUPTED: u8 = 130;

/// The source of Ctrl+C events.
#[cfg(unix)]
type Listener = tokio::signal::unix::Signal;
/// The source of Ctrl+C events.
#[cfg(windows)]
type Listener = tokio::signal::windows::CtrlC;

/// Ctrl+C, watched from before anything starts to the end of the run. It owns the stop signal, so an interruption
/// is remembered whatever else happens: a startup error does not hide it.
pub(super) struct Interrupt {
    pub(super) stop: watch::Sender<bool>,
    events: Arc<Events>,
}

/// The Ctrl+C events not yet taken, and whether one was.
struct Events {
    listener: Mutex<Option<Listener>>,
    happened: AtomicBool,
}

impl Events {
    /// Takes the next event once the first was taken: the second Ctrl+C.
    fn again(&self, cx: &mut Context) -> Poll<()> {
        let mut listener = self.listener.lock().unwrap_or_else(PoisonError::into_inner);
        match listener.as_mut().map(|listener| listener.poll_recv(cx)) {
            Some(Poll::Ready(Some(()))) => Poll::Ready(()),
            _ => Poll::Pending,
        }
    }

    /// Takes a pending event. `happened` is set while the listener is locked, so whoever takes the lock afterwards
    /// sees either the flag or the event, never neither.
    fn poll(&self, cx: &mut Context) -> Poll<()> {
        let mut listener = self.listener.lock().unwrap_or_else(PoisonError::into_inner);
        if self.happened.load(Ordering::Acquire) {
            return Poll::Ready(());
        }
        if let Some(Poll::Ready(Some(()))) =
            listener.as_mut().map(|listener| listener.poll_recv(cx))
        {
            self.happened.store(true, Ordering::Release);
            return Poll::Ready(());
        }
        Poll::Pending
    }
}

impl Interrupt {
    /// Installs the Ctrl+C handler before returning, and turns the stop signal on when Ctrl+C comes. A second Ctrl+C
    /// ends the process at once. Needs a runtime.
    pub(super) fn watch() -> Self {
        Self::watch_then(|| {
            // A stalled startup step or shutdown never reaches the cleanup the first Ctrl+C asked for, and Python's
            // KeyboardInterrupt ends such a startup at once too. Nothing is left to clean that exiting would skip.
            #[allow(clippy::exit)]
            std::process::exit(i32::from(INTERRUPTED))
        })
    }

    /// Like [`Interrupt::watch`], with `second` run on the second Ctrl+C instead of ending the process.
    pub(super) fn watch_then(second: impl FnOnce() + Send + 'static) -> Self {
        let interrupt = Self::listen();
        let (stop, events) = (interrupt.stop.clone(), Arc::clone(&interrupt.events));
        tokio::spawn(async move {
            std::future::poll_fn(|cx| events.poll(cx)).await;
            stop.send_replace(true);
            std::future::poll_fn(|cx| events.again(cx)).await;
            second();
        });
        interrupt
    }

    /// Installs the handler and nothing else: [`Interrupt::status`] still sees Ctrl+C, but nothing stops.
    pub(super) fn listen() -> Self {
        #[cfg(unix)]
        let listener = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::interrupt());
        #[cfg(windows)]
        let listener = tokio::signal::windows::ctrl_c();
        Self::with(listener.ok())
    }

    /// Ignores Ctrl+C, for the launcher whose child gets it from the terminal as well.
    pub(super) fn ignore() -> Self {
        Self::with(None)
    }

    fn with(listener: Option<Listener>) -> Self {
        Self {
            stop: watch::channel(false).0,
            events: Arc::new(Events {
                listener: Mutex::new(listener),
                happened: AtomicBool::new(false),
            }),
        }
    }

    /// The exit status of an interrupted run, once Ctrl+C has been pressed. The answer does not wait for the task that
    /// turns the stop signal on to run. Call it once the run is over: it takes the event the task would wait for.
    pub(super) fn status(&self) -> Option<ExitCode> {
        let seen = self
            .events
            .poll(&mut Context::from_waker(Waker::noop()))
            .is_ready();
        seen.then(|| {
            self.stop.send_replace(true);
            ExitCode::from(INTERRUPTED)
        })
    }
}

/// Set to `1` by the desktop shell: the backend then stops when its standard input closes, so it never outlives the
/// shell, even one that crashed. Unset, a closed standard input (a service, a container) changes nothing.
const EXIT_ON_STDIN_CLOSE_VARIABLE: &str = "AX_DEVIL_EXIT_ON_STDIN_CLOSE";

/// What both backends start from: the services their routes are adapters over, and the stop signal.
pub(super) struct Services {
    pub(super) settings: Arc<settings::Settings>,
    catalogs: Library,
    launch: Arc<LaunchQueue>,
    files: Arc<WorkspaceFiles>,
    videos: Arc<VideoSources>,
    plugins: Arc<Plugins>,
    live: Arc<LiveSources>,
    network: Arc<Network>,
    cache_dir: PathBuf,
    stop: watch::Sender<bool>,
}

impl Services {
    /// Loads the settings, starts the video cache and queues `launch` for the first Local session. Ctrl+C turns the
    /// stop signal on, and so does the end of the standard input when [`EXIT_ON_STDIN_CLOSE_VARIABLE`] asks for it.
    /// `interrupt` owns the stop signal, which is already on when Ctrl+C came during startup.
    pub(super) fn start(
        paths: &Paths,
        plugins: Arc<Plugins>,
        launch: Option<Launch>,
        interrupt: &Interrupt,
    ) -> Result<Self, Box<dyn Error>> {
        let stop = interrupt.stop.clone();
        let settings = Arc::new(settings::Settings::load(&paths.config_file)?);
        tracing::info!("Loaded settings from {}", paths.config_file.display());
        let cache_dir = paths.cache_dir(settings.state().folder("storage.cache_dir"));
        tracing::info!("Caching in {}", cache_dir.display());
        let videos = video_cache::start(&settings, &cache_dir);
        let catalog_dir = catalog_folder(paths, &settings);
        tracing::info!("Reading user catalogs from {}", catalog_dir.display());
        let catalogs = Library::with_folder(catalog_dir);
        let network = Network::new();
        network::scan_on_startup(&network, &settings.state());
        tokio::spawn(Arc::clone(&plugins).watch(stop.subscribe()));
        if std::env::var_os(EXIT_ON_STDIN_CLOSE_VARIABLE).is_some_and(|value| value == "1") {
            let stop_on_eof = stop.clone();
            // A plain thread: reading standard input blocks, and the thread may stay blocked until the process exits.
            std::thread::spawn(move || {
                let _ = std::io::copy(&mut std::io::stdin().lock(), &mut std::io::sink());
                stop_on_eof.send_replace(true);
            });
        }
        let live = Arc::new(LiveSources::cameras(Arc::clone(&settings)));
        Ok(Self {
            settings,
            catalogs,
            launch: Arc::new(LaunchQueue::new(launch)),
            files: Arc::new(WorkspaceFiles::new(Some(WorkspaceStorage {
                recents: Recents::new(paths.recent_file.clone()),
                recovery: RecoveryFolder::new(paths.recovery_dir.clone()),
            }))),
            videos,
            plugins,
            live,
            network,
            cache_dir,
            stop,
        })
    }

    /// The state of one listener over these services.
    pub(super) fn state(&self, policy: Policy) -> AppState {
        AppState::new(
            Arc::clone(&self.settings),
            policy,
            Arc::clone(&self.videos),
            self.stop.subscribe(),
            Arc::clone(&self.plugins),
            &self.cache_dir,
        )
        .with_live(Arc::clone(&self.live))
        .with_network(Arc::clone(&self.network))
        .with_launch(Arc::clone(&self.launch))
        .with_files(Arc::clone(&self.files))
        .with_catalogs(self.catalogs.clone())
    }

    /// Serves `router` until the stop signal, giving connections [`SHUTDOWN_GRACE`] to close, then ends every viewer,
    /// closes the live connections and waits once for the decode threads to finish their frame and exit.
    pub(super) async fn serve(
        self,
        listener: tokio::net::TcpListener,
        router: Router,
    ) -> Result<(), Box<dyn Error>> {
        let (mut graceful, mut expired) = (self.stop.subscribe(), self.stop.subscribe());
        let server = axum::serve(
            listener,
            router.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async move {
            let _ = graceful.wait_for(|stop| *stop).await;
        });
        let served = tokio::select! {
            served = server.into_future() => served,
            () = async {
                let _ = expired.wait_for(|stop| *stop).await;
                tokio::time::sleep(SHUTDOWN_GRACE).await;
            } => {
                tracing::warn!("Closing connections that stayed open after Ctrl+C");
                Ok(())
            }
        };
        // Whatever ended the server, every viewer ends.
        self.stop.send_replace(true);
        self.network.cancel();
        self.files.flush_all().await;
        self.live.shutdown(LIVE_SHUTDOWN).await;
        let videos = self.videos;
        let retired =
            tokio::task::spawn_blocking(move || videos.registry().wait_retired(SHUTDOWN_GRACE))
                .await;
        if !matches!(retired, Ok(true)) {
            tracing::warn!("Some video decoders did not stop in time");
        }
        Ok(served?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ax_devil::live_sources::{Connector, Inputs, Transport, UnitDecoder};
    use camera::{Status, VideoCodec};
    use foundation::{ErrorCode, ItemId, UserError};
    use handlers::Handlers;
    use overlay_sync::MatchPolicy;
    use std::sync::atomic::{AtomicBool, Ordering};
    use workspace::{
        ContentId, LIVE_SLOT, LiveOverlay, LiveVideoContent, Protocol, ResolvedCamera,
    };

    /// A camera that connects to nothing and says when its connection was closed.
    struct Closing(Arc<AtomicBool>);

    impl Connector for Closing {
        fn video(&self, _camera: &ResolvedCamera, _metadata: bool) -> Transport {
            let closed = Arc::clone(&self.0);
            let (_, status) = watch::channel(Status::Connecting);
            Transport::new(
                Inputs {
                    video: None,
                    docs: None,
                    status,
                },
                async move { closed.store(true, Ordering::Release) },
            )
        }

        fn mqtt(
            &self,
            _: &ResolvedCamera,
            _: &ax_devil::live_sources::Broker,
            _: &str,
        ) -> Transport {
            unreachable!("the stream has no MQTT overlay")
        }

        fn datahub(&self, _: &ResolvedCamera, _: &str, _: u32) -> Transport {
            unreachable!("the stream has no DataHub overlay")
        }

        fn decoder(&self, _: VideoCodec, _: &[u8]) -> Result<Box<dyn UnitDecoder>, UserError> {
            Err(UserError::new(ErrorCode::Internal, "no codec in this test"))
        }
    }

    #[tokio::test]
    async fn quitting_closes_the_live_connections_a_viewer_still_holds() {
        let folder = tempfile::tempdir().unwrap();
        let settings =
            Arc::new(settings::Settings::load(&folder.path().join("settings.json")).unwrap());
        let cache_dir = folder.path().join("cache");
        let closed = Arc::new(AtomicBool::new(false));
        let live = LiveSources::new(Arc::new(Closing(Arc::clone(&closed))));
        let item = ItemId::generate();
        let content = LiveVideoContent {
            item_id: item,
            id: ContentId::of_item(&item),
            label: "cam".into(),
            camera: ResolvedCamera {
                host: "cam".into(),
                protocol: Protocol::Http,
                port: 80,
                rtsp_port: 554,
                rtsp_route: workspace::RtspRoute::Direct,
                head: 1,
                resolution: None,
                url: None,
                login: None,
            },
            overlay: LiveOverlay::None,
            slot: LIVE_SLOT.to_owned(),
        };
        let _viewer = live
            .open(&content, &Handlers::built_in(), MatchPolicy::default())
            .await
            .unwrap();
        assert!(!closed.load(Ordering::Acquire));
        let (stop, _) = watch::channel(true);
        let services = Services {
            videos: video_cache::start(&settings, &cache_dir),
            settings,
            catalogs: Library::built_in(),
            launch: Arc::new(LaunchQueue::new(None)),
            files: Arc::new(WorkspaceFiles::new(None)),
            plugins: Plugins::fixed(Handlers::built_in()),
            live: Arc::new(live),
            network: Network::new(),
            cache_dir,
            stop,
        };
        let router = ax_devil::app(services.state(Policy::Local));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();

        services.serve(listener, router).await.unwrap();

        assert!(closed.load(Ordering::Acquire));
    }
}
