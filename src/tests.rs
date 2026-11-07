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
        .header("host", "127.0.0.1")
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

fn get_settings_request() -> Request<Body> {
    Request::builder()
        .uri("/api/settings")
        .body(Body::empty())
        .unwrap()
}

#[tokio::test]
async fn settings_are_replaced_whole_and_a_rejected_change_leaves_them_as_they_were() {
    let test = TestApp::new();
    let (_, body) = test.send(get_settings_request()).await;
    let mut change = json(&body);
    assert_eq!(change["shortcuts"]["save-workspace"], "Ctrl+S");
    // The default password is a reference, and what it gives is never sent; the owner is told where the file is.
    assert!(change["resolved"].is_object() && change["resolved"].get("camera.password").is_none());
    assert!(
        change["file"]["path"]
            .as_str()
            .is_some_and(|path| path.ends_with(".json"))
    );
    assert!(change["file"]["version"].is_u64());
    change["values"]["ui.theme"] = "dark".into();
    change["shortcuts"]["save-workspace"] = "Ctrl+Alt+S".into();
    change["shortcuts"]["exit"] = serde_json::Value::Null;
    let body = change.to_string();

    let (status, saved) = test.send(put(&body)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json(&saved)["values"]["ui.theme"], "dark");
    let (_, read) = test.send(get_settings_request()).await;
    assert_eq!(json(&read), json(&saved));
    assert_eq!(json(&read)["shortcuts"]["exit"], serde_json::Value::Null);

    let bad_theme = body.replace("\"dark\"", "\"neon\"");
    let (status, error) = test.send(put(&bad_theme)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(json(&error)["location"], "ui.theme");
    // A scan range that cannot be scanned is refused here, not at the first scan.
    let bad_range = body.replace("\"192.168.1.0/24\"", "\"10.0.0\"");
    let (status, error) = test.send(put(&bad_range)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(json(&error)["location"], "scan.range");
    let taken = body.replace("\"Ctrl+Alt+S\"", "\"Ctrl+O\"");
    let (status, error) = test.send(put(&taken)).await;
    assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(json(&error)["location"], "shortcuts.save-workspace");

    let (_, after) = test.send(get_settings_request()).await;
    assert_eq!(json(&after), json(&saved));
}

#[tokio::test]
async fn a_window_is_saved_beside_the_others_and_a_corrupt_file_is_no_window() {
    let test = TestApp::new();
    let window = |method: &str, name: &str, body: &str| {
        Request::builder()
            .method(method)
            .uri(format!("/api/windows/{name}"))
            .header("content-type", "application/json")
            .body(Body::from(body.to_owned()))
            .unwrap()
    };
    let main = r#"{"x":-20,"y":30,"width":1280,"height":800,"maximized":true}"#;

    assert_eq!(
        test.send(window("GET", "main", "")).await.0,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        test.send(window("PUT", "main", main)).await.0,
        StatusCode::NO_CONTENT
    );
    let other = r#"{"x":0,"y":0,"width":300,"height":200,"maximized":false}"#;
    assert_eq!(
        test.send(window("PUT", "tools-panel", other)).await.0,
        StatusCode::NO_CONTENT
    );
    let (status, body) = test.send(window("GET", "main", "")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(json(&body), json(main.as_bytes()));
    assert_eq!(
        test.send(window("PUT", "Main_1", main)).await.0,
        StatusCode::UNPROCESSABLE_ENTITY
    );

    std::fs::write(test._folder.path().join("windows.json"), "{ not json").unwrap();
    assert_eq!(
        test.send(window("GET", "main", "")).await.0,
        StatusCode::NO_CONTENT
    );
}

#[tokio::test]
async fn catalogs_are_polled_in_the_background_so_a_file_that_appears_is_listed() {
    let folder = tempfile::tempdir().unwrap();
    let catalog_dir = folder.path().join("catalogs");
    std::fs::create_dir(&catalog_dir).unwrap();
    let library = Library::with_folder(catalog_dir.clone());
    let settings = Arc::new(settings::Settings::load(&folder.path().join("s.json")).unwrap());
    let cache = folder.path().join("cache");
    let videos = crate::video_cache::start(&settings, &cache);
    let (_stop, stopping) = watch::channel(false);
    let state = AppState::new(
        settings,
        Policy::Local,
        videos,
        stopping,
        plugins::Plugins::fixed(handlers::Handlers::built_in()),
        &cache,
    )
    .with_catalogs(library.clone());
    let mut changes = library.changes();

    std::fs::write(catalog_dir.join("night.json"), "{}").unwrap();

    tokio::time::timeout(Duration::from_secs(10), changes.changed())
        .await
        .expect("the poller finds the file")
        .unwrap();
    assert!(
        state
            .catalogs
            .documents()
            .iter()
            .any(|info| info.id.to_string() == "user:night.json")
    );
}
