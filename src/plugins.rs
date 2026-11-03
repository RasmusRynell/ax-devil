//! The plugins the app runs with: the built-in handlers plus the installed plugins' ones, reloaded while the app runs.
//! Everything that installs, removes or reloads goes through [`Plugins`]; routes and the command line read from it.

use api_types::handlers::{
    HandlerKind, HandlerSummary, PluginInfo, PluginSource, PluginStatus, PluginsInfo,
    SkippedHandlerInfo,
};
use foundation::{ErrorCode, UserError};
use handler_api::{Decoder, PlaylistResolver};
use handlers::{HandlerSet, Handlers};
use plugins::{Loaded, Registry, Reserved};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard, PoisonError, RwLock},
    time::{Duration, UNIX_EPOCH},
};
use tokio::{sync::watch, time::MissedTickBehavior};

/// How often the registry and the manifests it lists are looked at. An install made by the command line reaches
/// a running app after one or two of these.
const WATCH_POLL: Duration = Duration::from_secs(1);

/// The most [`WATCH_POLL`]s between two retries of a failed plugin whose files did not change.
const MAX_RETRY_WAIT: u32 = 30;

/// The ids a plugin may not take: every built-in's plugin id, and every built-in decoder's and resolver's handler
/// id.
pub fn reserved(handlers: &Handlers) -> Reserved {
    Reserved {
        plugins: handlers
            .decoders()
            .iter()
            .map(|decoder| decoder.spec().plugin.clone())
            .chain(
                handlers
                    .resolvers()
                    .iter()
                    .map(|resolver| resolver.spec().plugin.clone()),
            )
            .collect(),
        handlers: handlers
            .decoders()
            .iter()
            .map(|decoder| decoder.spec().id.clone())
            .chain(
                handlers
                    .resolvers()
                    .iter()
                    .map(|resolver| resolver.spec().id.clone()),
            )
            .collect(),
    }
}

/// Length and modification time of the registry file and of each manifest it lists, which is what tells that
/// something was installed, removed or edited. Code files are not in it: a plugin's code is looked at on each use.
#[derive(Default, PartialEq, Eq)]
struct Stamp(Vec<(PathBuf, Option<(u64, u128)>)>);

impl Stamp {
    fn of(registry: &Path) -> Self {
        let mut files = vec![registry.to_path_buf()];
        if let Ok(read) = Registry::read(registry) {
            files.extend(read.manifests());
        }
        Self(
            files
                .into_iter()
                .map(|file| {
                    let stat = fs::metadata(&file).ok().map(|meta| {
                        let modified = meta
                            .modified()
                            .ok()
                            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                            .map_or(0, |since| since.as_nanos());
                        (meta.len(), modified)
                    });
                    (file, stat)
                })
                .collect(),
        )
    }
}

/// What the last load read, and how long to wait before loading again for a failed plugin: loading walks the
/// plugins' folders, so a failure that may never clear by itself is retried less and less often.
struct Seen {
    stamp: Stamp,
    /// [`WATCH_POLL`]s between retries.
    wait: u32,
    /// [`WATCH_POLL`]s since the last load.
    waited: u32,
}

impl Default for Seen {
    fn default() -> Self {
        Self {
            stamp: Stamp::default(),
            wait: 1,
            waited: 0,
        }
    }
}

/// The handlers the app runs with and what loading the installed plugins said, replaced as plugins change.
///
/// Work takes a snapshot ([`handlers`](Self::handlers), or the [`set`](Self::set) for a long-lived holder) and keeps
/// it, so a reload never changes what is already running. The only code that replaces the handlers is here.
pub struct Plugins {
    /// The registry file, or why there is none.
    registry: Result<PathBuf, String>,
    built_in: Handlers,
    set: Arc<HandlerSet>,
    info: RwLock<Arc<PluginsInfo>>,
    /// What the last load read. Held for a whole refresh, install, remove or update, so they do not interleave.
    seen: Mutex<Seen>,
}

impl Plugins {
    /// `built_in` plus the plugins the registry file lists, loaded now. `registry` is the file's place, or why it is
    /// not known; then only the built-ins load and the reason is reported.
    pub fn new(registry: Result<PathBuf, String>, built_in: Handlers) -> Arc<Self> {
        let problem = registry.as_ref().err().cloned();
        let plugins = Self::build(registry, built_in, problem);
        plugins.refresh();
        Arc::new(plugins)
    }

