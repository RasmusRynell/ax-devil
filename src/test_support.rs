//! Shared by the route tests: the app over a private settings file.

use crate::{AppState, access::Policy, app, network::Network, plugins::Plugins};
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderValue, Request, StatusCode, header},
    response::Response,
};
use catalogs::Library;
use handlers::Handlers;
use std::{path::Path, sync::Arc};
use tokio::sync::mpsc;
use tower::ServiceExt;

/// A network whose scans find nothing, so that no test probes a real network.
pub fn idle_network() -> Arc<Network> {
    Network::with_scanner(|_| mpsc::channel(1).1)
}

/// The app, kept with the temporary folder that holds its settings and caches. Must be created inside a Tokio runtime.
pub struct TestApp {
    router: Router,
    catalogs: Arc<Library>,
    /// The state the router runs over, to reach what a request cannot, such as a live session.
    pub state: AppState,
    /// Keeps the app from seeing its stop signal; sending true stops it.
    pub _stop: tokio::sync::watch::Sender<bool>,
    pub _folder: tempfile::TempDir,
}

impl TestApp {
    pub fn new() -> Self {
        Self::with(Handlers::built_in())
    }

    /// The app over exactly these handlers.
    pub fn with(handlers: Handlers) -> Self {
        Self::over(|_| Plugins::fixed(handlers))
    }

    /// The app over the plugins `plugins` makes, given the app's temporary folder to keep a registry in.
    pub fn over(plugins: impl FnOnce(&Path) -> Arc<Plugins>) -> Self {
        Self::build(plugins, idle_network(), false)
    }

    /// The app over built-in handlers and a network that scans with `network`.
    pub fn scanning(network: Arc<Network>) -> Self {
        Self::build(|_| Plugins::fixed(Handlers::built_in()), network, false)
    }

    /// The app over built-in handlers and a library with an empty catalog folder of its own.
    pub fn with_catalog_folder() -> Self {
        Self::build(
            |_| Plugins::fixed(Handlers::built_in()),
            idle_network(),
            true,
        )
    }

    /// The library the app serves catalogs from.
    pub fn catalogs(&self) -> &Library {
        &self.catalogs
    }

    fn build(
        plugins: impl FnOnce(&Path) -> Arc<Plugins>,
        network: Arc<Network>,
        catalog_folder: bool,
    ) -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let cache = folder.path().join("cache");
        let videos = crate::video_cache::start(&settings, &cache);
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let mut state = AppState::new(
            Arc::new(settings),
            Policy::Local,
            videos,
            stopping,
            plugins(folder.path()),
            &cache,
        );
        if catalog_folder {
            let catalogs = folder.path().join("catalogs");
            std::fs::create_dir(&catalogs).unwrap();
            state = state.with_catalogs(Library::with_folder(catalogs));
        }
        let state = state
            .with_network(network)
            .with_windows(folder.path().join("windows.json"));
        let catalogs = Arc::clone(&state.catalogs);
        let router = app(state.clone());
        Self {
            router,
            catalogs,
            state,
            _stop: stop,
            _folder: folder,
        }
    }

    /// Sends one request, as a page of the local backend would, and returns the response unread.
    pub async fn open(&self, mut request: Request<Body>) -> Response {
        request
            .headers_mut()
            .entry(header::HOST)
            .or_insert(HeaderValue::from_static("127.0.0.1"));
        self.router.clone().oneshot(request).await.unwrap()
    }

    /// Sends one request and reads the whole response.
    pub async fn send(&self, request: Request<Body>) -> (StatusCode, Vec<u8>) {
        let response = self.open(request).await;
        let status = response.status();
        (
            status,
            to_bytes(response.into_body(), 1 << 20)
                .await
                .unwrap()
                .to_vec(),
        )
    }
}

/// Parses a response body as JSON.
pub fn json(bytes: &[u8]) -> serde_json::Value {
    serde_json::from_slice(bytes).unwrap()
}
