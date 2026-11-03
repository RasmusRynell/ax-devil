//! Routes for the decoders and playlist resolvers the app knows, and for the scan a resolver's form asks for.

use super::CancelOnDrop;
use crate::AppState;
use api_types::{
    Audience, ErrorWire,
    handlers::{
        ChoiceInfo, DecoderInfo, FieldInfo, FieldKindWire, HandlersInfo, PluginsInfo, ResolverInfo,
        ScanRequest,
    },
};
use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use foundation::{CancelToken, ErrorCode, Interrupt, Location, UserError};
use handler_api::{Choice, FieldKind, Form, PlaylistResolver, Settings};
use handlers::Handlers;
use std::sync::Arc;

/// A refused request: the status and the error as the host sees it. Both routes need the `Files` grant, so the
/// caller may read the host's paths and reasons.
type Refusal = (StatusCode, Json<ErrorWire>);

fn refuse(status: StatusCode, error: &UserError) -> Refusal {
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
        FieldKind::Scan { depends_on } => FieldKindWire::Scan {
            depends_on: depends_on.clone(),
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

/// The decoders and resolvers the app knows, with the form each decoder's options and resolver's settings take.
pub(crate) async fn get_handlers(State(state): State<AppState>) -> Json<HandlersInfo> {
    let handlers = state.plugins.handlers();
    let decoders = handlers.decoders().iter().map(|decoder| {
        let spec = decoder.spec();
        DecoderInfo {
            handler: spec.id.clone(),
            display_name: spec.name.clone(),
            extensions: spec.extensions.clone(),
            options: field_infos(&spec.options),
        }
    });
    let resolvers = handlers.resolvers().iter().map(|resolver| {
        let spec = resolver.spec();
        ResolverInfo {
            id: spec.id.clone(),
            display_name: spec.name.clone(),
            description: spec.description.clone(),
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

/// Runs the resolver's scan of `field` on a blocking thread. Dropping the returned future, as the server does when
/// the client closes the request, cancels the scan.
async fn scan_choices(
    handlers: &Handlers,
    resolver: Arc<dyn PlaylistResolver>,
    field: String,
    settings: Settings,
) -> Result<Vec<Choice>, Interrupt<UserError>> {
    let form = &resolver.spec().form;
    let scannable = form
        .fields
        .iter()
        .any(|declared| declared.key == field && matches!(declared.kind, FieldKind::Scan { .. }));
    if !scannable {
        return Err(Interrupt::Failed(
            UserError::new(
                ErrorCode::PlaylistInvalid,
                "This setting has nothing to scan",
            )
            .at(Location::field(field)),
        ));
    }
    // The plugin runs in its own working folder, so a relative path, a default too, would not mean what it does here.
    let mut filled = form
        .validate(&settings)
        .map_err(Interrupt::Failed)?
        .declared();
    workspace::absolute_paths(form, &mut filled, handlers);
    let settings = form.validate(&filled).map_err(Interrupt::Failed)?;
    let cancel = CancelToken::new();
    let _cancel_when_dropped = CancelOnDrop(cancel.clone());
    let location = field.clone();
    let job = tokio::task::spawn_blocking(move || resolver.scan(&field, &settings, &cancel));
    job.await.unwrap_or_else(|error| {
        tracing::error!("Scanning {location} failed: {error}");
        Err(Interrupt::Failed(
            UserError::new(ErrorCode::Internal, "The scan stopped unexpectedly")
                .at(Location::field(location)),
        ))
    })
}

/// Finds the values of one `Scan` field from the settings entered so far. The caller supersedes a slow scan by
/// aborting its request, which cancels the scan.
pub(crate) async fn scan(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(request): Json<ScanRequest>,
) -> Result<Json<Vec<ChoiceInfo>>, Refusal> {
    let handlers = state.plugins.handlers();
    let Some(resolver) = handlers.resolver(&id).cloned() else {
        return Err(refuse(
            StatusCode::NOT_FOUND,
            &UserError::new(ErrorCode::UnknownHandler, "No such playlist resolver"),
        ));
    };
    match scan_choices(&handlers, resolver, request.field, request.settings).await {
        Ok(choices) => Ok(Json(
            choices
                .into_iter()
                .map(|choice| ChoiceInfo {
                    value: choice.value,
                    label: choice.label,
                    detail: choice.detail,
                })
                .collect(),
        )),
        Err(Interrupt::Failed(error)) => Err(refuse(StatusCode::UNPROCESSABLE_ENTITY, &error)),
        // Only a dropped request cancels a scan, so nobody reads this answer.
        Err(Interrupt::Cancelled) => Err(refuse(
            StatusCode::SERVICE_UNAVAILABLE,
            &UserError::new(ErrorCode::Internal, "The scan was cancelled"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{TestApp, json};
    use api_types::handlers::PluginStatus;
    use axum::{body::Body, http::Request};
    use handler_api::{
        DecodeError, DecodeSummary, Decoder, Field, Form, Framing, Input, ResolvedPlaylist,
        ResolverSpec, SceneSink, Spec, Values,
    };
    use scene::MatchBasis;
    use serde_json::json;
    use std::{
        path::Path,
        sync::{
            LazyLock, Mutex,
            atomic::{AtomicBool, Ordering},
        },
        time::{Duration, Instant},
    };

    fn post(uri: &str, body: serde_json::Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    fn mot_folder() -> tempfile::TempDir {
        let folder = tempfile::tempdir().unwrap();
        for name in ["SEQ-02", "SEQ-01"] {
            let dir = folder.path().join(name);
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(
                dir.join("seqinfo.ini"),
                "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
            )
            .unwrap();
        }
        folder
    }

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

    #[tokio::test]
    async fn a_scan_returns_the_choices_and_names_the_setting_a_problem_is_about() {
        let test = TestApp::new();
        let folder = mot_folder();
        let scan = |id: &str, field: &str, settings: serde_json::Value| {
            post(
                &format!("/api/resolvers/{id}/scan"),
                json!({ "field": field, "settings": settings }),
            )
        };

        let (status, body) = test
            .send(scan(
                "mot_challenge",
                "sequences",
                json!({ "root": folder.path() }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            json(&body),
            json!([
                { "value": "SEQ-01", "label": "SEQ-01", "detail": "64×48 · 25 fps · 10 frames" },
                { "value": "SEQ-02", "label": "SEQ-02", "detail": "64×48 · 25 fps · 10 frames" },
            ])
        );

        // A folder typed relative to the backend's working folder is the same folder to the plugin.
        let cwd = std::env::current_dir().unwrap();
        let up = std::path::PathBuf::from_iter(cwd.components().skip(1).map(|_| ".."));
        let relative = up.join(folder.path().strip_prefix("/").unwrap());
        let (status, again) = test
            .send(scan(
                "mot_challenge",
                "sequences",
                json!({ "root": relative }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&again), json(&body));

        let failures = [
            (
                scan("mot_challenge", "sequences", json!({})),
                StatusCode::UNPROCESSABLE_ENTITY,
                "root",
            ),
            (
                scan(
                    "mot_challenge",
                    "sequences",
                    json!({ "root": folder.path().join("gone") }),
                ),
                StatusCode::UNPROCESSABLE_ENTITY,
                "root",
            ),
            (
                scan("mot_challenge", "root", json!({})),
                StatusCode::UNPROCESSABLE_ENTITY,
                "root",
            ),
        ];
        for (request, expected, location) in failures {
            let (status, body) = test.send(request).await;
            assert_eq!(status, expected);
            assert_eq!(json(&body)["location"], location);
        }
        let (status, body) = test.send(scan("no_such", "sequences", json!({}))).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(json(&body)["code"], "UnknownHandler");
    }

    /// A resolver whose scan runs until it is cancelled and says whether it saw that.
    #[derive(Default)]
    struct Waiting {
        started: AtomicBool,
        cancelled: AtomicBool,
        dir: Mutex<String>,
    }

    static WAITING_SPEC: LazyLock<ResolverSpec> = LazyLock::new(|| ResolverSpec {
        id: "waiting".into(),
        plugin: "waiting".into(),
        name: "Waiting".into(),
        description: "Scans until cancelled.".into(),
        form: Form {
            fields: vec![
                Field::new("items", "Items", FieldKind::Scan { depends_on: vec![] }),
                Field {
                    default: Some("runs".into()),
                    ..Field::new("dir", "Folder", FieldKind::Folder)
                },
            ],
        },
    });

    impl PlaylistResolver for Waiting {
        fn spec(&self) -> &ResolverSpec {
            &WAITING_SPEC
        }

        fn scan(
            &self,
            _field: &str,
            settings: &Values,
            cancel: &CancelToken,
        ) -> Result<Vec<Choice>, Interrupt<UserError>> {
            *self.dir.lock().unwrap() = settings.text("dir").unwrap_or_default().to_owned();
            self.started.store(true, Ordering::Release);
            let deadline = Instant::now() + Duration::from_secs(10);
            while Instant::now() < deadline {
                if cancel.is_cancelled() {
                    self.cancelled.store(true, Ordering::Release);
                    return Err(Interrupt::Cancelled);
                }
                std::thread::sleep(Duration::from_millis(1));
            }
            Ok(Vec::new())
        }

        fn resolve(
            &self,
            _settings: &Values,
            _cancel: &CancelToken,
        ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
            Err(Interrupt::Failed(
                UserError::new(ErrorCode::PlaylistInvalid, "Not resolvable")
                    .at(Location::field("items")),
            ))
        }
    }

    async fn until(what: &AtomicBool) -> bool {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !what.load(Ordering::Acquire) {
            if Instant::now() > deadline {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
        true
    }

    #[tokio::test]
    async fn a_scan_is_cancelled_when_the_client_drops_the_request() {
        let resolver = Arc::new(Waiting::default());
        let scanned = resolver.clone();
        let request = tokio::spawn(async move {
            scan_choices(
                &Handlers::built_in(),
                scanned,
                "items".to_owned(),
                Settings::new(),
            )
            .await
        });
        assert!(until(&resolver.started).await, "the scan started");
        // The omitted folder setting reaches the plugin as its default made absolute.
        assert!(Path::new(&*resolver.dir.lock().unwrap()).is_absolute());
        assert!(!resolver.cancelled.load(Ordering::Acquire));

        request.abort();

        assert!(
            until(&resolver.cancelled).await,
            "the resolver saw the cancel"
        );
    }
}
