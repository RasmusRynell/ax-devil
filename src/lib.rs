//! The ax-devil backend: HTTP routes for the frontend and, in release builds, the embedded frontend itself.

pub mod access;
pub mod desktop;
pub mod disk_cache;
pub mod exports;
pub mod plugins;
mod prepare;
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

use access::{Admission, Limits, Policy, Principal, bearer};
use api_types::{
    AppInfo, Audience, ErrorWire, SHELL_API, SettingsFile, SettingsPatch, SettingsState,
    api_version, project,
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
use foundation::{ErrorCode, Location, UserError};
use overlays::OverlayService;
use routes::Listener;
use scene_store::TrackRegistry;
use session::{GRACE, LaunchQueue, Sessions, WorkspaceFiles, WorkspaceStorage};
use sessions::session::{OnRuntime, poll_sources};
use share::Hosting;
use std::{path::Path, sync::Arc, time::Duration};
use tokio::{sync::watch, time::MissedTickBehavior};

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
    /// The settings service; every Owner reads and writes every row.
    pub settings: Arc<settings::Settings>,
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
    pub live: Arc<live::LiveSources>,
    /// The network scan and what it found.
    pub network: Arc<live::network::Network>,
    /// The snapshots and login checks the sidebar asked cameras for.
    pub glances: Arc<live::glance::Glances>,
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
            live: Arc::new(live::LiveSources::cameras(cameras)),
            network: live::network::Network::new(),
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
    pub fn with_live(mut self, live: Arc<live::LiveSources>) -> Self {
        self.live = live;
        self
    }

    /// The same state scanning and reporting through `network`.
    #[must_use]
    pub fn with_network(mut self, network: Arc<live::network::Network>) -> Self {
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

/// What the holder of `credential` reads of `state`: every value and shortcut, what references give only with Full
/// access as `state` itself grants it. The one place that decides it, for a read and for the reply to a save.
fn snapshot(
    settings: &settings::Settings,
    log_file: Option<&Path>,
    state: &settings::State,
    policy: &Policy,
    credential: Option<&str>,
) -> SettingsState {
    let principal = policy.principal_in(credential, state);
    SettingsState {
        revision: state.revision(),
        values: state.values(),
        // What a reference gives is the server's environment: only Full access is told.
        resolved: principal
            .full_access()
            .then(|| state.references())
            .filter(|resolved| !resolved.is_empty()),
        file: Some(SettingsFile {
            path: settings.path().display().to_string(),
            version: state.version(),
            log: log_file.map(|file| file.display().to_string()),
        }),
        shortcuts: state
            .shortcuts()
            .into_iter()
            .map(|(id, key)| (id.to_owned(), key))
            .collect(),
    }
}

pub(crate) async fn get_settings(
    headers: axum::http::HeaderMap,
    State(AppState {
        settings,
        policy,
        log_file,
        ..
    }): State<AppState>,
) -> Json<SettingsState> {
    Json(snapshot(
        &settings,
        log_file.as_deref(),
        &settings.state(),
        &policy,
        bearer(&headers),
    ))
}

/// Applies a patch to the keys it names, in the order writes arrive; a rejected or unsaved patch leaves the stored
/// state as it was.
pub(crate) async fn patch_settings(
    headers: axum::http::HeaderMap,
    State(AppState {
        settings,
        policy,
        catalogs: library,
        log_file,
        ..
    }): State<AppState>,
    Json(body): Json<SettingsPatch>,
) -> Result<Json<SettingsState>, Failure> {
    let patch = settings::Patch {
        set: body.set.unwrap_or_default(),
        reset: body.reset.unwrap_or_default().into_iter().collect(),
        shortcuts_set: body.shortcuts_set.unwrap_or_default(),
        shortcuts_reset: body
            .shortcuts_reset
            .unwrap_or_default()
            .into_iter()
            .collect(),
    };
    // The file write blocks, so it stays off the async workers.
    let saved = tokio::task::spawn_blocking(move || {
        // The default catalog is checked under the lock that writes it, against the value stored by then, so a
        // catalog deleted meanwhile (which resets the default under the same lock) is not stored again.
        settings
            .patch_if(&patch, |current| {
                admit_save(&policy, bearer(&headers), current, &patch)?;
                // Only after admission, which refuses what a caller without Full access may not read or write.
                let full_access = policy.principal_in(bearer(&headers), current).full_access();
                refuse_unscannable_range(&patch, full_access)?;
                let stored = current.resolved(catalogs::DEFAULT_SETTING);
                match routes::catalogs::default_refusal(&library, stored.as_ref(), &patch.set, body.catalog_revision) {
                    Some(reason) => Err(settings::Error {
                        location: catalogs::DEFAULT_SETTING.to_owned(),
                        reason,
                        invalid: true,
                        forbidden: false,
                    }),
                    None => Ok(()),
                }
            })
            .map(|state| {
                snapshot(
                    &settings,
                    log_file.as_deref(),
                    &state,
                    &policy,
                    bearer(&headers),
                )
            })
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
        let status = if error.forbidden {
            StatusCode::FORBIDDEN
        } else if error.invalid {
            StatusCode::UNPROCESSABLE_ENTITY
        } else {
            tracing::error!("Cannot save settings: {error}");
            StatusCode::INTERNAL_SERVER_ERROR
        };
        failure(status, &error.location, error.reason)
    })
}

/// Refuses the patch if it needs Full access that this caller does not have in `current`, the stored state under the
/// write lock.
fn admit_save(
    policy: &Policy,
    credential: Option<&str>,
    current: &settings::State,
    patch: &settings::Patch,
) -> Result<(), settings::Error> {
    if policy.principal_in(credential, current).full_access() {
        Ok(())
    } else {
        current.refuse_without_full_access(patch)
    }
}

/// Refuses a patch whose `scan.range` could not be scanned. Full access is checked as the variable holds the value
/// now when it is a `$NAME` reference, as the scan will read it; anyone else is checked as written, and a reference is
/// not expanded for them, so what the environment holds is neither read nor reported. Blank is the local network.
fn refuse_unscannable_range(
    patch: &settings::Patch,
    full_access: bool,
) -> Result<(), settings::Error> {
    let Some(serde_json::Value::String(range)) = patch.set.get("scan.range") else {
        return Ok(());
    };
    let text = match settings::reference(range) {
        None => range.clone(),
        Some(name) if full_access => std::env::var(name).unwrap_or_default(),
        Some(_) => return Ok(()),
    };
    if text.trim().is_empty() {
        return Ok(());
    }
    live::Targets::parse(&text)
        .map(drop)
        .map_err(|error| settings::Error {
            location: "scan.range".to_owned(),
            reason: error.detail().to_owned(),
            invalid: true,
            forbidden: false,
        })
}

/// A failed request: the status and the error as the host sees it. Every route that fails answers this shape.
pub(crate) type Failure = (StatusCode, Json<ErrorWire>);

/// The failure `error` under `status`, as the host sees it.
fn failed(status: StatusCode, error: UserError) -> Failure {
    (status, Json(project(&error, Audience::HOST)))
}

/// The code of a failure by its status: a refused caller is `Forbidden`, any other client fault `InvalidRequest`, and
/// the rest `Internal`.
fn code_of(status: StatusCode) -> ErrorCode {
    match status {
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => ErrorCode::Forbidden,
        status if status.is_client_error() => ErrorCode::InvalidRequest,
        _ => ErrorCode::Internal,
    }
}

/// The failure `reason` about `location`, a setting key or a field.
pub(crate) fn failure(status: StatusCode, location: &str, reason: String) -> Failure {
    failed(
        status,
        UserError::new(code_of(status), reason).at(Location::field(location)),
    )
}

/// The failure `reason` about no particular field.
pub(crate) fn rejected(status: StatusCode, reason: String) -> Failure {
    failed(status, UserError::new(code_of(status), reason))
}

pub(crate) async fn info(principal: Principal) -> Json<AppInfo> {
    Json(AppInfo {
        app: "ax-devil".into(),
        api: api_version().into(),
        version: env!("CARGO_PKG_VERSION").into(),
        development: !cfg!(feature = "embed-assets"),
        pid: std::process::id(),
        shell: SHELL_API,
        owner: principal.is_owner(),
        full_access: principal.full_access(),
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
