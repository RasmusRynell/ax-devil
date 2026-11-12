//! Routes that copy and delete the user's catalogs. The catalog folder belongs to this machine, so only its own
//! listener serves them (see the route table); the list reaches viewers on the playback socket.

use super::handlers::refuse;
use crate::{AppState, Failure};
use api_types::catalogs::{
    CopiedCatalog, CopyCatalogRequest, DeleteCatalogRequest, ReloadCatalogRequest,
};
use axum::{Json, extract::State, http::StatusCode};
use catalogs::{CatalogId, DEFAULT_SETTING, Library};
use foundation::{ErrorCode, UserError};

/// Why a settings patch may not make its default catalog the one it names: the patch names a catalog other than the
/// stored default, and the library does not list that one without a problem. Saving other settings with a stale
/// default stays possible.
pub(crate) fn default_refusal(
    library: &Library,
    stored: Option<&serde_json::Value>,
    values: &std::collections::BTreeMap<String, serde_json::Value>,
) -> Option<String> {
    let named = values
        .get(DEFAULT_SETTING)?
        .as_str()
        .filter(|id| !id.is_empty())?;
    if stored.and_then(|stored| stored.as_str()) == Some(named) {
        return None;
    }
    let id = CatalogId::parse(named);
    let usable = library
        .documents()
        .iter()
        .any(|info| Some(&info.id) == id.as_ref() && info.problem.is_none());
    (!usable).then(|| format!("{named} is not a catalog that can be used"))
}

/// The id a request names, or a refusal that says it names no catalog.
fn parse(id: &str) -> Result<CatalogId, Failure> {
    CatalogId::parse(id).ok_or_else(|| {
        refuse(
            StatusCode::BAD_REQUEST,
            &UserError::new(ErrorCode::CatalogInvalid, "That is not a catalog id"),
        )
    })
}

/// Runs work on the catalog folder on a blocking thread and maps a failure to the status that tells its cause.
async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, UserError> + Send + 'static,
) -> Result<T, Failure> {
    let answer = tokio::task::spawn_blocking(work)
        .await
        .unwrap_or_else(|error| {
            tracing::error!("Changing the catalogs failed: {error}");
            Err(UserError::new(
                ErrorCode::Internal,
                "The change stopped unexpectedly",
            ))
        });
    answer.map_err(|error| {
        let status = match error.code {
            ErrorCode::CatalogInvalid => StatusCode::BAD_REQUEST,
            ErrorCode::FileNotFound => StatusCode::NOT_FOUND,
            _ => StatusCode::INTERNAL_SERVER_ERROR,
        };
        refuse(status, &error)
    })
}

/// Saves a copy of a catalog in the user's folder under a new name and answers with its id.
pub(crate) async fn copy(
    State(state): State<AppState>,
    Json(request): Json<CopyCatalogRequest>,
) -> Result<Json<CopiedCatalog>, Failure> {
    let source = parse(&request.source)?;
    let library = state.catalogs;
    let id = blocking(move || library.copy_at(&source, request.revision, &request.name)).await?;
    Ok(Json(CopiedCatalog { id: id.to_string() }))
}