    /// Exactly `handlers`, with no registry: nothing to reload and nothing can be installed. For tests.
    pub fn fixed(handlers: Handlers) -> Arc<Self> {
        Arc::new(Self::build(
            Err("This backend has no plugin registry".to_owned()),
            handlers,
            None,
        ))
    }

    fn build(
        registry: Result<PathBuf, String>,
        built_in: Handlers,
        problem: Option<String>,
    ) -> Self {
        let info = PluginsInfo {
            plugins: built_in_info(&built_in),
            problem,
        };
        Self {
            registry,
            set: HandlerSet::new(built_in.clone()),
            built_in,
            info: RwLock::new(Arc::new(info)),
            seen: Mutex::default(),
        }
    }

    /// The handlers as they are now, for holders that look things up for a long time.
    pub fn set(&self) -> Arc<HandlerSet> {
        Arc::clone(&self.set)
    }

    /// A snapshot of the handlers as they are now.
    pub fn handlers(&self) -> Arc<Handlers> {
        self.set.current()
    }

    /// What loading the plugins said.
    pub fn info(&self) -> Arc<PluginsInfo> {
        Arc::clone(&self.info.read().unwrap_or_else(PoisonError::into_inner))
    }

    /// Reloads when the registry or a manifest it lists changed since the last load, or, while an installed plugin
    /// is failed, when loading again gives another result: a code file that was missing may be back, and the stamp
    /// does not cover code files. Those retries come after 1, 2, 4 ... up to [`MAX_RETRY_WAIT`] calls, starting over
    /// whenever the plugins change. Blocking. Returns whether the handlers were replaced.
    pub fn refresh(&self) -> bool {
        let Ok(registry) = &self.registry else {
            return false;
        };
        let mut seen = self.locked();
        let stamp = Stamp::of(registry);
        let unchanged = stamp == seen.stamp;
        if unchanged {
            seen.waited += 1;
            if !self.has_failed() || seen.waited < seen.wait {
                return false;
            }
        }
        let wait = seen.wait;
        let (handlers, info) = self.read(registry, &mut seen);
        // `info` leaves out what a manifest edit may change, so it only tells a retry apart from a reload when the
        // files were the same before and when `read` stamped them.
        if unchanged && seen.stamp == stamp && info == *self.info() {
            seen.wait = (wait * 2).min(MAX_RETRY_WAIT);
            return false;
        }
        self.swap(handlers, info);
        true
    }

    fn has_failed(&self) -> bool {
        self.info().plugins.iter().any(|plugin| {
            matches!(plugin.source, PluginSource::Installed { .. })
                && matches!(plugin.status, PluginStatus::Failed { .. })
        })
    }

    /// Installs the plugin in the folder, or at the manifest file, `path`, and loads it. Blocking.
    pub fn install(&self, path: &Path) -> Result<Arc<PluginsInfo>, UserError> {
        self.change("", |installed, reserved| {
            installed
                .install(path, reserved)
                .map(|_| true)
                .map_err(|error| invalid(path, &error))
        })
    }

