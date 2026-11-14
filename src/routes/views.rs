//! A plugin's views: the files of its `views/` folder, served to the sandboxed frame that shows them.
//!
//! The token in the address is the only credential and names one plugin; the files come from nothing but that plugin's
//! `views/` folder: a path that resolves outside it, by `..` or a link, is refused. The sandbox headers are added for
//! the whole prefix by [`security_headers`](crate::access::security_headers), errors included.

use crate::AppState;
use axum::{
    body::Body,
    extract::{Path, State},
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use std::{
    fs::{self, File},
    io::Read,
    path::Path as FsPath,
};

/// The only folder of a plugin that is served.
const VIEWS_FOLDER: &str = "views";

/// The largest file served.
const MOST_BYTES: u64 = 32 << 20;

/// The bytes of the regular file `path` names below `root`, if it resolves to inside `root/views/` (so no `..` or link
/// leads out) and is not larger than [`MOST_BYTES`].
fn read_file(root: &FsPath, path: &str) -> Option<Vec<u8>> {
    // The plugin's own folder may be reached through links: it was installed there.
    let views = root.join(VIEWS_FOLDER).canonicalize().ok()?;
    let file = root.join(path).canonicalize().ok()?;
    // A pipe or a device is refused before it is opened, so nothing waits on it.
    if !file.starts_with(&views) || !fs::metadata(&file).ok()?.is_file() {
        return None;
    }
    let mut bytes = Vec::new();
    File::open(file)
        .ok()?
        .take(MOST_BYTES + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    (bytes.len() as u64 <= MOST_BYTES).then_some(bytes)
}

/// What a browser needs to run a file of a view; text is read as UTF-8.
fn content_type(path: &str) -> String {
    let mime = mime_guess::from_path(path).first_or_octet_stream();
    if mime.type_() == mime_guess::mime::TEXT {
        format!("{mime}; charset=utf-8")
    } else {
        mime.to_string()
    }
}

/// The file of a plugin's view, or 404 for an unknown token, a path outside `views/`, or anything but a regular file.
pub(super) async fn get(
    State(state): State<AppState>,
    Path((token, path)): Path<(String, String)>,
) -> Response {
    let Some(root) = state.plugins.view_root(&token) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let served = tokio::task::spawn_blocking(move || {
        read_file(&root, &path).map(|bytes| (content_type(&path), bytes))
    })
    .await
    .ok()
    .flatten();
    match served {
        Some((kind, bytes)) => (
            [(header::CONTENT_TYPE, kind)],
            [(header::CACHE_CONTROL, "no-store")],
            Body::from(bytes),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        plugins::Plugins,
        test_support::{TestApp, json},
    };
    use axum::{
        body::Body,
        http::{Request, StatusCode, header},
    };
    use handlers::Handlers;
    use serde_json::json;

    fn get(uri: &str) -> Request<Body> {
        Request::builder()
            .uri(uri)
            .header("host", "127.0.0.1:1")
            .body(Body::empty())
            .unwrap()
    }

    #[tokio::test]
    async fn a_view_is_served_in_its_sandbox_and_only_from_its_views_folder() {
        let test = TestApp::over(|folder| {
            Plugins::new(Ok(folder.join("plugins.json")), Handlers::built_in())
        });
        let plugin = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/plugins/derived-demo");
        let install = Request::builder()
            .method("POST")
            .uri("/api/plugins/install")
            .header("content-type", "application/json")
            .body(Body::from(json!({ "path": plugin }).to_string()))
            .unwrap();
        let (status, body) = test.send(install).await;
        assert_eq!(status, StatusCode::OK);
        let listed = json(&body)["plugins"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["source"]["kind"] == "installed")
            .unwrap()["status"]
            .clone();
        let url = listed["views"][0]["url"].as_str().unwrap().to_owned();
        let token = url.split('/').nth(2).unwrap().to_owned();

        let served = test.open(get(&url)).await;
        assert_eq!(served.status(), StatusCode::OK);
        assert_eq!(
            served.headers()[header::CONTENT_TYPE],
            "text/html; charset=utf-8"
        );
        // Beside the view, a file of the plugin that is not under `views/`, a missing file and an unknown token.
        let refused = [
            format!("/views/{token}/ax-devil-plugin.json"),
            format!("/views/{token}/views/missing.html"),
            format!("/views/{token}/views/../ax-devil-plugin.json"),
            format!("/views/{token}/views"),
            "/views/not-a-token/views/review/index.html".to_owned(),
        ];
        let mut responses = vec![served];
        for uri in &refused {
            let response = test.open(get(uri)).await;
            assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
            responses.push(response);
        }
        for response in responses {
            let headers = response.headers();
            let policy = headers[header::CONTENT_SECURITY_POLICY].to_str().unwrap();
            assert!(policy.starts_with("sandbox allow-scripts"), "{policy}");
            // Loads and fetches are limited to this view's own address, even for an unknown token's 404.
            assert!(
                policy.contains("connect-src 127.0.0.1:1/views/"),
                "{policy}"
            );
            assert_eq!(policy.matches("'self'").count(), 1, "{policy}"); // frame-ancestors only
            assert_eq!(headers[header::X_CONTENT_TYPE_OPTIONS], "nosniff");
            assert!(headers.get("x-frame-options").is_none());
            assert_eq!(headers[header::REFERRER_POLICY], "no-referrer");
        }
        // Everywhere else the app's own policy stays.
        let page = test.open(get("/api/info")).await;
        assert!(
            page.headers()[header::CONTENT_SECURITY_POLICY]
                .to_str()
                .unwrap()
                .starts_with("frame-ancestors 'none'")
        );
        assert_eq!(page.headers()["x-frame-options"], "DENY");
    }

    #[cfg(unix)]
    #[test]
    fn a_link_or_a_dot_dot_never_leads_out_of_the_views_folder() {
        let plugin = tempfile::tempdir().unwrap();
        let views = plugin.path().join("views");
        std::fs::create_dir_all(views.join("inner")).unwrap();
        std::fs::write(plugin.path().join("secret.txt"), "secret").unwrap();
        std::fs::write(views.join("inner/page.html"), "page").unwrap();
        std::os::unix::fs::symlink("../secret.txt", views.join("file")).unwrap();
        std::os::unix::fs::symlink("../..", views.join("inner/up")).unwrap();
        let read = |path: &str| super::read_file(plugin.path(), path);
        assert_eq!(read("views/inner/page.html").as_deref(), Some(&b"page"[..]));
        for path in [
            "views/file",
            "views/inner/up/secret.txt",
            "secret.txt",
            "views/../secret.txt",
            "views/inner",
        ] {
            assert_eq!(read(path), None, "{path}");
        }
    }

    #[tokio::test]
    async fn only_a_view_may_send_an_opaque_origin_to_the_local_listener() {
        let test = TestApp::new();
        let status = |uri: &'static str| {
            let request = Request::builder()
                .uri(uri)
                .header("origin", "null")
                .body(Body::empty())
                .unwrap();
            async { test.send(request).await.0 }
        };
        assert_eq!(status("/api/info").await, StatusCode::FORBIDDEN);
        assert_eq!(
            status("/views/token/views/a.js").await,
            StatusCode::NOT_FOUND
        );
        // The host check stays.
        let rebound = Request::builder()
            .uri("/views/token/views/a.js")
            .header("host", "evil.example")
            .header("origin", "null")
            .body(Body::empty())
            .unwrap();
        assert_eq!(test.send(rebound).await.0, StatusCode::FORBIDDEN);
    }
}
