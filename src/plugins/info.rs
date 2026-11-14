//! What [`PluginsInfo`](api_types::handlers::PluginsInfo) lists: one entry per plugin, built-in or installed.

use api_types::handlers::{
    CommandInfo, ContributionRow, FieldInfo, FieldKindWire, OperationInfo, PluginInfo,
    PluginSource, PluginStatus, ScanCountInfo, ViewInfo,
};
use handler_api::{Decoder, FieldKind, Form, PlaylistResolver};
use handlers::Handlers;
use plugins::{DecoderInput, Handler, Loaded, LoadedPlugin, PluginTask};
use std::{collections::HashMap, fmt::Write as _, path::PathBuf, sync::Arc};

fn field_kind(kind: &FieldKind) -> FieldKindWire {
    match kind {
        FieldKind::Folder => FieldKindWire::Folder,
        FieldKind::File { extensions } => FieldKindWire::File {
            extensions: extensions.clone(),
        },
        FieldKind::Text => FieldKindWire::Text,
        FieldKind::Integer { min } => FieldKindWire::Integer { min: *min },
        FieldKind::Handler => FieldKindWire::Handler,
        FieldKind::DecoderOptions { handler_field } => FieldKindWire::DecoderOptions {
            handler_field: handler_field.clone(),
        },
        FieldKind::Scan { depends_on, count } => FieldKindWire::Scan {
            depends_on: depends_on.clone(),
            count: count.as_ref().map(|count| ScanCountInfo {
                missing: count.missing.clone(),
                found: count.found.clone(),
                none: count.none.clone(),
            }),
        },
    }
}

/// The wire form of each field of `form`, in order.
pub(crate) fn field_infos(form: &Form) -> Vec<FieldInfo> {
    form.fields
        .iter()
        .map(|field| FieldInfo {
            key: field.key.clone(),
            label: field.label.clone(),
            kind: field_kind(&field.kind),
            required: field.required,
            default: field.default.clone(),
        })
        .collect()
}

fn row(kind: &str, id: &str, name: &str, detail: String) -> ContributionRow {
    ContributionRow {
        kind: kind.to_owned(),
        id: id.to_owned(),
        name: name.to_owned(),
        detail,
        skipped: None,
    }
}

fn decoder_row(decoder: &Arc<dyn Decoder>) -> ContributionRow {
    let spec = decoder.spec();
    row("decoder", &spec.id, &spec.name, spec.extensions.join(" "))
}

fn resolver_row(resolver: &Arc<dyn PlaylistResolver>) -> ContributionRow {
    let spec = resolver.spec();
    row("playlist_resolver", &spec.id, &spec.name, String::new())
}

/// A plugin that ships with the app: it has handlers and nothing else.
fn built_in(id: String, rows: Vec<ContributionRow>) -> PluginInfo {
    PluginInfo {
        source: PluginSource::BuiltIn,
        status: PluginStatus::Loaded {
            name: id.clone(),
            id,
            version: env!("CARGO_PKG_VERSION").to_owned(),
            rows,
            settings: Vec::new(),
            values: serde_json::Map::new(),
            views: Vec::new(),
            commands: Vec::new(),
            operations: Vec::new(),
        },
    }
}

/// One entry per built-in plugin, in the order its first handler appears.
pub(super) fn built_in_info(built_in_handlers: &Handlers) -> Vec<PluginInfo> {
    let mut groups: Vec<(String, Vec<ContributionRow>)> = Vec::new();
    let mut add =
        |plugin: &str, row: ContributionRow| match groups.iter_mut().find(|(id, _)| id == plugin) {
            Some((_, rows)) => rows.push(row),
            None => groups.push((plugin.to_owned(), vec![row])),
        };
    for decoder in built_in_handlers.decoders() {
        add(&decoder.spec().plugin, decoder_row(decoder));
    }
    for resolver in built_in_handlers.resolvers() {
        add(&resolver.spec().plugin, resolver_row(resolver));
    }
    groups
        .into_iter()
        .map(|(id, rows)| built_in(id, rows))
        .collect()
}

/// What loading the registry made: the listing, and what the routes find a task or a view's files by.
pub(super) struct Installed {
    pub plugins: Vec<PluginInfo>,
    /// The tasks of the plugins that loaded.
    pub tasks: Vec<Arc<PluginTask>>,
    /// The folder of each plugin that has views, by the token its view addresses carry.
    pub roots: HashMap<String, PathBuf>,
}

