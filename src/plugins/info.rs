//! What [`PluginsInfo`](api_types::handlers::PluginsInfo) lists: one entry per plugin, built-in or installed.

use api_types::handlers::{
    HandlerKind, HandlerSummary, PluginInfo, PluginSource, PluginStatus, SkippedHandlerInfo,
};
use handler_api::{Decoder, PlaylistResolver};
use handlers::Handlers;
use plugins::Loaded;
use std::{path::Path, sync::Arc};

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
pub(super) fn built_in_info(built_in: &Handlers) -> Vec<PluginInfo> {
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
pub(super) fn installed_info(loaded: &Loaded) -> Vec<PluginInfo> {
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
                    reason: error.capitalised(),
                },
            },
        })
        .collect()
}
