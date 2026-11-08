//! The scan a resolver's form asks for.

use super::{
    CancelOnDrop,
    handlers::{Refusal, refuse},
};
use crate::AppState;
use api_types::handlers::{ChoiceInfo, ScanRequest};
use axum::{
    Json,
    extract::{Path, State},
    http::StatusCode,
};
use foundation::{CancelToken, ErrorCode, Interrupt, Location, UserError};
use handler_api::{Choice, Field, FieldKind, Form, PlaylistResolver, Settings};
use handlers::Handlers;
use std::sync::Arc;

/// Runs the resolver's scan of `field` on a blocking thread. Dropping the returned future, as the server does when
/// the client closes the request, cancels the scan.
async fn scan_choices(
    handlers: &Handlers,
    resolver: Arc<dyn PlaylistResolver>,
    field: String,
    settings: Settings,
) -> Result<Vec<Choice>, Interrupt<UserError>> {
    let form = &resolver.spec().form;
    let reads = form
        .fields
        .iter()
        .find_map(|declared| match &declared.kind {
            FieldKind::Scan { depends_on, .. } if declared.key == field => Some(depends_on),
            _ => None,
        });
    let Some(reads) = reads else {
        return Err(Interrupt::Failed(
            UserError::new(
                ErrorCode::PlaylistInvalid,
                "This setting has nothing to scan",
            )
            .at(Location::field(field)),
        ));
    };
    // A scan needs only what it reads: a setting it does not read, such as the decoder a folder pair is loaded with,
    // may still be blank.
    let scan_form = Form {
        fields: form
            .fields
            .iter()
            .map(|declared| Field {
                required: declared.required && reads.contains(&declared.key),
                ..declared.clone()
            })
            .collect(),
    };
    let form = &scan_form;
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
    use axum::{body::Body, http::Request};
    use handler_api::{ResolvedPlaylists, ResolverSpec, Values};
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
                { "value": "SEQ-01", "label": "SEQ-01", "detail": "64x48 @ 25.0 fps, 10 frames" },
                { "value": "SEQ-02", "label": "SEQ-02", "detail": "64x48 @ 25.0 fps, 10 frames" },
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

        // A scan needs only what it reads: a folder pair counts its pairs before a decoder is chosen.
        let both = json!({ "videos_dir": folder.path(), "overlays_dir": folder.path() });
        let (status, body) = test.send(scan("folder_pair", "pairs", both)).await;
        assert_eq!((status, json(&body)), (StatusCode::OK, json!([])));

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
                Field::new(
                    "items",
                    "Items",
                    FieldKind::Scan {
                        depends_on: vec![],
                        count: None,
                    },
                ),
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
        ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
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
