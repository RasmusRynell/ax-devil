//! The ax-devil backend: HTTP routes for the frontend and, in release builds, the embedded frontend itself.

use api_types::{API_VERSION, ApiError, AppInfo, Settings as SettingsBody};
use axum::{Json, Router, extract::State, http::StatusCode, routing::get};
#[cfg(feature = "embed-assets")]
use axum::{
    http::{Uri, header},
    response::{IntoResponse, Response},
};
use std::sync::Arc;

mod routes;
pub mod runtime;
#[cfg(test)]
mod test_support;
pub mod video_cache;
pub mod video_sources;

#[cfg(feature = "embed-assets")]
#[derive(rust_embed::RustEmbed)]
#[folder = "frontend/dist/"]
// The frontend build creates the folder; checks that compile without it serve nothing.
#[allow_missing = true]
struct Assets;

/// The services the routes are adapters over, created once at startup.
#[derive(Clone)]
pub struct AppState {
    /// The settings service.
    pub settings: Arc<settings::Settings>,
    /// The workspace store.
    pub store: workspace_store::Store,
    /// The open videos, which the playback socket draws its viewers from.
    pub videos: Arc<video_sources::VideoSources>,
    /// Turns true when the backend is stopping: long-lived connections, such as the playback sockets, end.
    pub stopping: tokio::sync::watch::Receiver<bool>,
}

/// Builds the backend's routes around the services.
pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/api/info", get(info))
        .route("/api/settings", get(get_settings).put(put_settings))
        .merge(routes::workspace::router())
        .merge(routes::playback::router())
        .fallback(asset)
        .with_state(state)
}

async fn get_settings(State(state): State<AppState>) -> Json<SettingsBody> {
    Json(SettingsBody(state.settings.state().values()))
}

/// Replaces every setting; a rejected or unsaved change leaves the stored state as it was.
async fn put_settings(
    State(AppState { settings, .. }): State<AppState>,
    Json(body): Json<SettingsBody>,
) -> Result<Json<SettingsBody>, (StatusCode, Json<ApiError>)> {
    // The file write blocks, so it stays off the async workers.
    let saved = tokio::task::spawn_blocking(move || settings.replace(&body.0))
        .await
        .map_err(|error| {
            failure(
                StatusCode::INTERNAL_SERVER_ERROR,
                "settings",
                error.to_string(),
            )
        })?;
    match saved {
        Ok(state) => Ok(Json(SettingsBody(state.values()))),
        Err(error) => {
            let status = if error.invalid {
                StatusCode::UNPROCESSABLE_ENTITY
            } else {
                tracing::error!("Cannot save settings: {error}");
                StatusCode::INTERNAL_SERVER_ERROR
            };
            Err(failure(status, &error.location, error.reason))
        }
    }
}

pub(crate) fn failure(
    status: StatusCode,
    location: &str,
    reason: String,
) -> (StatusCode, Json<ApiError>) {
    (
        status,
        Json(ApiError {
            reason,
            location: location.to_owned(),
        }),
    )
}

async fn info() -> Json<AppInfo> {
    Json(AppInfo {
        app: "ax-devil".into(),
        api: API_VERSION,
        version: env!("CARGO_PKG_VERSION").into(),
        development: !cfg!(feature = "embed-assets"),
        pid: std::process::id(),
    })
}

#[cfg(feature = "embed-assets")]
async fn asset(uri: Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    match Assets::get(path) {
        Some(file) => (
            [(
                header::CONTENT_TYPE,
                mime_guess::from_path(path).first_or_octet_stream().as_ref(),
            )],
            file.data.to_vec(),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

#[cfg(not(feature = "embed-assets"))]
async fn asset() -> StatusCode {
    StatusCode::NOT_FOUND
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{TestApp, json};
    use axum::{body::Body, http::Request};

    async fn get(path: &str) -> (StatusCode, Vec<u8>) {
        let test = TestApp::new();
        test.send(Request::builder().uri(path).body(Body::empty()).unwrap())
            .await
    }

    fn put(body: &str) -> Request<Body> {
        Request::builder()
            .method("PUT")
            .uri("/api/settings")
            .header("content-type", "application/json")
            .body(Body::from(body.to_owned()))
            .unwrap()
    }

    #[tokio::test]
    async fn release_builds_serve_the_frontend_and_development_builds_leave_it_to_vite() {
        let (status, body) = get("/").await;
        if cfg!(feature = "embed-assets") {
            assert_eq!(status, StatusCode::OK);
            assert!(String::from_utf8_lossy(&body).contains("<div id=\"root\">"));
        } else {
            assert_eq!(status, StatusCode::NOT_FOUND);
        }
        assert_eq!(get("/missing.js").await.0, StatusCode::NOT_FOUND);
        assert_eq!(get("/api/missing").await.0, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn info_identifies_the_backend_and_its_build() {
        let (status, body) = get("/api/info").await;
        assert_eq!(status, StatusCode::OK);
        let info = json(&body);
        assert_eq!(info["app"], "ax-devil");
        assert_eq!(info["api"], API_VERSION);
        assert_eq!(info["development"], !cfg!(feature = "embed-assets"));
    }

    #[tokio::test]
    async fn settings_are_replaced_whole_and_a_rejected_change_leaves_them_as_they_were() {
        let test = TestApp::new();
        let all = |theme: &str| {
            format!(
                r#"{{"ui.theme":"{theme}","ui.quick_setup_done":true,"playback.video_cache":"auto"}}"#
            )
        };

        let (status, body) = test.send(put(&all("dark"))).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json(&body), json(all("dark").as_bytes()));

        let (status, body) = test.send(put(&all("neon"))).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(json(&body)["location"], "ui.theme");

        let (_, body) = test
            .send(
                Request::builder()
                    .uri("/api/settings")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await;
        assert_eq!(json(&body), json(all("dark").as_bytes()));
    }
}
