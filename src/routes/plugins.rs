//! Routes that install and remove plugins. Installed plugins belong to this machine, so only its own listener serves
//! them (see the route table); listing is in [`super::handlers`].

use super::handlers::{Refusal, refuse};
use crate::AppState;
use api_types::handlers::{InstallRequest, PluginsInfo, RemoveRequest, UpdateRequest};
use axum::{Json, extract::State, http::StatusCode};
use foundation::{ErrorCode, UserError};
use std::{path::PathBuf, sync::Arc};

/// Runs a change of the installed plugins on a blocking thread and answers with the plugins as they are after it.
async fn changed(
    change: impl FnOnce() -> Result<Arc<PluginsInfo>, UserError> + Send + 'static,
) -> Result<Json<PluginsInfo>, Refusal> {
    let answer = tokio::task::spawn_blocking(change)
        .await
        .unwrap_or_else(|error| {
            tracing::error!("Changing the plugins failed: {error}");
            Err(UserError::new(
                ErrorCode::Internal,
                "The change stopped unexpectedly",
            ))
        });
    match answer {
        Ok(info) => Ok(Json(PluginsInfo::clone(&info))),
        Err(error) => {
            let status = match error.code {
                ErrorCode::PluginInvalid => StatusCode::BAD_REQUEST,
                ErrorCode::UnknownHandler => StatusCode::NOT_FOUND,
                _ => StatusCode::INTERNAL_SERVER_ERROR,
            };
            Err(refuse(status, &error))
        }
    }
}

/// Installs the plugin in a folder, or at a manifest file, and loads it.
pub(crate) async fn install(
    State(state): State<AppState>,
    Json(request): Json<InstallRequest>,
) -> Result<Json<PluginsInfo>, Refusal> {
    let path = PathBuf::from(request.path);
    changed(move || {
        // A relative path would mean this backend's working folder, which nobody typing it has in mind.
        if !path.is_absolute() {
            return Err(UserError::new(
                ErrorCode::PluginInvalid,
                format!("{} is not an absolute path", path.display()),
            ));
        }
        state.plugins.install(&path)
    })
    .await
}

/// Stops loading an installed plugin, which stays in its folder.
pub(crate) async fn remove(
    State(state): State<AppState>,
    Json(request): Json<RemoveRequest>,
) -> Result<Json<PluginsInfo>, Refusal> {
    changed(move || state.plugins.remove(&request.target)).await
}

/// Reads an installed plugin's manifest again, which also retries one that failed.
pub(crate) async fn update(
    State(state): State<AppState>,
    Json(request): Json<UpdateRequest>,
) -> Result<Json<PluginsInfo>, Refusal> {
    changed(move || state.plugins.reread(&request.target)).await
}

#[cfg(test)]
mod tests {
    use crate::{
        plugins::Plugins,
        test_support::{TestApp, json},
    };
    use axum::{
        body::Body,
        http::{Request, StatusCode},
    };
    use handlers::Handlers;
    use serde_json::{Value, json};

    fn post(uri: &str, body: Value) -> Request<Body> {
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap()
    }

    #[tokio::test]
    async fn a_plugin_is_installed_listed_updated_and_removed_and_a_refusal_says_why() {
        let test = TestApp::over(|folder| {
            Plugins::new(Ok(folder.join("plugins.json")), Handlers::built_in())
        });
        let plugin = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/plugins/shell-times");
        let installed = |info: &Value| {
            info["plugins"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|entry| entry["source"]["kind"] == "installed")
                .map(|entry| entry["status"]["id"].as_str().unwrap().to_owned())
                .collect::<Vec<_>>()
        };

        let (status, body) = test
            .send(post("/api/plugins/install", json!({ "path": plugin })))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(installed(&json(&body)), ["shell-times"]);

        // Installing it again, or from a path that means nothing here, is refused and changes nothing.
        for path in [plugin, "shell-times"] {
            let (status, body) = test
                .send(post("/api/plugins/install", json!({ "path": path })))
                .await;
            assert_eq!(status, StatusCode::BAD_REQUEST, "{path}");
            assert_eq!(json(&body)["code"], "PluginInvalid", "{path}");
        }

        // Updating reads the manifest again, by id or by folder, and refuses what is not installed.
        for target in ["shell-times", plugin] {
            let (status, body) = test
                .send(post("/api/plugins/update", json!({ "target": target })))
                .await;
            assert_eq!(status, StatusCode::OK, "{target}");
            assert_eq!(installed(&json(&body)), ["shell-times"], "{target}");
        }
        let (status, body) = test
            .send(post("/api/plugins/update", json!({ "target": "nobody" })))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(json(&body)["code"], "UnknownHandler");

        let (status, body) = test
            .send(post("/api/plugins/remove", json!({ "target": "nobody" })))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(json(&body)["code"], "UnknownHandler");

        let (status, body) = test
            .send(post(
                "/api/plugins/remove",
                json!({ "target": "shell-times" }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        assert!(installed(&json(&body)).is_empty());
    }
}
