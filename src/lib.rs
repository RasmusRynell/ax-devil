//! The ax-devil backend: HTTP routes for the frontend and, in release builds, the embedded frontend itself.

pub mod access;
pub mod desktop;
pub mod disk_cache;
pub mod exports;
pub mod plugins;
pub mod render_host;
mod routes;
pub mod runtime;
pub mod session;
pub mod share;
#[cfg(test)]
mod test_support;
mod thumbnails;
pub mod video_cache;
pub mod video_sources;

use access::{Admission, Limits, Policy, Principal};
use api_types::{API_VERSION, ApiError, AppInfo, SHELL_API, SettingsChange, SettingsState};
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
use catalogs::Library;
use overlays::OverlayService;
use routes::Listener;
use scene_store::TrackRegistry;
use session::{GRACE, LaunchQueue, Sessions, WorkspaceFiles, WorkspaceStorage};
use sessions::session::OnRuntime;
use settings::Scope;
use share::Hosting;
use std::{collections::BTreeMap, path::Path, sync::Arc, time::Duration};
use tokio::{sync::watch, time::MissedTickBehavior};

pub use routes::window::Windows;

#[cfg(feature = "embed-assets")]
#[derive(rust_embed::RustEmbed)]
#[folder = "frontend/dist/"]
// The frontend build creates the folder; checks that compile without it serve nothing.
#[allow_missing = true]
struct Assets;

/// How often the user's catalog folder is looked at. A saved catalog reaches a lane after two of these, the second
/// being the one that found it unchanged.
const CATALOG_POLL: Duration = Duration::from_millis(500);

/// What every route can reach: the services it is an adapter over, who is asking, the socket limits, and the sessions.
#[derive(Clone)]
pub struct AppState {
    /// The settings service; a route reads and writes only the scopes its caller is granted.
    pub settings: Arc<settings::Settings>,
    /// Where the saved window positions are, when this backend keeps any; see [`AppState::with_windows`].
    windows: Option<Arc<Windows>>,
    /// The decoders and playlist resolvers the app knows, and the plugins that provide them.
    pub plugins: Arc<plugins::Plugins>,
    /// The open videos, which the playback socket draws its viewers from.
    pub videos: Arc<video_sources::VideoSources>,
    /// The cached first pictures of videos.
    pub(crate) thumbnails: Arc<thumbnails::Thumbnails>,
    /// Opens the overlays of viewers.
    pub overlays: Arc<OverlayService>,
    /// Runs the backend's one export at a time.
    pub exports: Arc<exports::ExportService>,
    /// The render catalogs, listed to every viewer and served on request; see [`AppState::with_catalogs`].
    pub catalogs: Arc<Library>,
    /// Turns true when the backend is stopping: long-lived connections, such as the playback and session sockets,
    /// end.
    pub stopping: watch::Receiver<bool>,
    /// How this listener decides who is asking.
    pub policy: Arc<Policy>,
    /// Counts the open sockets.
    pub admission: Arc<Admission>,
    /// Every session this backend holds, whichever listener its socket arrives on.
    pub sessions: Sessions,
    /// Playlist items resolved by the resolve route and waiting for `addResolved`.
    pub staged: Arc<routes::resolve::Staged>,
    /// Items the first Local session takes when it starts.
    launch: Arc<LaunchQueue>,
    /// Opens, saves and recovers the workspaces of the sessions; see [`AppState::with_files`].
    files: Arc<WorkspaceFiles>,
    /// Where the join links of the sessions point.
    hosting: Arc<Hosting>,
}

impl AppState {
    /// State for one listener with the default socket limits, no launch items and no join links. Decoded overlay
    /// tracks are cached under `cache_dir`. Must be created inside a Tokio runtime, which resolves the sessions'
    /// items.
    pub fn new(
        settings: Arc<settings::Settings>,
        policy: Policy,
        videos: Arc<video_sources::VideoSources>,
        stopping: watch::Receiver<bool>,
        plugins: Arc<plugins::Plugins>,
        cache_dir: &Path,
    ) -> Self {
        let runtime = tokio::runtime::Handle::current();
        let catalogs = Library::built_in();
        let tracks = TrackRegistry::new(plugins.set().decoder_lookup(), Some(cache_dir));
        let limits = match policy {
            // Every client of the Local listener is this machine, so a per-address cap would only limit the person
            // using it: all their windows and viewers share one address.
            Policy::Local => Limits::behind_proxy(),
            Policy::Shared { .. } => Limits::default(),
        };
        Self {
            settings,
            overlays: Arc::new(OverlayService::new(tracks)),
            exports: Arc::new(exports::ExportService::electron()),
            catalogs: Arc::new(catalogs),
            sessions: Sessions::new(
                GRACE,
                Arc::new(OnRuntime {
                    runtime,
                    handlers: plugins.set(),
                }),
            ),
            thumbnails: Arc::new(thumbnails::Thumbnails::new(
                cache_dir.join(thumbnails::FOLDER),
            )),
            plugins,
            videos,
            stopping,
            windows: None,
            policy: Arc::new(policy),
            admission: Arc::new(Admission::new(limits)),
            staged: Arc::default(),
            launch: Arc::new(LaunchQueue::default()),
            files: Arc::new(WorkspaceFiles::new(None)),
            hosting: Arc::new(Hosting::None),
        }
    }

