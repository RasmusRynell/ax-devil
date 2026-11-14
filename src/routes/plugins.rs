//! Routes that install, remove and configure plugins. Installing, removing and updating run code on this machine, so
//! they need Full access; the settings of a plugin already installed are the Owner's (see the route table). Listing is
//! in [`super::handlers`].

use crate::{AppState, Failure, access::Principal};
use api_types::{
    Audience,
    handlers::{InstallRequest, PluginsInfo, RemoveRequest, SettingsRequest, UpdateRequest},
    project,
};
use axum::{Json, extract::State, http::StatusCode};
use foundation::{ErrorCode, UserError};
use std::{path::PathBuf, sync::Arc};

/// Runs a change of the installed plugins on a blocking thread and answers with the plugins as they are after it. A
/// refusal carries the host's reasons and paths only for `audience`.
async fn changed(
    audience: Audience,
    change: impl FnOnce() -> Result<Arc<PluginsInfo>, UserError> + Send + 'static,
) -> Result<Json<PluginsInfo>, Failure> {
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
            Err((status, Json(project(&error, audience))))
        }
    }
}

/// Stores the settings of an installed plugin, which the plugin's form must accept, and rebuilds what depends on
/// them. An Owner without Full access gets no host reasons or paths in a refusal.
pub(crate) async fn settings(
    principal: Principal,
    State(state): State<AppState>,
    Json(request): Json<SettingsRequest>,
) -> Result<Json<PluginsInfo>, Failure> {
    changed(super::audience(principal), move || {
        state.plugins.set_settings(&request.plugin, request.values)
    })
    .await
}

/// Installs the plugin in a folder, or at a manifest file, and loads it.
pub(crate) async fn install(
    State(state): State<AppState>,
    Json(request): Json<InstallRequest>,
) -> Result<Json<PluginsInfo>, Failure> {
    let path = PathBuf::from(request.path);
    changed(Audience::HOST, move || {
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
) -> Result<Json<PluginsInfo>, Failure> {
    changed(Audience::HOST, move || {
        state.plugins.remove(&request.target)
    })
    .await
}

/// Reads an installed plugin's manifest again, which also retries one that failed.
pub(crate) async fn update(
    State(state): State<AppState>,
    Json(request): Json<UpdateRequest>,
) -> Result<Json<PluginsInfo>, Failure> {
    changed(Audience::HOST, move || {
        state.plugins.reread(&request.target)
    })
    .await
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
        let replaced = test.state.plugins.revisions();
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
        assert!(replaced.has_changed().unwrap(), "the plugins topic rose");

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

    #[tokio::test]
    async fn saved_settings_are_checked_against_the_form_and_rebuild_the_plugins_decoders() {
        let test = TestApp::over(|folder| {
            Plugins::new(Ok(folder.join("plugins.json")), Handlers::built_in())
        });
        let plugin = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/plugins/derived-demo");
        let (status, body) = test
            .send(post("/api/plugins/install", json!({ "path": plugin })))
            .await;
        assert_eq!(status, StatusCode::OK);
        let listed = json(&body)["plugins"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["source"]["kind"] == "installed")
            .unwrap()["status"]
            .clone();
        let kinds: Vec<_> = listed["rows"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| row["kind"].as_str().unwrap())
            .collect();
        assert_eq!(kinds, ["decoder", "task", "view", "command"]);
        assert_eq!(listed["values"], json!({ "label": "demo" }));
        assert_eq!(
            listed["commands"][0]["id"],
            "derived-demo.derived-count-run"
        );
        assert_eq!(listed["operations"].as_array().unwrap().len(), 2);
        let url = listed["views"][0]["url"].as_str().unwrap();
        let token = url
            .strip_prefix("/views/")
            .and_then(|rest| rest.strip_suffix("/views/review/index.html"))
            .expect("a view address carries the plugin's token");
        assert!(test.state.plugins.view_root(token).is_some());
        assert!(test.state.plugins.task("derived-count").is_some());

        let code_hash = || {
            test.state
                .plugins
                .handlers()
                .decoder("derived-union")
                .unwrap()
                .code_hash()
        };
        let before = code_hash();
        assert!(before.is_some());
        let (status, body) = test
            .send(post(
                "/api/plugins/settings",
                json!({ "plugin": "derived-demo", "values": { "label": "changed", "stray": 1 } }),
            ))
            .await;
        assert_eq!(status, StatusCode::OK);
        let stored = &json(&body)["plugins"];
        assert!(
            stored
                .to_string()
                .contains(r#""values":{"label":"changed"}"#)
        );
        assert_ne!(code_hash(), before, "another setting is another program");
        assert!(
            test.state.plugins.view_root(token).is_some(),
            "a reload keeps the token of a plugin that stays in its folder"
        );

        // A value the form refuses, or a plugin that is not installed, changes nothing.
        let (status, body) = test
            .send(post(
                "/api/plugins/settings",
                json!({ "plugin": "derived-demo", "values": { "label": 5 } }),
            ))
            .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(json(&body)["location"], "label");
        let (status, _) = test
            .send(post(
                "/api/plugins/settings",
                json!({ "plugin": "nobody", "values": {} }),
            ))
            .await;
        assert_eq!(status, StatusCode::NOT_FOUND);
    }
}
