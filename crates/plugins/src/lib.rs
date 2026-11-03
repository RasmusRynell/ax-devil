//! Plugins: installed programs that provide handlers: decoders that turn an overlay file into Scenes, and playlist
//! resolvers that turn settings into a playlist.
//!
//! A plugin is a folder with an `ax-devil-plugin.json` manifest listing its handlers, listed in the user's registry
//! file ([`Registry`]). [`load`] turns the registry into [`PluginDecoder`]s and [`PluginResolver`]s, which
//! `Handlers::with_decoders` and `with_resolvers` add beside the built-ins. One handler call is one run of the
//! plugin's program: it writes a request on stdin, reads JSON Lines from stdout, and kills its whole process group
//! whenever the run ends. A decode feeds the lines to a `SceneSink`; a failure keeps the samples already delivered:
//! `decode` returns `Err` and the host, which counts the samples, marks the lane incomplete. A scan or resolve is
//! used only if the plugin exits 0.

mod code;
mod decoder;
mod fields;
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
mod wire;

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use handler_api::{Decoder, PlaylistResolver};

pub use decoder::PluginDecoder;
pub use fields::RESERVED_SETTING_KEYS;
pub use manifest::{
    DecoderHandler, Handler, MANIFEST_FILE, Manifest, ManifestError, ResolverHandler,
    SkippedHandler,
};
pub use program::Program;
pub use registry::{Registry, RegistryError};
pub use reserved::Reserved;
pub use resolver::PluginResolver;

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
    /// The handlers it lists that this build does not know how to run.
    pub skipped: Vec<SkippedHandler>,
}

impl LoadedPlugin {
    /// Reads the manifest at `path` and makes the plugin's handlers.
    pub fn load(path: &Path) -> Result<Self, ManifestError> {
        Manifest::load(path).map(Self::new)
    }

    /// The handlers of a validated manifest, sharing one [`Program`].
    pub fn new(manifest: Manifest) -> Self {
        let program = Arc::new(Program::new(manifest.clone()));
        let mut decoders = Vec::new();
        let mut resolvers = Vec::new();
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
            }
        }
        Self {
            skipped: manifest.skipped.clone(),
            manifest,
            decoders,
            resolvers,
        }
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
    let mut taken = reserved.clone();
    let entries = registry
        .manifests()
        .into_iter()
        .map(|manifest| {
            let plugin = LoadedPlugin::load(&manifest).and_then(|plugin| {
                taken.claim(&plugin.manifest)?;
                Ok(plugin)
            });
            Entry { manifest, plugin }
        })
        .collect();
    Loaded {
        entries,
        registry: None,
    }
}