    /// The same state with these socket limits.
    #[must_use]
    pub fn with_limits(mut self, limits: Limits) -> Self {
        self.admission = Arc::new(Admission::new(limits));
        self
    }

    /// The same state with `launch` as what the first Local session starts from.
    #[must_use]
    pub fn with_launch(mut self, launch: Arc<LaunchQueue>) -> Self {
        self.launch = launch;
        self
    }

    /// The same state with `files` running every session's workspace files. The default keeps no recovery copies and
    /// no recent workspaces.
    #[must_use]
    pub fn with_files(mut self, files: Arc<WorkspaceFiles>) -> Self {
        self.files = files;
        self
    }

    /// The same state keeping recovery copies and recent workspaces in `storage`.
    #[must_use]
    pub fn with_workspace_storage(self, storage: WorkspaceStorage) -> Self {
        self.with_files(Arc::new(WorkspaceFiles::new(Some(storage))))
    }

    /// The same state with window positions saved in `file`, which need not exist yet.
    #[must_use]
    pub fn with_windows(mut self, file: std::path::PathBuf) -> Self {
        self.windows = Some(Arc::new(Windows::new(file)));
        self
    }

    /// The same state serving `catalogs`, whose folder is polled until the backend stops. Must be called inside a
    /// Tokio runtime.
    #[must_use]
    pub fn with_catalogs(mut self, catalogs: Library) -> Self {
        let polled = catalogs.clone();
        let mut stopping = self.stopping.clone();
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(CATALOG_POLL);
            tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    _ = tick.tick() => {
                        let library = polled.clone();
                        // Reading the folder blocks.
                        let _ = tokio::task::spawn_blocking(move || library.rescan()).await;
                    }
                    () = async { let _ = stopping.wait_for(|stopping| *stopping).await; } => return,
                }
            }
        });
        self.catalogs = Arc::new(catalogs);
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

/// What `principal` may read of the settings: its scopes' values, the shortcuts when it holds the `preferences`
/// grant.
fn snapshot(state: &settings::State, principal: Principal) -> SettingsState {
    let scopes = principal.scopes();
    let shortcuts = if scopes.contains(&Scope::Personal) {
        state
            .shortcuts()
            .into_iter()
            .map(|(id, key)| (id.to_owned(), key))
            .collect()
    } else {
        BTreeMap::new()
    };
    SettingsState {
        values: state.values(&scopes),
        shortcuts,
    }
}

pub(crate) async fn get_settings(
    principal: Principal,
    State(AppState { settings, .. }): State<AppState>,
) -> Json<SettingsState> {
    Json(snapshot(&settings.state(), principal))
}