    /// Stops loading the plugin `target` names: its id, or the path of its manifest or folder. Its files stay.
    /// Work that already runs keeps the plugin until it ends. Blocking.
    pub fn remove(&self, target: &str) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, _| Ok(installed.remove(target)))
    }

    /// Replaces the plugin `target` names by the one at `path`, reading and checking its manifest again. Blocking.
    pub fn update(&self, target: &str, path: &Path) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, reserved| {
            installed
                .update(target, path, reserved)
                .map(|updated| updated.is_some())
                .map_err(|error| invalid(path, &error))
        })
    }

    /// Replaces the plugin `target` names by what its own manifest says now, in the same place in the order. Blocking.
    pub fn reread(&self, target: &str) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, reserved| {
            let Some(path) = installed.manifest_of(target) else {
                return Ok(false);
            };
            installed
                .update(target, &path, reserved)
                .map(|updated| updated.is_some())
                .map_err(|error| invalid(&path, &error))
        })
    }

    /// Reloads every second until `stopping` turns true.
    pub async fn watch(self: Arc<Self>, mut stopping: watch::Receiver<bool>) {
        let mut tick = tokio::time::interval(WATCH_POLL);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = tick.tick() => {
                    let plugins = Arc::clone(&self);
                    // Reading the files blocks.
                    if let Ok(true) = tokio::task::spawn_blocking(move || plugins.refresh()).await {
                        tracing::info!("Plugins reloaded");
                    }
                }
                () = async { let _ = stopping.wait_for(|stopping| *stopping).await; } => return,
            }
        }
    }

    fn locked(&self) -> MutexGuard<'_, Seen> {
        self.seen.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Edits the registry file with `edit`, which says whether it found something to change, saves it and reloads.
    /// When it did not, `target` is reported as matching no plugin and the file is left alone.
    fn change(
        &self,
        target: &str,
        edit: impl FnOnce(&mut Registry, &Reserved) -> Result<bool, UserError>,
    ) -> Result<Arc<PluginsInfo>, UserError> {
        let registry = self.registry.as_ref().map_err(|reason| {
            UserError::new(
                ErrorCode::PluginInvalid,
                format!("Cannot use plugins: {reason}"),
            )
        })?;
        let mut seen = self.locked();
        let mut installed = Registry::read(registry)
            .map_err(|error| UserError::new(ErrorCode::PluginInvalid, error.to_string()))?;
        if !edit(&mut installed, &reserved(&self.built_in))? {
            return Err(UserError::new(
                ErrorCode::UnknownHandler,
                format!("No installed plugin matches '{target}'"),
            ));
        }
        installed.save().map_err(|error| {
            UserError::new(
                ErrorCode::Internal,
                format!("Cannot save {}: {error}", registry.display()),
            )
        })?;
        self.load(registry, &mut seen);
        Ok(self.info())
    }

    /// Reads the registry and swaps in what it lists.
    fn load(&self, registry: &Path, seen: &mut Seen) {
        let (handlers, info) = self.read(registry, seen);
        self.swap(handlers, info);
    }

    /// The handlers and info the registry lists now.
    fn read(&self, registry: &Path, seen: &mut Seen) -> (Handlers, PluginsInfo) {
        // Before the files are read, so a change made meanwhile is seen by the next refresh.
        *seen = Seen {
            stamp: Stamp::of(registry),
            ..Seen::default()
        };
        let loaded = plugins::load(registry, &reserved(&self.built_in));
        let mut plugins = built_in_info(&self.built_in);
        plugins.extend(installed_info(&loaded));
        let info = PluginsInfo {
            plugins,
            problem: loaded.registry.as_ref().map(ToString::to_string),
        };
        let handlers = self
            .built_in
            .clone()
            .with_decoders(loaded.decoders())
            .with_resolvers(loaded.resolvers());
        (handlers, info)
    }

    fn swap(&self, handlers: Handlers, info: PluginsInfo) {
        self.set.replace(handlers);
        let before = std::mem::replace(
            &mut *self.info.write().unwrap_or_else(PoisonError::into_inner),
            Arc::new(info),
        );
        log_new_problems(&before, &self.info());
    }
}

fn invalid(path: &Path, error: &plugins::ManifestError) -> UserError {
    UserError::new(
        ErrorCode::PluginInvalid,
        format!("{}: {error}", path.display()),
    )
}

fn decoder_summary(decoder: &Arc<dyn Decoder>) -> HandlerSummary {
    let spec = decoder.spec();
    HandlerSummary {
        kind: HandlerKind::Decoder,
        id: spec.id.clone(),
        name: spec.name.clone(),
        extensions: spec.extensions.clone(),
    }
}

fn resolver_summary(resolver: &Arc<dyn PlaylistResolver>) -> HandlerSummary {
    let spec = resolver.spec();
    HandlerSummary {
        kind: HandlerKind::PlaylistResolver,
        id: spec.id.clone(),
        name: spec.name.clone(),
        extensions: Vec::new(),
    }
}

