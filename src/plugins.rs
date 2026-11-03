//! The plugins the user installed, as the app starts with them: their decoders join the built-in ones, and what
//! `GET /api/plugins` and `plugins list` say comes from the same load.

use api_types::handlers::{PluginInfo, PluginStatus, PluginsInfo};
use handler_api::Decoder;
use handlers::Handlers;
use plugins::{Loaded, Reserved};
use std::path::{Path, PathBuf};

/// The ids a plugin may not take: every built-in decoder's plugin id, and every built-in decoder's and resolver's
/// handler id.
pub fn reserved(handlers: &Handlers) -> Reserved {
    Reserved {
        plugins: handlers
            .decoders()
            .iter()
            .map(|decoder| decoder.spec().plugin.clone())
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

/// `built_in` plus the plugins the registry file lists, and what loading them said. `registry` is the file's
/// place, or why it is not known; then only the built-ins load and the reason is reported.
pub fn load(registry: &Result<PathBuf, String>, built_in: Handlers) -> (Handlers, PluginsInfo) {
    match registry {
        Ok(file) => {
            let loaded = plugins::load(file, &reserved(&built_in));
            let info = describe(&loaded);
            (built_in.with_decoders(loaded.decoders()), info)
        }
        Err(reason) => (
            built_in,
            PluginsInfo {
                plugins: Vec::new(),
                problem: Some(reason.clone()),
            },
        ),
    }
}

/// What loading said, one entry per registry entry.
pub fn describe(loaded: &Loaded) -> PluginsInfo {
    let plugins = loaded
        .entries
        .iter()
        .map(|entry| PluginInfo {
            manifest: entry.manifest.display().to_string(),
            folder: entry
                .manifest
                .parent()
                .unwrap_or_else(|| Path::new("."))
                .display()
                .to_string(),
            status: match &entry.plugin {
                Ok(plugin) => {
                    let manifest = &plugin.manifest;
                    PluginStatus::Loaded {
                        id: manifest.id.clone(),
                        name: manifest.name.clone(),
                        version: manifest.version.clone(),
                        extensions: plugin
                            .decoders
                            .iter()
                            .flat_map(|decoder| decoder.spec().extensions.iter().cloned())
                            .collect(),
                    }
                }
                Err(error) => PluginStatus::Failed {
                    reason: error.to_string(),
                },
            },
        })
        .collect();
    PluginsInfo {
        plugins,
        problem: loaded.registry.as_ref().map(ToString::to_string),
    }
}

/// Logs what did not load, which `load` could not because logging starts after the command line is read.
pub fn log_problems(info: &PluginsInfo) {
    if let Some(problem) = &info.problem {
        tracing::warn!("plugins not loaded: {problem}");
    }
    for plugin in &info.plugins {
        if let PluginStatus::Failed { reason } = &plugin.status {
            tracing::warn!("plugin {} not loaded: {reason}", plugin.manifest);
        }
    }
}