/// Replaces every setting and shortcut the caller may change; a rejected or unsaved change leaves the stored state
/// as it was.
pub(crate) async fn put_settings(
    principal: Principal,
    State(AppState { settings, .. }): State<AppState>,
    Json(body): Json<SettingsChange>,
) -> Result<Json<SettingsState>, (StatusCode, Json<ApiError>)> {
    let scopes = principal.scopes();
    let not_yours = |location: &str| {
        failure(
            StatusCode::FORBIDDEN,
            location,
            "this setting is not yours to change".into(),
        )
    };
    if let Some(key) = body.values.keys().find(|key| {
        settings::TABLE
            .iter()
            .any(|setting| setting.key == key.as_str() && !scopes.contains(&setting.scope))
    }) {
        return Err(not_yours(key));
    }
    if !scopes.contains(&Scope::Personal)
        && let Some(id) = body.shortcuts.keys().next()
    {
        return Err(not_yours(&format!("shortcuts.{id}")));
    }
    let change = settings::Change {
        values: body.values,
        shortcuts: body.shortcuts,
    };
    // The file write blocks, so it stays off the async workers.
    let saved = tokio::task::spawn_blocking(move || {
        settings
            .replace(&scopes, &change)
            .map(|state| snapshot(&state, principal))
    })
    .await
    .map_err(|error| {
        failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            "settings",
            error.to_string(),
        )
    })?;
    saved.map(Json).map_err(|error| {
        let status = if error.invalid {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            tracing::error!("Cannot save settings: {error}");
            StatusCode::INTERNAL_SERVER_ERROR
        };
        failure(status, &error.location, error.reason)
    })
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

    fn get_settings_request() -> Request<Body> {
        Request::builder()
            .uri("/api/settings")
            .body(Body::empty())
            .unwrap()
    }

    #[tokio::test]
    async fn settings_are_replaced_whole_and_a_rejected_change_leaves_them_as_they_were() {
        let test = TestApp::new();
        let (_, body) = test.send(get_settings_request()).await;
        let mut change = json(&body);
        assert_eq!(change["shortcuts"]["save-workspace"], "Ctrl+S");
        change["values"]["ui.theme"] = "dark".into();
        change["shortcuts"]["save-workspace"] = "Ctrl+Alt+S".into();
        change["shortcuts"]["exit"] = serde_json::Value::Null;
        let body = change.to_string();

        let (status, saved) = test.send(put(&body)).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&saved)["values"]["ui.theme"], "dark");
        let (_, read) = test.send(get_settings_request()).await;
        assert_eq!(json(&read), json(&saved));
        assert_eq!(json(&read)["shortcuts"]["exit"], serde_json::Value::Null);

        let bad_theme = body.replace("\"dark\"", "\"neon\"");
        let (status, error) = test.send(put(&bad_theme)).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&error)["location"], "ui.theme");
        let taken = body.replace("\"Ctrl+Alt+S\"", "\"Ctrl+O\"");
        let (status, error) = test.send(put(&taken)).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&error)["location"], "shortcuts.save-workspace");

        let (_, after) = test.send(get_settings_request()).await;
        assert_eq!(json(&after), json(&saved));
    }

    #[tokio::test]
    async fn a_window_is_saved_beside_the_others_and_a_corrupt_file_is_no_window() {
        let test = TestApp::new();
        let window = |method: &str, name: &str, body: &str| {
            Request::builder()
                .method(method)
                .uri(format!("/api/windows/{name}"))
                .header("content-type", "application/json")
                .body(Body::from(body.to_owned()))
                .unwrap()
        };
        let main = r#"{"x":-20,"y":30,"width":1280,"height":800,"maximized":true}"#;

        assert_eq!(
            test.send(window("GET", "main", "")).await.0,
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            test.send(window("PUT", "main", main)).await.0,
            StatusCode::NO_CONTENT
        );
        let other = r#"{"x":0,"y":0,"width":300,"height":200,"maximized":false}"#;
        assert_eq!(
            test.send(window("PUT", "tools-panel", other)).await.0,
            StatusCode::NO_CONTENT
        );
        let (status, body) = test.send(window("GET", "main", "")).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&body), json(main.as_bytes()));
        assert_eq!(
            test.send(window("PUT", "Main_1", main)).await.0,
            StatusCode::UNPROCESSABLE_ENTITY
        );

        std::fs::write(test._folder.path().join("windows.json"), "{ not json").unwrap();
        assert_eq!(
            test.send(window("GET", "main", "")).await.0,
            StatusCode::NO_CONTENT
        );
    }

    #[tokio::test]
    async fn catalogs_are_polled_in_the_background_so_a_file_that_appears_is_listed() {
        let folder = tempfile::tempdir().unwrap();
        let catalog_dir = folder.path().join("catalogs");
        std::fs::create_dir(&catalog_dir).unwrap();
        let library = Library::with_folder(catalog_dir.clone());
        let settings = Arc::new(settings::Settings::load(&folder.path().join("s.json")).unwrap());
        let cache = folder.path().join("cache");
        let videos = crate::video_cache::start(&settings, &cache);
        let (_stop, stopping) = watch::channel(false);
        let state = AppState::new(
            settings,
            Policy::Local,
            videos,
            stopping,
            plugins::Plugins::fixed(handlers::Handlers::built_in()),
            &cache,
        )
        .with_catalogs(library.clone());
        let mut changes = library.changes();

        std::fs::write(catalog_dir.join("night.json"), "{}").unwrap();

        tokio::time::timeout(Duration::from_secs(10), changes.changed())
            .await
            .expect("the poller finds the file")
            .unwrap();
        assert!(
            state
                .catalogs
                .documents()
                .iter()
                .any(|info| info.id.to_string() == "user:night.json")
        );
    }
}