/// One entry per built-in plugin, in the order its first handler appears.
fn built_in_info(built_in: &Handlers) -> Vec<PluginInfo> {
    let mut groups: Vec<(String, Vec<HandlerSummary>)> = Vec::new();
    let mut add = |plugin: &str, summary: HandlerSummary| match groups
        .iter_mut()
        .find(|(id, _)| id == plugin)
    {
        Some((_, handlers)) => handlers.push(summary),
        None => groups.push((plugin.to_owned(), vec![summary])),
    };
    for decoder in built_in.decoders() {
        add(&decoder.spec().plugin, decoder_summary(decoder));
    }
    for resolver in built_in.resolvers() {
        add(&resolver.spec().plugin, resolver_summary(resolver));
    }
    groups
        .into_iter()
        .map(|(id, handlers)| PluginInfo {
            source: PluginSource::BuiltIn,
            status: PluginStatus::Loaded {
                name: id.clone(),
                id,
                version: env!("CARGO_PKG_VERSION").to_owned(),
                handlers,
                skipped: Vec::new(),
            },
        })
        .collect()
}

/// One entry per registry entry.
fn installed_info(loaded: &Loaded) -> Vec<PluginInfo> {
    loaded
        .entries
        .iter()
        .map(|entry| PluginInfo {
            source: PluginSource::Installed {
                manifest: entry.manifest.display().to_string(),
                folder: entry
                    .manifest
                    .parent()
                    .unwrap_or_else(|| Path::new("."))
                    .display()
                    .to_string(),
            },
            status: match &entry.plugin {
                Ok(plugin) => {
                    let manifest = &plugin.manifest;
                    PluginStatus::Loaded {
                        id: manifest.id.clone(),
                        name: manifest.name.clone(),
                        version: manifest.version.clone(),
                        handlers: plugin
                            .decoders
                            .iter()
                            .map(|decoder| {
                                decoder_summary(&(Arc::clone(decoder) as Arc<dyn Decoder>))
                            })
                            .chain(plugin.resolvers.iter().map(|resolver| {
                                resolver_summary(
                                    &(Arc::clone(resolver) as Arc<dyn PlaylistResolver>),
                                )
                            }))
                            .collect(),
                        skipped: plugin
                            .skipped
                            .iter()
                            .map(|handler| SkippedHandlerInfo {
                                kind: handler.kind.clone(),
                                id: handler.id.clone(),
                                reason: handler.reason.clone(),
                            })
                            .collect(),
                    }
                }
                Err(error) => PluginStatus::Failed {
                    reason: error.to_string(),
                },
            },
        })
        .collect()
}

/// Logs what did not load, which loading could not because logging starts after the command line is read.
pub fn log_problems(info: &PluginsInfo) {
    log_new_problems(&PluginsInfo::default(), info);
}

/// Logs the problems in `info` that `before` did not have, so a plugin that stays broken is logged once, not on every
/// reload retry.
fn log_new_problems(before: &PluginsInfo, info: &PluginsInfo) {
    if let Some(problem) = info
        .problem
        .as_ref()
        .filter(|_| info.problem != before.problem)
    {
        tracing::warn!("plugins not loaded: {problem}");
    }
    let known: Vec<_> = failures(before).collect();
    for (manifest, reason) in failures(info).filter(|failure| !known.contains(failure)) {
        tracing::warn!("plugin {manifest} not loaded: {reason}");
    }
    let known: Vec<_> = skips(before).collect();
    for (manifest, handler) in skips(info).filter(|skip| !known.contains(skip)) {
        let id = handler.id.as_deref().unwrap_or("without an id");
        tracing::warn!(
            "plugin {manifest}: handler {} ({id}) skipped: {}",
            handler.kind,
            handler.reason
        );
    }
}

/// The handlers of installed plugins that are listed but not used, with their plugin's manifest.
fn skips(info: &PluginsInfo) -> impl Iterator<Item = (&str, &SkippedHandlerInfo)> {
    info.plugins
        .iter()
        .filter_map(|plugin| match (&plugin.source, &plugin.status) {
            (PluginSource::Installed { manifest, .. }, PluginStatus::Loaded { skipped, .. }) => {
                Some(
                    skipped
                        .iter()
                        .map(move |handler| (manifest.as_str(), handler)),
                )
            }
            _ => None,
        })
        .flatten()
}

/// The installed plugins that did not load, as manifest and reason.
fn failures(info: &PluginsInfo) -> impl Iterator<Item = (&str, &str)> {
    info.plugins
        .iter()
        .filter_map(|plugin| match (&plugin.source, &plugin.status) {
            (PluginSource::Installed { manifest, .. }, PluginStatus::Failed { reason }) => {
                Some((manifest.as_str(), reason.as_str()))
            }
            _ => None,
        })
}
