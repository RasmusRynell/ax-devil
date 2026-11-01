//! The catalogs a lane can be drawn with.

use crate::{AppState, failure};
use api_types::{
    ApiError,
    catalogs::{CatalogInfoWire, CatalogsInfo},
};
use axum::{Json, Router, extract::State, http::StatusCode, routing::get};

/// The catalog routes.
pub fn router() -> Router<AppState> {
    Router::new().route("/api/catalogs", get(list))
}

/// The built-in catalogs, Standard first, and the one a new lane starts with.
async fn list(
    State(state): State<AppState>,
) -> Result<Json<CatalogsInfo>, (StatusCode, Json<ApiError>)> {
    let library = state.catalogs;
    // Listing compiles the catalogs the first time, which stays off the async workers.
    let info = tokio::task::spawn_blocking(move || CatalogsInfo {
        catalogs: library
            .list()
            .into_iter()
            .map(|info| CatalogInfoWire {
                id: info.id.to_string(),
                name: info.name,
                description: info.description,
            })
            .collect(),
        default: library.default_id().to_string(),
    })
    .await
    .map_err(|error| {
        tracing::error!("Listing the catalogs failed: {error}");
        failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            "catalogs",
            "The catalogs could not be listed".to_owned(),
        )
    })?;
    Ok(Json(info))
}

#[cfg(test)]
mod tests {
    use crate::test_support::{TestApp, json};
    use axum::{body::Body, http::Request};

    #[tokio::test]
    async fn the_built_in_catalogs_are_listed_with_standard_first_and_the_default() {
        let request = Request::builder()
            .uri("/api/catalogs")
            .body(Body::empty())
            .unwrap();

        let (status, body) = TestApp::new().send(request).await;

        assert_eq!(status, 200);
        let info = json(&body);
        let ids: Vec<_> = info["catalogs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|catalog| catalog["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids.len(), 6);
        assert_eq!(ids[0], "built-in:standard");
        assert_eq!(info["default"], "built-in:standard");
        assert_eq!(info["catalogs"][0]["name"], "Standard");
    }
}