/// `path` as a URL path: `/` between its parts, anything but letters, digits and `-._~` percent-encoded.
fn url_path(path: &std::path::Path) -> String {
    let mut encoded = String::new();
    for byte in path.to_string_lossy().replace('\\', "/").bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~/".contains(&byte) {
            encoded.push(char::from(byte));
        } else {
            // Writing to a String cannot fail.
            let _ = write!(encoded, "%{byte:02X}");
        }
    }
    encoded
}

/// The listing of one plugin that loaded, with the token its views are served under.
fn loaded_status(plugin: &LoadedPlugin, token: &str) -> PluginStatus {
    let manifest = &plugin.manifest;
    let mut rows = Vec::new();
    let mut views = Vec::new();
    let mut commands = Vec::new();
    let mut operations = Vec::new();
    for handler in &manifest.handlers {
        let (name, detail) = match handler {
            Handler::Decoder(decoder) => {
                if decoder.input == DecoderInput::Lanes {
                    operations.push(OperationInfo {
                        id: decoder.id.clone(),
                        kind: "decoder".to_owned(),
                        name: decoder.name.clone(),
                        lanes: decoder.inputs.lanes.clone(),
                        video: decoder.inputs.video,
                        options: field_infos(&decoder.options),
                    });
                }
                (&decoder.name, decoder.extensions.join(" "))
            }
            Handler::PlaylistResolver(resolver) => (&resolver.name, String::new()),
            Handler::Task(task) => {
                operations.push(OperationInfo {
                    id: task.id.clone(),
                    kind: "task".to_owned(),
                    name: task.name.clone(),
                    lanes: task.inputs.lanes.clone(),
                    video: task.inputs.video,
                    options: field_infos(&task.options),
                });
                (&task.name, task.description.clone().unwrap_or_default())
            }
            Handler::View(view) => {
                views.push(ViewInfo {
                    id: view.id.clone(),
                    title: view.title.clone(),
                    icon: view.icon.clone(),
                    place: view.place.as_str().to_owned(),
                    url: format!("/views/{token}/{}", url_path(&view.document)),
                });
                (&view.title, view.place.as_str().to_owned())
            }
            Handler::Command(command) => {
                commands.push(CommandInfo {
                    id: format!("{}.{}", manifest.id, command.id),
                    title: command.title.clone(),
                    on: command.on.as_str().to_owned(),
                    key: command.shortcut.clone(),
                    runs: command.runs.clone(),
                });
                (
                    &command.title,
                    format!("{} runs {}", command.on.as_str(), command.runs),
                )
            }
        };
        rows.push(row(handler.kind(), handler.id(), name, detail));
    }
    rows.extend(plugin.skipped.iter().map(|skipped| ContributionRow {
        kind: skipped.kind.clone(),
        id: skipped.id.clone().unwrap_or_default(),
        name: skipped.id.clone().unwrap_or_else(|| skipped.kind.clone()),
        detail: String::new(),
        skipped: Some(skipped.reason.clone()),
    }));
    PluginStatus::Loaded {
        id: manifest.id.clone(),
        name: manifest.name.clone(),
        version: manifest.version.clone(),
        rows,
        settings: field_infos(&manifest.settings),
        values: plugin.settings.declared(),
        views,
        commands,
        operations,
    }
}

/// One entry per registry entry. A plugin that has views keeps the token it had in `before` while it stays in the same
/// folder, so a reload does not move its views; any other gets a new one.
pub(super) fn installed_info(loaded: &Loaded, before: &HashMap<String, PathBuf>) -> Installed {
    let mut installed = Installed {
        plugins: Vec::new(),
        tasks: Vec::new(),
        roots: HashMap::new(),
    };
    for entry in &loaded.entries {
        let source = PluginSource::Installed {
            manifest: entry.manifest.display().to_string(),
            folder: entry
                .manifest
                .parent()
                .unwrap_or_else(|| std::path::Path::new("."))
                .display()
                .to_string(),
        };
        let status = match &entry.plugin {
            Ok(plugin) => {
                let folder = plugin.manifest.folder();
                let token = before
                    .iter()
                    .find_map(|(token, root)| (root == folder).then(|| token.clone()))
                    .unwrap_or_else(foundation::random_token);
                let status = loaded_status(plugin, &token);
                if matches!(&status, PluginStatus::Loaded { views, .. } if !views.is_empty()) {
                    installed.roots.insert(token, folder.to_path_buf());
                }
                installed.tasks.extend(plugin.tasks.iter().cloned());
                status
            }
            Err(error) => PluginStatus::Failed {
                reason: error.capitalised(),
            },
        };
        installed.plugins.push(PluginInfo { source, status });
    }
    installed
}
