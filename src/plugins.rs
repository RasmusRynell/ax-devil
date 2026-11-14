//! The plugins the app runs with: the built-in handlers plus the installed plugins' ones, reloaded while the app runs.
//! Everything that installs, removes or reloads goes through [`Plugins`]; routes and the command line read from it.

mod info;
mod problems;
mod seen;

use crate::shortcut_commands::settle_default_keys;
use api_types::handlers::{CommandInfo, PluginSource, PluginStatus, PluginsInfo};
use foundation::{ErrorCode, UserError};
use handler_api::Settings;
use handlers::{HandlerSet, Handlers};
pub(crate) use info::field_infos;
use info::{Installed, built_in_info, installed_info};
use plugins::{Manifest, PluginTask, Registry, Reserved};
use problems::log_new_problems;
use seen::{Seen, Stamp};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard, PoisonError, RwLock},
    time::Duration,
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

/// What one load of the plugins made besides the handlers: the listing, the tasks and the folders of the views.
struct Current {
    info: Arc<PluginsInfo>,
    tasks: Vec<Arc<PluginTask>>,
    /// The folder of each plugin that has views, by the token its view addresses carry.
    roots: HashMap<String, PathBuf>,
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
    current: RwLock<Arc<Current>>,
    /// What the last load read. Held for a whole refresh, install, remove or update, so they do not interleave.
    seen: Mutex<Seen>,
    /// Rises, from 0, with every replacement of the handlers and `info`.
    replaced: watch::Sender<u64>,
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
        let current = Current {
            info: Arc::new(info),
            tasks: Vec::new(),
            roots: HashMap::new(),
        };
        Self {
            registry,
            set: HandlerSet::new(built_in.clone()),
            built_in,
            current: RwLock::new(Arc::new(current)),
            seen: Mutex::default(),
            replaced: watch::channel(0).0,
        }
    }

    /// The count of replacements of the plugins' handlers and info, as they happen.
    pub fn revisions(&self) -> watch::Receiver<u64> {
        self.replaced.subscribe()
    }

    /// The handlers as they are now, for holders that look things up for a long time.
    pub fn set(&self) -> Arc<HandlerSet> {
        Arc::clone(&self.set)
    }

    /// A snapshot of the handlers as they are now.
    pub fn handlers(&self) -> Arc<Handlers> {
        self.set.current()
    }

    fn current(&self) -> Arc<Current> {
        Arc::clone(&self.current.read().unwrap_or_else(PoisonError::into_inner))
    }

    /// What loading the plugins said.
    pub fn info(&self) -> Arc<PluginsInfo> {
        Arc::clone(&self.current().info)
    }

    /// The task with this handler id, of an installed plugin that loaded.
    pub fn task(&self, id: &str) -> Option<Arc<PluginTask>> {
        self.current()
            .tasks
            .iter()
            .find(|task| task.spec().id == id)
            .cloned()
    }

    /// The folder whose files the views under `token` are served from. A token lasts until the plugins reload.
    pub fn view_root(&self, token: &str) -> Option<PathBuf> {
        self.current().roots.get(token).cloned()
    }

    /// The commands of the plugins that loaded, in plugin order.
    pub fn commands(&self) -> Vec<CommandInfo> {
        self.info()
            .plugins
            .iter()
            .filter_map(|plugin| match &plugin.status {
                PluginStatus::Loaded { commands, .. } => Some(commands.iter().cloned()),
                PluginStatus::Failed { .. } => None,
            })
            .flatten()
            .collect()
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
        let (handlers, current) = self.read(registry, &mut seen);
        // The listing leaves out what a manifest edit may change, so it only tells a retry apart from a reload when
        // the files were the same before and when `read` stamped them.
        if unchanged && seen.stamp == stamp && current.info == self.info() {
            seen.wait = (wait * 2).min(MAX_RETRY_WAIT);
            return false;
        }
        self.swap(handlers, current);
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

    /// Stops loading the plugin `target` names: the path of its manifest or folder, or its id when only one declares
    /// it. Its files stay. Work that already runs keeps the plugin until it ends. Blocking.
    pub fn remove(&self, target: &str) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, _| {
            installed.remove(target).map_err(ambiguous)
        })
    }

    /// Replaces the plugin `target` names by the one at `path`, reading and checking its manifest again. Blocking.
    pub fn update(&self, target: &str, path: &Path) -> Result<Arc<PluginsInfo>, UserError> {
        self.replace(target, |_| path.to_path_buf())
    }

    /// Replaces the plugin `target` names by what its own manifest says now, in the same place in the order. Blocking.
    pub fn reread(&self, target: &str) -> Result<Arc<PluginsInfo>, UserError> {
        self.replace(target, |current| current.to_path_buf())
    }

    /// Replaces the plugin `target` names by the manifest at `path_of(its current manifest)`.
    fn replace(
        &self,
        target: &str,
        path_of: impl FnOnce(&Path) -> PathBuf,
    ) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, reserved| {
            let Some(index) = installed.find(target).map_err(ambiguous)? else {
                return Ok(false);
            };
            let path = path_of(&installed.manifests()[index]);
            installed
                .update(index, &path, reserved)
                .map(|_| true)
                .map_err(|error| invalid(&path, &error))
        })
    }

    /// Stores `values` as the settings of the plugin `target` names, after checking them against the form its
    /// manifest declares, and loads it again, which rebuilds the lanes it derived. Blocking.
    pub fn set_settings(
        &self,
        target: &str,
        values: Settings,
    ) -> Result<Arc<PluginsInfo>, UserError> {
        self.change(target, |installed, _| {
            let Some(index) = installed.find(target).map_err(ambiguous)? else {
                return Ok(false);
            };
            let manifest = Manifest::load(&installed.manifests()[index])
                .map_err(|error| UserError::new(ErrorCode::PluginInvalid, error.capitalised()))?;
            installed.set_settings(index, manifest.stored_settings(values)?);
            Ok(true)
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
    /// When it did not, `target` is reported as matching no plugin and the file is left alone. Every caller is an
    /// explicit change, so another process changing the registry fails it at once; [`refresh`](Self::refresh) only
    /// reads the atomically saved file and takes no lock.
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
        let _lock = Registry::try_lock(registry)
            .map_err(|error| UserError::new(ErrorCode::PluginInvalid, error.to_string()))?;
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
        let (handlers, current) = self.read(registry, seen);
        self.swap(handlers, current);
    }

    /// The handlers and listing the registry lists now. A plugin keeps its view token while it stays in its folder.
    fn read(&self, registry: &Path, seen: &mut Seen) -> (Handlers, Current) {
        // Before the files are read, so a change made meanwhile is seen by the next refresh.
        *seen = Seen {
            stamp: Stamp::of(registry),
            ..Seen::default()
        };
        let loaded = plugins::load(registry, &reserved(&self.built_in));
        let Installed {
            plugins: installed,
            tasks,
            roots,
        } = installed_info(&loaded, &self.current().roots);
        let mut plugins = built_in_info(&self.built_in);
        plugins.extend(installed);
        settle_default_keys(
            plugins
                .iter_mut()
                .filter_map(|plugin| match &mut plugin.status {
                    PluginStatus::Loaded { commands, .. } => Some(commands.iter_mut()),
                    PluginStatus::Failed { .. } => None,
                })
                .flatten()
                .collect(),
        );
        let current = Current {
            info: Arc::new(PluginsInfo {
                plugins,
                problem: loaded.registry.as_ref().map(ToString::to_string),
            }),
            tasks,
            roots,
        };
        let handlers = self
            .built_in
            .clone()
            .with_decoders(loaded.decoders())
            .with_resolvers(loaded.resolvers());
        (handlers, current)
    }

    fn swap(&self, handlers: Handlers, current: Current) {
        self.set.replace(handlers);
        let before = std::mem::replace(
            &mut *self.current.write().unwrap_or_else(PoisonError::into_inner),
            Arc::new(current),
        );
        log_new_problems(&before.info, &self.info());
        self.replaced.send_modify(|revision| *revision += 1);
    }
}

fn ambiguous(error: plugins::RegistryError) -> UserError {
    UserError::new(ErrorCode::PluginInvalid, error.to_string())
}

fn invalid(path: &Path, error: &plugins::ManifestError) -> UserError {
    UserError::new(
        ErrorCode::PluginInvalid,
        format!("{}: {error}", path.display()),
    )
}

/// Logs what did not load, which loading could not because logging starts after the command line is read.
pub fn log_problems(info: &PluginsInfo) {
    log_new_problems(&PluginsInfo::default(), info);
}
