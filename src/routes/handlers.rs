//! Routes for the decoders and playlist resolvers the app knows, and for the scan a resolver's form asks for.

use super::CancelOnDrop;
use crate::AppState;
use api_types::{
    Audience, ErrorWire,
    handlers::{
        ChoiceInfo, DecoderInfo, FieldInfo, FieldKindWire, HandlersInfo, ResolverInfo, ScanRequest,
    },
};
use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use foundation::{CancelToken, ErrorCode, Interrupt, Location, UserError};
use playlist_resolvers::{Choice, FieldKind, PlaylistResolver, Settings};
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
            extensions: extensions
                .iter()
                .map(|&extension| extension.to_owned())
                .collect(),
        },
        FieldKind::Text => FieldKindWire::Text,
        FieldKind::Handler => FieldKindWire::Handler,
        FieldKind::Scan { depends_on } => FieldKindWire::Scan {
            depends_on: depends_on.iter().map(|&key| key.to_owned()).collect(),
        },
    }
}

/// The decoders and resolvers the app knows, with the form each resolver's settings take.
pub(crate) async fn get_handlers(State(state): State<AppState>) -> Json<HandlersInfo> {
    let decoders = state.handlers.file_decoders().iter().map(|decoder| {
        let spec = decoder.spec();
        DecoderInfo {
            handler: spec.handler.to_owned(),
            display_name: spec.display_name.to_owned(),
            extensions: spec
                .extensions
                .iter()
                .map(|&extension| extension.to_owned())
                .collect(),
        }
    });
    let resolvers = state.handlers.resolvers().iter().map(|resolver| {
        let spec = resolver.spec();
        ResolverInfo {
            id: spec.id.to_owned(),
            display_name: spec.display_name.to_owned(),
            description: spec.description.to_owned(),
            fields: resolver
                .schema()
                .fields
                .iter()
                .map(|field| FieldInfo {
                    key: field.key.to_owned(),
                    label: field.label.to_owned(),
                    kind: field_kind(&field.kind),
                    required: field.required,
                })
                .collect(),
        }
    });
    Json(HandlersInfo {
        decoders: decoders.collect(),
        resolvers: resolvers.collect(),
    })
}

/// Runs the resolver's scan of `field` on a blocking thread. Dropping the returned future, as the server does when
/// the client closes the request, cancels the scan.
async fn scan_choices(
    resolver: Arc<dyn PlaylistResolver>,
    field: String,
    settings: Settings,
) -> Result<Vec<Choice>, Interrupt<UserError>> {
    let scannable =
        resolver.schema().fields.iter().any(|declared| {
            declared.key == field && matches!(declared.kind, FieldKind::Scan { .. })
        });
    if !scannable {
        return Err(Interrupt::Failed(
            UserError::new(
                ErrorCode::PlaylistInvalid,
                "This setting has nothing to scan",
            )
            .at(Location::field(field)),
        ));
    }
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
    let Some(resolver) = state.handlers.resolver(&id).cloned() else {
        return Err(refuse(
            StatusCode::NOT_FOUND,
            &UserError::new(ErrorCode::UnknownHandler, "No such playlist resolver"),
        ));
    };
    match scan_choices(resolver, request.field, request.settings).await {
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
    use axum::{body::Body, http::Request};
    use foundation::Cancelled;
    use playlist_resolvers::{
        Cli, Field, ResolvedPlaylist, ResolverSchema, ResolverSpec, WatchPaths,
    };
    use serde_json::json;
    use std::{
        sync::atomic::{AtomicBool, Ordering},
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
    }

    static WAITING_SPEC: ResolverSpec = ResolverSpec {
        id: "waiting",
        display_name: "Waiting",
        description: "Scans until cancelled.",
    };
    static WAITING_SCHEMA: ResolverSchema = ResolverSchema {
        fields: &[Field {
            key: "items",
            label: "Items",
            kind: FieldKind::Scan { depends_on: &[] },
            required: false,
            cli: Cli::Flag("items"),
        }],
    };

    impl PlaylistResolver for Waiting {
        fn spec(&self) -> &'static ResolverSpec {
            &WAITING_SPEC
        }

        fn schema(&self) -> &'static ResolverSchema {
            &WAITING_SCHEMA
        }

        fn scan(
            &self,
            _field: &str,
            _settings: &Settings,
            cancel: &CancelToken,
        ) -> Result<Vec<Choice>, Interrupt<UserError>> {
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
            _settings: &Settings,
            _cancel: &CancelToken,
        ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
            Err(Interrupt::Failed(
                UserError::new(ErrorCode::PlaylistInvalid, "Not resolvable")
                    .at(Location::field("items")),
            ))
        }

        fn watch_paths(&self, _: &Settings, _: &CancelToken) -> Result<WatchPaths, Cancelled> {
            Ok(WatchPaths::default())
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
        let request = tokio::spawn(scan_choices(
            resolver.clone(),
            "items".to_owned(),
            Settings::new(),
        ));
        assert!(until(&resolver.started).await, "the scan started");
        assert!(!resolver.cancelled.load(Ordering::Acquire));

        request.abort();

        assert!(
            until(&resolver.cancelled).await,
            "the resolver saw the cancel"
        );
    }
}
