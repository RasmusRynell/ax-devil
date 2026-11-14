//! Plugins: installed programs that provide handlers: decoders that turn an overlay file (or lanes) into Scenes,
//! playlist resolvers that turn settings into a playlist, and tasks that read lanes. A plugin may also list views and
//! commands, which have no program of their own: the host reads them from the manifest.
//!
//! A plugin is a folder with an `ax-devil-plugin.json` manifest listing its handlers, listed in the user's registry
//! file ([`Registry`]). [`load`] turns the registry into [`PluginDecoder`]s, [`PluginResolver`]s and [`PluginTask`]s,
//! of which `Handlers::with_decoders` and `with_resolvers` add the first two beside the built-ins. One handler call is one run of the
//! plugin's program: it writes a request on stdin, reads JSON Lines from stdout, and kills its whole process group
//! whenever the run ends. A decode feeds the lines to a `SceneSink`; a failure keeps the samples already delivered:
//! `decode` returns `Err` and the host, which counts the samples, marks the lane incomplete. A scan or resolve is
//! used only if the plugin exits 0.

mod budget;
mod code;
mod decoder;
mod fields;
mod kinds;
mod lanes;
mod lines;
mod manifest;
mod program;
mod records;
mod registry;
mod reserved;
mod resolved;
mod resolver;
mod run;
mod spawn;
mod stderr;
mod task;
mod wire;

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use handler_api::{Decoder, PlaylistResolver, Settings, Values};

pub use decoder::PluginDecoder;
pub use fields::RESERVED_SETTING_KEYS;
pub use kinds::{CommandHandler, LaneInputs, On, Place, TaskHandler, ViewHandler};
pub use manifest::{
    DecoderHandler, DecoderInput, Handler, MANIFEST_FILE, Manifest, ManifestError, ResolverHandler,
    SkippedHandler,
};
pub use program::Program;
pub use registry::{Registry, RegistryError};
pub use reserved::Reserved;
pub use resolver::PluginResolver;
pub use task::{PluginTask, TaskProgress};

/// The plugin protocol version, the plugin API version: manifests must declare it and requests carry it.
pub const PROTOCOL: u32 = 3;

/// A plugin that loaded, with the handlers it provides.
pub struct LoadedPlugin {
    /// What the plugin declares about itself.
    pub manifest: Manifest,
    /// Its decoders, in manifest order.
    pub decoders: Vec<Arc<PluginDecoder>>,
    /// Its playlist resolvers, in manifest order.
    pub resolvers: Vec<Arc<PluginResolver>>,
    /// Its tasks, in manifest order. Its views and commands are read from `manifest.handlers`.
    pub tasks: Vec<Arc<PluginTask>>,
    /// The handlers it lists that this build cannot use: of a kind it does not know, or referring to something
    /// missing.
    pub skipped: Vec<SkippedHandler>,
    /// The settings its program runs with: the stored values with the defaults filled in.
    pub settings: Values,
}

impl LoadedPlugin {
    /// Reads the manifest at `path` and makes the plugin's handlers, with the settings at their defaults.
    pub fn load(path: &Path) -> Result<Self, ManifestError> {
        Manifest::load(path).and_then(|manifest| Self::new(manifest, &Settings::new()))
    }

    /// The handlers of a validated manifest, sharing one [`Program`] that runs with `settings`, the plugin's stored
    /// values (see [`Manifest::settings_values`]).
    pub fn new(manifest: Manifest, settings: &Settings) -> Result<Self, ManifestError> {
        let settings = manifest.settings_values(settings)?;
        let program = Arc::new(Program::new(manifest.clone(), settings.clone()));
        let mut decoders = Vec::new();
        let mut resolvers = Vec::new();
        let mut tasks = Vec::new();
        for handler in &manifest.handlers {
            match handler {
                Handler::Decoder(decoder) => {
                    decoders.push(Arc::new(PluginDecoder::new(Arc::clone(&program), decoder)));
                }
                Handler::PlaylistResolver(resolver) => {
                    resolvers.push(Arc::new(PluginResolver::new(
                        Arc::clone(&program),
                        resolver,
                    )));
                }
                Handler::Task(task) => {
                    tasks.push(Arc::new(PluginTask::new(Arc::clone(&program), task)));
                }
                Handler::View(_) | Handler::Command(_) => {}
            }
        }
        Ok(Self {
            skipped: manifest.skipped.clone(),
            manifest,
            decoders,
            resolvers,
            tasks,
            settings,
        })
    }
}

/// One registry entry after loading.
pub struct Entry {
    /// The manifest the registry names.
    pub manifest: PathBuf,
    /// The plugin, or why the entry cannot be used.
    pub plugin: Result<LoadedPlugin, ManifestError>,
}

/// Every plugin the registry lists.
pub struct Loaded {
    /// One per registry entry, in install order; failed ones stay so they can be listed.
    pub entries: Vec<Entry>,
    /// Why the registry itself could not be read; then no plugin is loaded.
    pub registry: Option<RegistryError>,
}

impl Loaded {
    /// The decoders of the plugins that loaded, for `Handlers::with_decoders`.
    pub fn decoders(&self) -> Vec<Arc<dyn Decoder>> {
        self.entries
            .iter()
            .filter_map(|entry| entry.plugin.as_ref().ok())
            .flat_map(|plugin| &plugin.decoders)
            .map(|decoder| Arc::clone(decoder) as Arc<dyn Decoder>)
            .collect()
    }

    /// The playlist resolvers of the plugins that loaded, for `Handlers::with_resolvers`.
    pub fn resolvers(&self) -> Vec<Arc<dyn PlaylistResolver>> {
        self.entries
            .iter()
            .filter_map(|entry| entry.plugin.as_ref().ok())
            .flat_map(|plugin| &plugin.resolvers)
            .map(|resolver| Arc::clone(resolver) as Arc<dyn PlaylistResolver>)
            .collect()
    }
}

/// Loads the plugins the registry file at `registry` lists. A broken registry, manifest or plugin never stops the
/// app: it is reported in the result and the rest still load. `reserved` holds the ids the built-ins take; a plugin
/// that repeats one, or an earlier plugin's, does not load.
pub fn load(registry: &Path, reserved: &Reserved) -> Loaded {
    let registry = match Registry::read(registry) {
        Ok(registry) => registry,
        Err(error) => {
            return Loaded {
                entries: Vec::new(),
                registry: Some(error),
            };
        }
    };
    let manifests = registry.manifests();
    let read: Vec<_> = manifests.iter().map(|path| Manifest::load(path)).collect();
    let entries = reserved
        .claim_in_order(read)
        .zip(manifests)
        .enumerate()
        .map(|(index, (plugin, manifest))| Entry {
            manifest,
            plugin: plugin.and_then(|plugin| LoadedPlugin::new(plugin, registry.settings(index))),
        })
        .collect();
    Loaded {
        entries,
        registry: None,
    }
}
