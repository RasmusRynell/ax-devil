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
    error::Error, future::IntoFuture, net::SocketAddr, path::PathBuf, sync::Arc, time::Duration,
};
use tokio::sync::watch;
use workspace_files::{Recents, RecoveryFolder};

/// How long open connections, such as the sockets, get to close after Ctrl+C before the backend exits anyway.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(2);

/// How long a quit waits for the live connections to close, which removes the temporary MQTT publishers from the
/// cameras.
const LIVE_SHUTDOWN: Duration = Duration::from_secs(3);

/// Set to `1` by the desktop shell: the backend then stops when its standard input closes, so it never outlives the
/// shell, even one that crashed. Unset, a closed standard input (a service, a container) changes nothing.
const EXIT_ON_STDIN_CLOSE_VARIABLE: &str = "AX_DEVIL_EXIT_ON_STDIN_CLOSE";

/// What both backends start from: the services their routes are adapters over, and the stop signal.
pub(super) struct Services {
    settings: Arc<settings::Settings>,
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
    pub(super) fn start(
        paths: &Paths,
        plugins: Arc<Plugins>,
        launch: Option<Launch>,
    ) -> Result<Self, Box<dyn Error>> {
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
        let (stop, _) = watch::channel(false);
        tokio::spawn(Arc::clone(&plugins).watch(stop.subscribe()));
        let stop_on_ctrl_c = stop.clone();
        tokio::spawn(async move {
            let _ = tokio::signal::ctrl_c().await;
            stop_on_ctrl_c.send_replace(true);
        });
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
