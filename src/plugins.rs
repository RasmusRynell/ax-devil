//! The converters the user installed, as the app starts with them: their decoders join the built-in ones, and what
//! `GET /api/plugins` and `plugins list` say comes from the same load.

use api_types::handlers::{PluginInfo, PluginStatus, PluginsInfo};
use converters::Loaded;
use handlers::Handlers;
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

/// The ids a converter may not take: every built-in decoder's handler and plugin id, and every resolver's id.
pub fn reserved_ids(handlers: &Handlers) -> Vec<&'static str> {
    let ids: HashSet<&'static str> = handlers
        .file_decoders()
        .iter()
        .flat_map(|decoder| [decoder.spec().handler, decoder.spec().plugin_id])
        .chain(
            handlers
                .resolvers()
                .iter()
                .map(|resolver| resolver.spec().id),
        )
        .collect();
    ids.into_iter().collect()
}

/// `built_in` plus the converters the registry file lists, and what loading them said. `registry` is the file's
/// place, or why it is not known; then only the built-ins load and the reason is reported.
pub fn load(registry: &Result<PathBuf, String>, built_in: Handlers) -> (Handlers, PluginsInfo) {
    match registry {
        Ok(file) => {
            let loaded = converters::load(file, &reserved_ids(&built_in));
            let info = describe(&loaded);
            (built_in.with_file_decoders(loaded.decoders()), info)
        }
        Err(reason) => (
            built_in,
            PluginsInfo {
                converters: Vec::new(),
                problem: Some(reason.clone()),
            },
        ),
    }
}

/// What loading said, one entry per registry entry.
pub fn describe(loaded: &Loaded) -> PluginsInfo {
    let converters = loaded
        .plugins
        .iter()
        .map(|plugin| PluginInfo {
            manifest: plugin.manifest.display().to_string(),
            folder: plugin
                .manifest
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .display()
                .to_string(),
            status: match &plugin.decoder {
                Ok(decoder) => {
                    let manifest = decoder.manifest();
                    PluginStatus::Loaded {
                        id: manifest.id.clone(),
                        name: manifest.name.clone(),
                        version: manifest.version.clone(),
                        extensions: manifest.extensions.clone(),
                    }
                }
                Err(error) => PluginStatus::Failed {
                    reason: error.to_string(),
                },
            },
        })
        .collect();
    PluginsInfo {
        converters,
        problem: loaded.registry.as_ref().map(ToString::to_string),
    }
}

/// Logs what did not load, which `load` could not because logging starts after the command line is read.
pub fn log_problems(info: &PluginsInfo) {
    if let Some(problem) = &info.problem {
        tracing::warn!("converters not loaded: {problem}");
    }
    for plugin in &info.converters {
        if let PluginStatus::Failed { reason } = &plugin.status {
            tracing::warn!("converter {} not loaded: {reason}", plugin.manifest);
        }
    }
}
