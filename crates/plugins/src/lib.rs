//! Plugins: installed programs that provide handlers, today decoders that turn an overlay file into Scenes.
//!
//! A plugin is a folder with an `ax-devil-plugin.json` manifest listing its handlers, listed in the user's registry
//! file ([`Registry`]). [`load`] turns the registry into [`PluginDecoder`]s, which `Handlers::with_decoders` adds
//! beside the built-in decoders. One handler call is one run of the plugin's program: it writes a request on stdin,
//! reads JSON Lines from stdout into the decode's `SceneSink`, and kills its whole process group whenever the run
//! ends. A failure keeps the samples already delivered: `decode` returns `Err` and the host, which counts the
//! samples, marks the lane incomplete.

mod code;
mod decoder;
mod fields;
mod lines;
mod manifest;
mod program;
mod records;
mod registry;
mod reserved;
mod run;
mod spawn;
mod stderr;

use std::{
    path::{Path, PathBuf},
    sync::Arc,
};

use handler_api::Decoder;

pub use decoder::PluginDecoder;
pub use manifest::{DecoderHandler, Handler, MANIFEST_FILE, Manifest, ManifestError};
pub use program::Program;
pub use registry::{Registry, RegistryError};
pub use reserved::Reserved;

/// The plugin protocol version, the plugin API version: manifests must declare it and requests carry it.
pub const PROTOCOL: u32 = 3;

/// A plugin that loaded, with the handlers it provides.
pub struct LoadedPlugin {
    /// What the plugin declares about itself.
    pub manifest: Manifest,
    /// Its decoders, in manifest order.
    pub decoders: Vec<Arc<PluginDecoder>>,
}

impl LoadedPlugin {
    /// Reads the manifest at `path` and makes the plugin's handlers.
    pub fn load(path: &Path) -> Result<Self, ManifestError> {
        Manifest::load(path).map(Self::new)
    }

    /// The handlers of a validated manifest, sharing one [`Program`].
    pub fn new(manifest: Manifest) -> Self {
        let program = Arc::new(Program::new(manifest.clone()));
        let decoders = manifest
            .handlers
            .iter()
            .map(|handler| match handler {
                Handler::Decoder(decoder) => {
                    Arc::new(PluginDecoder::new(Arc::clone(&program), decoder))
                }
            })
            .collect();
        Self { manifest, decoders }
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
            if let Err(error) = &plugin {
                tracing::warn!("plugin {} not loaded: {error}", manifest.display());
            }
            Entry { manifest, plugin }
        })
        .collect();
    Loaded {
        entries,
        registry: None,
    }
}
