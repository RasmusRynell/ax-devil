//! The ax-devil backend: HTTP routes for the frontend and, in release builds, the embedded frontend itself.

pub mod access;
mod routes;
pub mod runtime;
pub mod session;
pub mod share;
#[cfg(test)]
mod test_support;
pub mod video_cache;
pub mod video_sources;

use access::{Admission, Limits, Policy, Principal};
use api_types::{API_VERSION, ApiError, AppInfo, SHELL_API, Settings as SettingsBody};
use axum::{
    Json, Router,
    extract::{FromRef, State},
    http::StatusCode,
    middleware::from_fn,
};
#[cfg(feature = "embed-assets")]
use axum::{
    http::{Uri, header},
    response::{IntoResponse, Response},
};
use routes::Listener;
use session::{GRACE, LaunchQueue, Sessions};
use sessions::session::OnRuntime;
use share::Hosting;
use std::sync::Arc;
use tokio::sync::watch;

#[cfg(feature = "embed-assets")]
#[derive(rust_embed::RustEmbed)]
#[folder = "frontend/dist/"]
// The frontend build creates the folder; checks that compile without it serve nothing.
#[allow_missing = true]
struct Assets;

/// What every route can reach: the services it is an adapter over, who is asking, the socket limits, and the sessions.
#[derive(Clone)]
pub struct AppState {
    /// The settings service; a route reads and writes only the scopes its caller is granted.
    pub settings: Arc<settings::Settings>,
    /// The open videos, which the playback socket draws its viewers from.
    pub videos: Arc<video_sources::VideoSources>,
    /// Turns true when the backend is stopping: long-lived connections, such as the playback and session sockets,
    /// end.
    pub stopping: watch::Receiver<bool>,
    /// How this listener decides who is asking.
    pub policy: Arc<Policy>,
    /// Counts the open sockets.
    pub admission: Arc<Admission>,
    /// Every session this backend holds, whichever listener its socket arrives on.
    pub sessions: Sessions,
    /// Items the first Local session takes when it starts.
    launch: Arc<LaunchQueue>,
    /// Where the join links of the sessions point.
    hosting: Arc<Hosting>,
}

impl AppState {
    /// State for one listener with the default socket limits, no launch items and no join links. Must be created
    /// inside a Tokio runtime, which resolves the sessions' items.
    pub fn new(
        settings: Arc<settings::Settings>,
        policy: Policy,
        videos: Arc<video_sources::VideoSources>,
        stopping: watch::Receiver<bool>,
    ) -> Self {
        let runtime = tokio::runtime::Handle::current();
        let limits = match policy {
            // Every client of the Local listener is this machine, so a per-address cap would only limit the person
            // using it: all their windows and viewers share one address.
            Policy::Local => Limits::behind_proxy(),
            Policy::Shared { .. } => Limits::default(),
        };
        Self {
            settings,
            videos,
            stopping,
            policy: Arc::new(policy),
            admission: Arc::new(Admission::new(limits)),
            sessions: Sessions::new(GRACE, Arc::new(OnRuntime(runtime))),
            launch: Arc::new(LaunchQueue::default()),
            hosting: Arc::new(Hosting::None),
        }
    }

    /// The same state with these socket limits.
    #[must_use]
    pub fn with_limits(mut self, limits: Limits) -> Self {
        self.admission = Arc::new(Admission::new(limits));
        self
    }

    /// The same state with `launch` as the items the first Local session takes.
    #[must_use]
    pub fn with_launch(mut self, launch: Arc<LaunchQueue>) -> Self {
        self.launch = launch;
        self
    }

    /// The same state with join links pointing at `hosting`.
    #[must_use]
    pub fn with_hosting(mut self, hosting: Hosting) -> Self {
        self.hosting = Arc::new(hosting);
        self
    }

    /// State for another listener of this backend: the same settings and sessions, its own policy and limits, and no
    /// join links of its own.
    pub(crate) fn shared_with(&self, policy: Policy) -> Self {
        Self {
            policy: Arc::new(policy),
            admission: Arc::new(Admission::new(Limits::default())),
            hosting: Arc::new(Hosting::None),
            ..self.clone()
        }
    }
}

impl FromRef<AppState> for Arc<Policy> {
    fn from_ref(state: &AppState) -> Self {
        Arc::clone(&state.policy)
    }
}

/// Builds the routes of the listener for other machines that the desktop opens while a session is shared: the
/// frontend, the build's identity, and the session and playback sockets, which admit join links only.
pub fn lan_app(state: AppState) -> Router {
    routes::mount(Listener::Lan, state).layer(from_fn(access::security_headers))
}

/// Builds the backend's routes. The Local policy answers only pages this backend served.
pub fn app(state: AppState) -> Router {
    let (listener, local) = match *state.policy {
        Policy::Local => (Listener::Local, true),
        Policy::Shared { .. } => (Listener::Server, false),
    };
    let mut router = routes::mount(listener, state);
    if local {
        router = router.layer(from_fn(access::local_guard));
    }
    // Outermost, so refusals carry the headers too.
    router.layer(from_fn(access::security_headers))
}

