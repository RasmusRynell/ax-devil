//! What the plugins say did not load, for the log.

use api_types::handlers::{ContributionRow, PluginSource, PluginStatus, PluginsInfo};

/// Logs the problems in `info` that `before` did not have, so a plugin that stays broken is logged once, not on every
/// reload retry.
pub(super) fn log_new_problems(before: &PluginsInfo, info: &PluginsInfo) {
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
    for (manifest, handler, reason) in skips(info).filter(|skip| !known.contains(skip)) {
        let id = if handler.id.is_empty() {
            "without an id"
        } else {
            &handler.id
        };
        tracing::warn!(
            "plugin {manifest}: handler {} ({id}) skipped: {reason}",
            handler.kind
        );
    }
}

/// The handlers of installed plugins that are listed but not used, with their plugin's manifest.
fn skips(info: &PluginsInfo) -> impl Iterator<Item = (&str, &ContributionRow, &str)> {
    info.plugins
        .iter()
        .filter_map(|plugin| match (&plugin.source, &plugin.status) {
            (PluginSource::Installed { manifest, .. }, PluginStatus::Loaded { rows, .. }) => {
                Some(rows.iter().filter_map(move |row| {
                    row.skipped
                        .as_deref()
                        .map(|reason| (manifest.as_str(), row, reason))
                }))
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
