//! Routes for the decoders and playlist resolvers the app knows and the plugins that provide them.

use crate::AppState;
use api_types::{
    Audience, ErrorWire,
    handlers::{
        DecoderInfo, FieldInfo, FieldKindWire, HandlersInfo, PluginSource, PluginStatus,
        PluginsInfo, ResolverInfo, ScanCountInfo,
    },
};
use axum::{Json, extract::State, http::StatusCode};
use foundation::UserError;
use handler_api::{FieldKind, Form};
use std::collections::HashMap;

/// A refused request: the status and the error as the host sees it. Both routes need the `Files` grant, so the
/// caller may read the host's paths and reasons.
pub(super) type Refusal = (StatusCode, Json<ErrorWire>);

pub(super) fn refuse(status: StatusCode, error: &UserError) -> Refusal {
    (status, Json(api_types::project(error, Audience::HOST)))
}

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

fn field_infos(form: &Form) -> Vec<FieldInfo> {
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

/// The names of the installed plugins that loaded, by plugin id: a built-in is not "from" a plugin the user installed.
fn installed_names(info: &PluginsInfo) -> HashMap<&str, &str> {
    info.plugins
        .iter()
        .filter_map(|plugin| match (&plugin.source, &plugin.status) {
            (PluginSource::Installed { .. }, PluginStatus::Loaded { id, name, .. }) => {
                Some((id.as_str(), name.as_str()))
            }
            _ => None,
        })
        .collect()
}

/// The decoders and resolvers the app knows, with the form each decoder's options and resolver's settings take.
pub(crate) async fn get_handlers(State(state): State<AppState>) -> Json<HandlersInfo> {
    let handlers = state.plugins.handlers();
    let decoders = handlers.decoders().iter().map(|decoder| {
        let spec = decoder.spec();
        DecoderInfo {
            handler: spec.id.clone(),
            display_name: spec.name.clone(),
            description: spec.description.clone(),
            extensions: spec.extensions.clone(),
            options: field_infos(&spec.options),
            live: decoder.reads_live(),
        }
    });
    let plugins = state.plugins.info();
    let names = installed_names(&plugins);
    let resolvers = handlers.resolvers().iter().map(|resolver| {
        let spec = resolver.spec();
        ResolverInfo {
            id: spec.id.clone(),
            display_name: spec.name.clone(),
            description: spec.description.clone(),
            plugin: names
                .get(spec.plugin.as_str())
                .map(|name| (*name).to_owned()),
            fields: field_infos(&spec.form),
        }
    });
    Json(HandlersInfo {
        decoders: decoders.collect(),
        resolvers: resolvers.collect(),
    })
}

/// The built-in and installed plugins, with the reason for each that did not load.
pub(crate) async fn get_plugins(State(state): State<AppState>) -> Json<PluginsInfo> {
    Json(PluginsInfo::clone(&state.plugins.info()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{TestApp, json};
    use api_types::handlers::PluginInfo;
    use axum::{body::Body, http::Request};
    use foundation::{CancelToken, Interrupt};
    use handler_api::{
        DecodeError, DecodeSummary, Decoder, Framing, Input, SceneSink, Spec, Values,
    };
    use handlers::Handlers;
    use scene::MatchBasis;
    use serde_json::json;
    use std::sync::Arc;

    #[tokio::test]
    async fn the_handlers_list_decoders_and_the_forms_of_both_resolvers() {
        let test = TestApp::new();
        let request = Request::builder()
            .uri("/api/handlers")
            .body(Body::empty())
            .unwrap();
        let (status, body) = test.send(request).await;
        assert_eq!(status, StatusCode::OK);
        let info = json(&body);

        let handlers: Vec<_> = info["decoders"]
            .as_array()
            .unwrap()
            .iter()
            .map(|decoder| decoder["handler"].as_str().unwrap())
            .collect();
        assert!(handlers.contains(&"MOT_FILE"), "{handlers:?}");
        let described = |handler: &str| {
            info["decoders"]
                .as_array()
                .unwrap()
                .iter()
                .find(|decoder| decoder["handler"] == handler)
                .map(|decoder| decoder.get("description").cloned())
        };
        assert_eq!(
            described("UVG_VCM"),
            Some(Some(json!(
                "UVG-VCM v1.0 detection/tracking JSON with optional segmentation polygons."
            )))
        );
        assert_eq!(
            described("MOT_FILE"),
            Some(None),
            "no description is left out"
        );
        let resolvers = info["resolvers"].as_array().unwrap();
        let ids: Vec<_> = resolvers
            .iter()
            .map(|resolver| resolver["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, ["folder_pair", "mot_challenge"]);
        let mot = &resolvers[1];
        assert_eq!(
            mot["fields"][0],
            json!({ "key": "root", "label": "Dataset folder", "kind": { "kind": "Folder" }, "required": true })
        );
        assert_eq!(
            mot["fields"][1]["kind"],
            json!({ "kind": "Scan", "depends_on": ["root"] })
        );
    }

    #[test]
    fn a_resolver_is_from_the_installed_plugin_that_loaded_never_from_a_built_in() {
        let loaded = |source, id: &str, name: &str| PluginInfo {
            source,
            status: PluginStatus::Loaded {
                id: id.to_owned(),
                name: name.to_owned(),
                version: "1".to_owned(),
                handlers: Vec::new(),
                skipped: Vec::new(),
            },
        };
        let installed = || PluginSource::Installed {
            manifest: "/p/plugin.json".to_owned(),
            folder: "/p".to_owned(),
        };
        let info = PluginsInfo {
            plugins: vec![
                loaded(PluginSource::BuiltIn, "mot_challenge", "mot_challenge"),
                loaded(installed(), "acme", "Acme Tracks"),
                PluginInfo {
                    source: installed(),
                    status: PluginStatus::Failed {
                        reason: "no main.py".to_owned(),
                    },
                },
            ],
            problem: None,
        };
        assert_eq!(
            installed_names(&info),
            HashMap::from([("acme", "Acme Tracks")])
        );
    }

    /// A decoder that is only listed, never run.
    struct Listed(Spec);

    impl Decoder for Listed {
        fn spec(&self) -> &Spec {
            &self.0
        }

        fn decode(
            &self,
            _input: &mut dyn Input,
            _options: &Values,
            _sink: &mut dyn SceneSink,
            _cancel: &CancelToken,
        ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
            unreachable!("the route only lists decoders")
        }
    }

    fn listed(handler: &str, plugin: &str) -> Arc<dyn Decoder> {
        Arc::new(Listed(Spec {
            id: handler.into(),
            plugin: plugin.into(),
            name: format!("{handler} from {plugin}"),
            extensions: vec![".listed".into()],
            description: None,
            framing: Framing::Whole,
            bases: vec![MatchBasis::Frame],
            default_basis: MatchBasis::Frame,
            options: Form::default(),
            version: 1,
        }))
    }

    #[tokio::test]
    async fn a_built_in_plugin_lists_the_handlers_it_ships_not_those_named_like_it() {
        // Each handler id is the id of the other plugin, so grouping by handler id would swap them.
        let handlers =
            Handlers::built_in().with_decoders([listed("beta", "alpha"), listed("alpha", "beta")]);
        let test = TestApp::with(handlers);
        let request = Request::builder()
            .uri("/api/plugins")
            .body(Body::empty())
            .unwrap();
        let (status, body) = test.send(request).await;
        assert_eq!(status, StatusCode::OK);
        let info: PluginsInfo = serde_json::from_slice(&body).unwrap();

        let handler_ids = |plugin: &str| -> Vec<String> {
            info.plugins
                .iter()
                .find_map(|entry| match &entry.status {
                    PluginStatus::Loaded { id, handlers, .. } if id == plugin => {
                        Some(handlers.iter().map(|handler| handler.id.clone()).collect())
                    }
                    _ => None,
                })
                .expect("the plugin is listed")
        };
        assert_eq!(handler_ids("alpha"), ["beta"]);
        assert_eq!(handler_ids("beta"), ["alpha"]);
    }
}