pub(crate) async fn get_settings(
    principal: Principal,
    State(state): State<AppState>,
) -> Json<SettingsBody> {
    Json(SettingsBody(
        state.settings.state().values(&principal.scopes()),
    ))
}

/// Replaces every setting the caller may change; a rejected or unsaved change leaves the stored state as it was.
pub(crate) async fn put_settings(
    principal: Principal,
    State(state): State<AppState>,
    Json(body): Json<SettingsBody>,
) -> Result<Json<SettingsBody>, (StatusCode, Json<ApiError>)> {
    let scopes = principal.scopes();
    if let Some(key) = body.0.keys().find(|key| {
        settings::TABLE
            .iter()
            .any(|setting| setting.key == key.as_str() && !scopes.contains(&setting.scope))
    }) {
        return Err(failure(
            StatusCode::FORBIDDEN,
            key,
            "this setting is not yours to change".into(),
        ));
    }
    let settings = state.settings;
    // The file write blocks, so it stays off the async workers.
    let saved = tokio::task::spawn_blocking(move || settings.replace(&scopes, &body.0))
        .await
        .map_err(|error| {
            failure(
                StatusCode::INTERNAL_SERVER_ERROR,
                "settings",
                error.to_string(),
            )
        })?;
    match saved {
        Ok(state) => Ok(Json(SettingsBody(state.values(&principal.scopes())))),
        Err(error) => {
            let status = if error.invalid {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                tracing::error!("Cannot save settings: {error}");
                StatusCode::INTERNAL_SERVER_ERROR
            };
            Err(failure(status, &error.location, error.reason))
        }
    }
}

pub(crate) fn failure(
    status: StatusCode,
    location: &str,
    reason: String,
) -> (StatusCode, Json<ApiError>) {
    (
        status,
        Json(ApiError {
            reason,
            location: location.to_owned(),
        }),
    )
}

pub(crate) async fn info(principal: Principal) -> Json<AppInfo> {
    Json(AppInfo {
        app: "ax-devil".into(),
        api: API_VERSION,
        version: env!("CARGO_PKG_VERSION").into(),
        development: !cfg!(feature = "embed-assets"),
        pid: std::process::id(),
        shell: SHELL_API,
        grants: principal.grants().to_vec(),
    })
}

#[cfg(feature = "embed-assets")]
pub(crate) async fn asset(uri: Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    match Assets::get(path) {
        Some(file) => (
            [(
                header::CONTENT_TYPE,
                mime_guess::from_path(path).first_or_octet_stream().as_ref(),
            )],
            file.data.to_vec(),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{TestApp, json};
    use axum::{body::Body, http::Request};

    async fn get(path: &str) -> (StatusCode, Vec<u8>) {
        let test = TestApp::new();
        test.send(Request::builder().uri(path).body(Body::empty()).unwrap())
            .await
    }

    fn put(body: &str) -> Request<Body> {
        Request::builder()
            .method("PUT")
            .uri("/api/settings")
            .header("host", "127.0.0.1")
            .header("content-type", "application/json")
            .body(Body::from(body.to_owned()))
            .unwrap()
    }

    #[tokio::test]
    async fn release_builds_serve_the_frontend_and_development_builds_leave_it_to_vite() {
        let (status, body) = get("/").await;
        if cfg!(feature = "embed-assets") {
            assert_eq!(status, StatusCode::OK);
            assert!(String::from_utf8_lossy(&body).contains("<div id=\"root\">"));
        } else {
            assert_eq!(status, StatusCode::NOT_FOUND);
        }
        assert_eq!(get("/missing.js").await.0, StatusCode::NOT_FOUND);
        assert_eq!(get("/api/missing").await.0, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn info_identifies_the_backend_and_its_build() {
        let (status, body) = get("/api/info").await;
        assert_eq!(status, StatusCode::OK);
        let info = json(&body);
        assert_eq!(info["app"], "ax-devil");
        assert_eq!(info["api"], API_VERSION);
        assert_eq!(info["development"], !cfg!(feature = "embed-assets"));
    }

    #[tokio::test]
    async fn settings_are_replaced_whole_and_a_rejected_change_leaves_them_as_they_were() {
        let test = TestApp::new();
        let all = |theme: &str| {
            format!(
                r#"{{"ui.theme":"{theme}","ui.quick_setup_done":true,"playback.video_cache":"auto"}}"#
            )
        };

        let (status, body) = test.send(put(&all("dark"))).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&body), json(all("dark").as_bytes()));

        let (status, body) = test.send(put(&all("neon"))).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "ui.theme");

        let (_, body) = test
            .send(
                Request::builder()
                    .uri("/api/settings")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(json(&body), json(all("dark").as_bytes()));
    }
}
