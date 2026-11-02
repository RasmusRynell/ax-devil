//! Converters: installed programs that turn an overlay file into Scenes.
//!
//! A converter is a folder with an `ax-devil-converter.json` manifest, listed in the user's registry file
//! ([`Registry`]). [`load`] turns the registry into [`ConverterDecoder`]s, which `Handlers::with_decoders` adds
//! beside the built-in decoders. Running one starts its process, writes a request on stdin, reads JSON Lines from
//! stdout into the decode's `SceneSink`, and kills its whole process group whenever the run ends. A failure keeps
//! the samples already delivered: `decode` returns `Err` and the host, which counts the samples, marks the lane
//! incomplete.

mod code;
mod decoder;
mod lines;
mod manifest;
mod records;
mod registry;
mod run;
mod spawn;
mod stderr;

use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::Arc,
};

use handler_api::Decoder;

pub use decoder::ConverterDecoder;
pub use manifest::{MANIFEST_FILE, Manifest, ManifestError};
pub use registry::{Registry, RegistryError};

/// The converter protocol version, the plugin API version: manifests must declare it and requests carry it.
pub const PROTOCOL: u32 = 2;

/// One registry entry after loading.
pub struct Plugin {
    /// The manifest the registry names.
    pub manifest: PathBuf,
    /// The decoder, or why the entry cannot be used.
    pub decoder: Result<Arc<ConverterDecoder>, ManifestError>,
}

/// Every converter the registry lists.
pub struct Loaded {
    /// One per registry entry, in install order; failed ones stay so they can be listed.
    pub plugins: Vec<Plugin>,
    /// Why the registry itself could not be read; then no converter is loaded.
    pub registry: Option<RegistryError>,
}

impl Loaded {
    /// The converters that loaded, for `Handlers::with_decoders`.
    pub fn decoders(&self) -> Vec<Arc<dyn Decoder>> {
        self.plugins
            .iter()
            .filter_map(|plugin| plugin.decoder.as_ref().ok())
            .map(|decoder| Arc::clone(decoder) as Arc<dyn Decoder>)
            .collect()
    }
}

/// Loads the converters the registry file at `registry` lists. A broken registry, manifest or converter never
/// stops the app: it is reported in the result and the rest still load. `reserved` holds the ids and handler ids
/// already taken (the built-ins'); a converter that repeats one, or an earlier converter's, does not load.
pub fn load(registry: &Path, reserved: &[&str]) -> Loaded {
    let registry = match Registry::read(registry) {
        Ok(registry) => registry,
        Err(error) => {
            return Loaded {
                plugins: Vec::new(),
                registry: Some(error),
            };
        }
    };
    let mut taken: HashSet<String> = reserved.iter().map(|id| (*id).to_owned()).collect();
    let plugins = registry
        .manifests()
        .into_iter()
        .map(|manifest| {
            let decoder = ConverterDecoder::load(&manifest).and_then(|decoder| {
                let id = &decoder.manifest().id;
                if taken.insert(id.clone()) {
                    Ok(Arc::new(decoder))
                } else {
                    Err(ManifestError::new(
                        "id",
                        format!("'{id}' is already taken by a built-in or installed handler"),
                    ))
                }
            });
            if let Err(error) = &decoder {
                tracing::warn!("converter {} not loaded: {error}", manifest.display());
            }
            Plugin { manifest, decoder }
        })
        .collect();
    Loaded {
        plugins,
        registry: None,
    }
}
