//! Shared by the route tests: the app over a private settings file and an empty workspace.

use crate::{AppState, app};
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use std::sync::Arc;
use tower::ServiceExt;

/// The app, with the temporary folder that holds its settings and caches. Must be created inside a Tokio runtime.
pub struct TestApp {
    pub router: Router,
    /// Keeps the app from seeing its stop signal; sending true stops it.
    pub _stop: tokio::sync::watch::Sender<bool>,
    pub folder: tempfile::TempDir,
}

impl TestApp {
    pub fn new() -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let videos = crate::video_cache::start(&settings, &folder.path().join("cache"));
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let router = app(AppState {
            settings: Arc::new(settings),
            store: workspace_store::Store::new(tokio::runtime::Handle::current()),
            windows: Arc::new(crate::Windows::new(folder.path().join("windows.json"))),
            videos,
            stopping,
        });
        Self {
            router,
            _stop: stop,
            folder,
        }
    }

    /// Sends one request and reads the whole response.
    pub async fn send(&self, request: Request<Body>) -> (StatusCode, Vec<u8>) {
        let response = self.router.clone().oneshot(request).await.unwrap();
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