/// Reads a user's catalog file again now. A changed document reaches the viewers on the playback socket.
pub(crate) async fn reload(
    State(state): State<AppState>,
    Json(request): Json<ReloadCatalogRequest>,
) -> Result<StatusCode, Failure> {
    let id = parse(&request.id)?;
    let library = state.catalogs;
    blocking(move || {
        library.reread(&id);
        Ok(())
    })
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Deletes a user's catalog. When it is the stored default for new lanes, the default goes back to Standard.
pub(crate) async fn delete(
    State(state): State<AppState>,
    Json(request): Json<DeleteCatalogRequest>,
) -> Result<StatusCode, Failure> {
    let id = parse(&request.id)?;
    let (library, settings) = (state.catalogs, state.settings);
    blocking(move || {
        // The file first: a catalog that cannot be deleted leaves the default alone.
        library.delete(&id)?;
        let id = id.to_string();
        settings
            .update(DEFAULT_SETTING, |stored| {
                (stored.as_str() == Some(id.as_str())).then(|| "".into())
            })
            .map(|_| ())
            .map_err(|error| {
                UserError::new(
                    ErrorCode::SaveFailed,
                    format!("The catalog is deleted, but the default could not be reset: {error}"),
                )
            })
    })
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use crate::test_support::{TestApp, json};
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use serde_json::{Value, json};

    fn post(uri: &str, body: Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    /// The stored default catalog; with `set`, first stores that as the default through the settings API.
    async fn default_catalog(test: &TestApp, set: Option<&str>) -> String {
        let get = || Request::builder().uri("/api/settings").body(Body::empty());
        if let Some(id) = set {
            let patch = Request::builder()
                .method("PATCH")
                .uri("/api/settings")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({ "set": { "overlays.default_catalog": id } }).to_string(),
                ))
                .unwrap();
            assert_eq!(test.send(patch).await.0, StatusCode::OK);
        }
        let (_, body) = test.send(get().unwrap()).await;
        json(&body)["values"]["overlays.default_catalog"]
            .as_str()
            .unwrap()
            .to_owned()
    }

    #[tokio::test]
    async fn a_default_that_names_no_listed_catalog_is_refused_and_nothing_is_stored() {
        let test = TestApp::with_catalog_folder();
        let patch = Request::builder()
            .method("PATCH")
            .uri("/api/settings")
            .header("content-type", "application/json")
            .body(Body::from(
                json!({ "set": { "overlays.default_catalog": "user:gone.json" } }).to_string(),
            ))
            .unwrap();
        assert_eq!(test.send(patch).await.0, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(default_catalog(&test, None).await, "");
    }

    #[tokio::test]
    async fn a_catalog_is_copied_and_deleted_and_a_refusal_says_why() {
        let test = TestApp::with_catalog_folder();
        let (status, body) = test
            .send(post(
                "/api/catalogs/copy",
                json!({ "source": "built-in:standard", "revision": 1, "name": "Night" }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        let copy = json(&body)["id"].as_str().unwrap().to_owned();
        assert!(copy.starts_with("user:"), "{copy}");
        assert!(
            test.catalogs()
                .documents()
                .iter()
                .any(|info| info.id.to_string() == copy)
        );

        // Deleting the catalog that is the default resets the default; deleting another leaves it.
        let (_, body) = test
            .send(post(
                "/api/catalogs/copy",
                json!({ "source": "built-in:standard", "revision": 1, "name": "Day" }),
            ))
            .await;
        let other = json(&body)["id"].as_str().unwrap().to_owned();
        default_catalog(&test, Some(&copy)).await;
        let (status, _) = test
            .send(post("/api/catalogs/delete", json!({ "id": other })))
            .await;
        assert_eq!(status, StatusCode::NO_CONTENT);
        assert_eq!(default_catalog(&test, None).await, copy);

        let (status, _) = test
            .send(post("/api/catalogs/delete", json!({ "id": copy })))
            .await;
        assert_eq!(status, StatusCode::NO_CONTENT);
        assert_eq!(default_catalog(&test, None).await, "");
        assert!(
            !test
                .catalogs()
                .documents()
                .iter()
                .any(|info| info.id.to_string() == copy)
        );

        for (id, expected, code) in [
            (
                "built-in:standard",
                StatusCode::BAD_REQUEST,
                "CatalogInvalid",
            ),
            ("nonsense", StatusCode::BAD_REQUEST, "CatalogInvalid"),
            ("user:gone.json", StatusCode::NOT_FOUND, "FileNotFound"),
        ] {
            let (status, body) = test
                .send(post("/api/catalogs/delete", json!({ "id": id })))
                .await;
            assert_eq!(status, expected, "{id}");
            assert_eq!(json(&body)["code"], code, "{id}");
        }
    }
}
