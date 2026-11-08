//! The ax-devil backend: HTTP routes for the frontend and, in release builds, the embedded frontend itself.

pub mod access;
pub mod camera_questions;
pub mod desktop;
pub mod disk_cache;
pub mod exports;
pub mod glance;
pub mod live_sources;
pub mod network;
pub mod plugins;
mod prepare;
pub mod render_host;
mod routes;
pub mod runtime;
pub mod session;
pub mod share;
#[cfg(test)]
mod test_support;
pub mod throttled;
mod thumbnails;
pub mod video_cache;
pub mod video_sources;

use access::{Admission, Limits, Policy, Principal};
use api_types::{
    API_VERSION, ApiError, AppInfo, Grant, SHELL_API, SettingsChange, SettingsFile, SettingsState,
};
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
use sessions::session::{OnRuntime, poll_sources};
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
    /// The log file this backend writes, when it writes one; see [`AppState::with_log_file`].
    log_file: Option<std::path::PathBuf>,
    /// The decoders and playlist resolvers the app knows, and the plugins that provide them.
    pub plugins: Arc<plugins::Plugins>,
    /// The open videos, which the playback socket draws its viewers from.
    pub videos: Arc<video_sources::VideoSources>,
    /// The cached first pictures of videos.
    pub(crate) thumbnails: Arc<thumbnails::Thumbnails>,
    /// Opens the overlays of viewers.
    pub overlays: Arc<OverlayService>,
    /// Readies the next playlist entry's caches while a viewer plays the one before it.
    pub(crate) preparer: Arc<prepare::Preparer>,
    /// The live camera pipelines, which the playback socket serves live streams from.
    pub live: Arc<live_sources::LiveSources>,
    /// The network scan and what it found.
    pub network: Arc<network::Network>,
    /// The snapshots and login checks the sidebar asked cameras for.
    pub glances: Arc<glance::Glances>,
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
        let cameras = Arc::clone(&settings);
        let environment = Arc::new(workspace::Environment::of_process);
        let sessions = Sessions::new(
            GRACE,
            Arc::new(OnRuntime {
                runtime,
                handlers: plugins.set(),
                environment,
            }),
        );
        tokio::spawn(poll_sources(sessions.clone(), stopping.clone()));
        let overlays = Arc::new(OverlayService::new(tracks));
        Self {
            settings,
            preparer: Arc::new(prepare::Preparer::new(
                Arc::clone(&videos),
                Arc::clone(&overlays),
                stopping.clone(),
            )),
            overlays,
            live: Arc::new(live_sources::LiveSources::cameras(cameras)),
            network: network::Network::new(),
            glances: Arc::default(),
            exports: Arc::new(exports::ExportService::electron()),
            catalogs: Arc::new(catalogs),
            sessions,
            thumbnails: Arc::new(thumbnails::Thumbnails::new(
                cache_dir.join(thumbnails::FOLDER),
            )),
            plugins,
            videos,
            stopping,
            windows: None,
            log_file: None,
            policy: Arc::new(policy),
            admission: Arc::new(Admission::new(limits)),
            staged: Arc::default(),
            launch: Arc::new(LaunchQueue::default()),
            files: Arc::new(WorkspaceFiles::new(None)),
            hosting: Arc::new(Hosting::None),
        }
    }

    /// The same state serving live streams from `live`.
    #[must_use]
    pub fn with_live(mut self, live: Arc<live_sources::LiveSources>) -> Self {
        self.live = live;
        self
    }

    /// The same state scanning and reporting through `network`.
    #[must_use]
    pub fn with_network(mut self, network: Arc<network::Network>) -> Self {
        self.network = network;
        self
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

    /// The same state telling a caller who may see this machine's files that its log is written to `file`.
    #[must_use]
    pub fn with_log_file(mut self, file: std::path::PathBuf) -> Self {
        self.log_file = Some(file);
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
fn snapshot(
    settings: &settings::Settings,
    log_file: Option<&Path>,
    state: &settings::State,
    principal: Principal,
) -> SettingsState {
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
        // What a reference gives is this machine's environment: only the person at this machine is told.
        resolved: principal
            .grants()
            .contains(&Grant::Preferences)
            .then(|| state.references(&scopes))
            .filter(|resolved| !resolved.is_empty()),
        file: principal
            .grants()
            .contains(&Grant::Files)
            .then(|| SettingsFile {
                path: settings.path().display().to_string(),
                version: state.version(),
                log: log_file.map(|file| file.display().to_string()),
            }),
        shortcuts,
    }
}

pub(crate) async fn get_settings(
    principal: Principal,
    State(AppState {
        settings, log_file, ..
    }): State<AppState>,
) -> Json<SettingsState> {
    Json(snapshot(
        &settings,
        log_file.as_deref(),
        &settings.state(),
        principal,
    ))
}

/// Replaces every setting and shortcut the caller may change; a rejected or unsaved change leaves the stored state
/// as it was.
pub(crate) async fn put_settings(
    principal: Principal,
    State(AppState {
        settings,
        catalogs: library,
        log_file,
        ..
    }): State<AppState>,
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
    if let Some(serde_json::Value::String(range)) = body.values.get("scan.range") {
        // A `$NAME` reference is checked as the variable holds it now, as the scan will read it; blank is the local
        // network.
        let expanded = settings::reference(range).map_or_else(
            || range.clone(),
            |name| std::env::var(name).unwrap_or_default(),
        );
        if !expanded.trim().is_empty()
            && let Err(error) = camera::Targets::parse(&expanded)
        {
            return Err(failure(
                StatusCode::UNPROCESSABLE_ENTITY,
                "scan.range",
                error.detail().to_owned(),
            ));
        }
    }
    let change = settings::Change {
        values: body.values,
        shortcuts: body.shortcuts,
    };
    // The file write blocks, so it stays off the async workers.
    let saved = tokio::task::spawn_blocking(move || {
        // The default catalog is checked under the lock that writes it, against the value stored by then, so a
        // catalog deleted meanwhile (which resets the default under the same lock) is not stored again.
        settings
            .replace_if(&scopes, &change, |current| {
                let stored = current.resolved(catalogs::DEFAULT_SETTING);
                match routes::catalogs::default_refusal(&library, stored.as_ref(), &change.values) {
                    Some(reason) => Err(settings::Error {
                        location: catalogs::DEFAULT_SETTING.to_owned(),
                        reason,
                        invalid: true,
                    }),
                    None => Ok(()),
                }
            })
            .map(|state| snapshot(&settings, log_file.as_deref(), &state, principal))
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
mod tests;
