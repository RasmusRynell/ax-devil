//! CLI default persistence across a running backend's later saves.
use crate::common::Backend;
use serde_json::{Value, json};
use std::{path::Path, process::Command};

fn command(config: &Path, port: u16) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
    command
        .arg("catalog")
        .arg("--config")
        .arg(config)
        .args(["use", "built-in:tracking"])
        .env("PORT", port.to_string());
    command
}

#[tokio::test(flavor = "multi_thread")]
async fn catalog_cli_choice_survives_a_running_backends_save_and_is_the_new_default() {
    let backend = Backend::start().await;
    let path = backend.state.settings.path();
    // The file may be absent until the service's first write.
    backend
        .state
        .settings
        .update("scan.on_startup", |_| Some(json!(false)))
        .unwrap();
    let result = command(path, backend.address.port()).output().unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let state = backend
        .state
        .settings
        .patch(&settings::Patch {
            set: std::collections::BTreeMap::from([(
                "playback.prepare_next_video".to_owned(),
                json!(true),
            )]),
            ..Default::default()
        })
        .unwrap();
    assert_eq!(
        state.resolved("overlays.default_catalog"),
        Some(json!("built-in:tracking"))
    );
    let disk: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    assert_eq!(
        disk["settings"]["overlays.default_catalog"],
        "built-in:tracking"
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("running local backend"));
    let snapshot = backend.state.settings.state();
    assert_eq!(
        snapshot.resolved("overlays.default_catalog"),
        Some(json!("built-in:tracking"))
    );
}

#[test]
fn catalog_cli_writes_the_file_only_when_the_local_port_refuses_connection() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    std::fs::write(&path, json!({"version": settings::VERSION}).to_string()).unwrap();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let result = command(&path, port).output().unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("no local backend running"));
    assert_eq!(
        settings::Settings::load(&path)
            .unwrap()
            .state()
            .resolved("overlays.default_catalog"),
        Some(json!("built-in:tracking"))
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn catalog_cli_never_writes_around_a_running_backends_owner_refusal() {
    let backend = Backend::start_with(
        false,
        ax_devil::plugins::Plugins::fixed(handlers::Handlers::built_in()),
        |state| {
            state.policy = std::sync::Arc::new(ax_devil::access::Policy::Shared {
                password: None,
                settings: state.settings.clone(),
            });
        },
    )
    .await;
    let path = backend.state.settings.path();
    backend
        .state
        .settings
        .update("scan.on_startup", |_| Some(json!(false)))
        .unwrap();
    let before = std::fs::read(path).unwrap();
    let result = command(path, backend.address.port()).output().unwrap();
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("refuses Owner access"));
    assert_eq!(std::fs::read(path).unwrap(), before);
}

#[test]
fn invalid_storage_startup_prints_the_fix_and_exits_nonzero() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    std::fs::write(&path, json!({"version": settings::VERSION, "settings": {"storage.cache_dir": "$P2_INVALID_STORAGE", "camera.password": "MUST-NOT-REACH-ERROR", "scan.on_startup": false}}).to_string()).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
        .arg("--config")
        .arg(&path)
        .env("PORT", "0")
        .env_remove("P2_INVALID_STORAGE")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while child.try_wait().unwrap().is_none() {
        if std::time::Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("invalid storage must stop startup");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let result = child.wait_with_output().unwrap();
    assert!(!result.status.success());
    let reason = String::from_utf8_lossy(&result.stderr);
    for text in [
        "Invalid storage directory",
        "storage.cache_dir",
        "P2_INVALID_STORAGE",
        "Change",
        path.to_str().unwrap(),
    ] {
        assert!(reason.contains(text), "{reason}");
    }
    assert!(!reason.contains("MUST-NOT-REACH-ERROR"));
}
