use super::*;
use crate::test_support::{TestApp, json};
use axum::{body::Body, http::Request};

async fn get(path: &str) -> (StatusCode, Vec<u8>) {
    let test = TestApp::new();
    test.send(Request::builder().uri(path).body(Body::empty()).unwrap())
        .await
}

fn patch(body: &str) -> Request<Body> {
    Request::builder()
        .method("PATCH")
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
async fn a_patch_changes_only_the_keys_it_names_and_a_refused_one_changes_nothing() {
    let test = TestApp::new();
    let (_, body) = test.send(get_settings_request()).await;
    let first = json(&body);
    assert_eq!(first["shortcuts"]["save-workspace"], "Ctrl+S");
    // The default password is a reference, and what it gives is never sent; the owner is told where the file is.
    assert!(first["resolved"].is_object() && first["resolved"].get("camera.password").is_none());
    assert!(
        first["file"]["path"]
            .as_str()
            .is_some_and(|path| path.ends_with(".json"))
    );
    assert!(first["file"]["version"].is_u64());

    // Two windows that both loaded `first` each save one key: neither undoes the other.
    let (status, theme) = test
        .send(patch(r#"{"set": {"overlays.lane_names": false}}"#))
        .await;
    assert_eq!(status, StatusCode::OK);
    let (status, saved) = test
        .send(patch(
            r#"{"shortcutsSet": {"save-workspace": "Ctrl+Alt+S", "exit": null}, "reset": ["overlays.object_details"]}"#,
        ))
        .await;
    assert_eq!(status, StatusCode::OK);
    let saved = json(&saved);
    assert_eq!(saved["values"]["overlays.lane_names"], false);
    assert!(
        saved["values"].get("ui.theme").is_none(),
        "a Device value never reaches the server"
    );
    assert_eq!(saved["shortcuts"]["exit"], serde_json::Value::Null);
    assert_eq!(saved["shortcuts"]["save-workspace"], "Ctrl+Alt+S");
    assert!(saved["revision"].as_u64() > json(&theme)["revision"].as_u64());
    let (_, read) = test.send(get_settings_request()).await;
    assert_eq!(json(&read), saved);

    for (body, location) in [
        (r#"{"set": {"camera.protocol": "neon"}}"#, "camera.protocol"),
        (r#"{"set": {"ui.theme": "dark"}}"#, "ui.theme"),
        // A scan range that cannot be scanned is refused here, not at the first scan.
        (r#"{"set": {"scan.range": "10.0.0"}}"#, "scan.range"),
        (
            r#"{"set": {"overlays.lane_names": true}, "shortcutsSet": {"save-workspace": "Ctrl+O"}}"#,
            "shortcuts.save-workspace",
        ),
        (r#"{"set": {"ui.zoom": 2}}"#, "ui.zoom"),
    ] {
        let (status, error) = test.send(patch(body)).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{body}");
        assert_eq!(json(&error)["location"], location);
    }
    let (_, after) = test.send(get_settings_request()).await;
    assert_eq!(json(&after), saved);
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

#[test]
fn a_save_is_judged_against_the_state_it_is_written_under_so_a_revocation_in_between_counts() {
    let folder = tempfile::tempdir().unwrap();
    let settings =
        Arc::new(settings::Settings::load(&folder.path().join("settings.json")).unwrap());
    let policy = Policy::Shared {
        password: Some(sessions::Secret::chosen("pw".into()).unwrap()),
        settings: Arc::clone(&settings),
    };
    let toggle = |on| {
        settings
            .update("server.password_full_access", |_| Some(on))
            .unwrap()
    };
    let (on, off) = (toggle(true.into()), toggle(false.into()));
    let mut change = settings::Patch::default();
    change
        .set
        .insert("storage.cache_dir".into(), "/tmp/other".into());

    assert!(admit_save(&policy, Some("pw"), &on, &change).is_ok());
    let refused = admit_save(&policy, Some("pw"), &off, &change).unwrap_err();
    assert_eq!(refused.location, "storage.cache_dir");

    // The reply to a save that revokes Full access is projected by the saved state: no `resolved`.
    toggle(true.into());
    let with_reference = settings
        .update("scan.range", |_| Some("$RANGE".into()))
        .unwrap();
    let reply = |state: &settings::State| snapshot(&settings, None, state, &policy, Some("pw"));
    assert!(reply(&with_reference).resolved.is_some());
    change.set.insert("scan.range".into(), "$RANGE".into());
    change.set.insert("storage.cache_dir".into(), "".into());
    change
        .set
        .insert("server.password_full_access".into(), false.into());
    let saved = settings.patch(&change).unwrap();
    assert!(reply(&saved).resolved.is_none());
}
