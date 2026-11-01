//! Shared by the route tests: the app over a private settings file.

use crate::{AppState, access::Policy, app};
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderValue, Request, StatusCode, header},
    response::Response,
};
use std::sync::Arc;
use tower::ServiceExt;

/// The app, kept with the temporary folder that holds its settings and caches. Must be created inside a Tokio runtime.
pub struct TestApp {
    router: Router,
    /// Keeps the app from seeing its stop signal; sending true stops it.
    pub _stop: tokio::sync::watch::Sender<bool>,
    pub _folder: tempfile::TempDir,
}

impl TestApp {
    pub fn new() -> Self {
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let videos = crate::video_cache::start(&settings, &folder.path().join("cache"));
        let (stop, stopping) = tokio::sync::watch::channel(false);
        let router = app(AppState::new(
            Arc::new(settings),
            Policy::Local,
            videos,
            stopping,
        ));
        Self {
            router,
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
