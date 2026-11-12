//! CLI settings ownership and startup/cache refusals.
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
        .env("PORT", port.to_string())
        .env_remove("P2_UNSET_CACHE");
    command
}

// One deadline owns the whole CLI invocation, including failure and cleanup.
fn output(mut command: Command) -> std::process::Output {
    let mut child = command
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while child.try_wait().unwrap().is_none() {
        if std::time::Instant::now() >= deadline {
            child.kill().unwrap();
            child.wait().unwrap();
            panic!("CLI did not complete within its deadline");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    child.wait_with_output().unwrap()
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
    let result = output(command(path, backend.address.port()));
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
    std::fs::write(
        &path,
        json!({"version": settings::VERSION, "settings": {"storage.cache_dir": "$P2_UNSET_CACHE"}})
            .to_string(),
    )
    .unwrap();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    let result = output(command(&path, port));
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
    let result = output(command(path, backend.address.port()));
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("refuses Owner access"));
    assert_eq!(std::fs::read(path).unwrap(), before);
}

#[test]
fn launch_reports_never_expose_expanded_defaults_and_storage_stays_actionable() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    for hand_off in [false, true] {
        std::fs::write(
            &path,
            json!({"version": settings::VERSION, "settings": {
                "camera.rtsp_handler": "$AX_TEST_SECRET", "scan.on_startup": false
            }})
            .to_string(),
        )
        .unwrap();
        let mut launch = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
        launch.arg("--config").arg(&path);
        if hand_off {
            launch.arg("--hand-off");
        }
        launch
            .args(["live", "--host", "127.0.0.1", "--overlay", "rtsp"])
            .env("AX_TEST_SECRET", "AX-SYNTHETIC-SECRET")
            .env("AX_DEVIL_EXIT_ON_STDIN_CLOSE", "1")
            .env("PORT", "0");
        let result = output(launch);
        assert!(!result.status.success());
        let stdout = String::from_utf8_lossy(&result.stdout);
        let stderr = String::from_utf8_lossy(&result.stderr);
        assert!(!stdout.contains("AX-SYNTHETIC-SECRET"), "{stdout}");
        assert!(!stderr.contains("AX-SYNTHETIC-SECRET"), "{stderr}");
        assert!(
            format!("{stdout}{stderr}").contains("handler: Unknown live overlay handler type.")
        );
    }
    std::fs::write(
        &path,
        json!({"version": settings::VERSION, "settings": {
            "storage.cache_dir": "$AX_TEST_UNSET", "scan.on_startup": false
        }})
        .to_string(),
    )
    .unwrap();
    let mut launch = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
    launch
        .arg("--config")
        .arg(&path)
        .args(["live", "--host", "127.0.0.1"])
        .env("AX_DEVIL_EXIT_ON_STDIN_CLOSE", "1")
        .env("PORT", "0")
        .env_remove("AX_TEST_UNSET");
    let result = output(launch);
    assert!(!result.status.success());
    let stdout = String::from_utf8_lossy(&result.stdout);
    let payload = stdout
        .lines()
        .find_map(|line| line.strip_prefix("ax-devil-error: "))
        .unwrap();
    let reason: String = serde_json::from_str(payload).unwrap();
    for text in [
        "storage.cache_dir",
        "$AX_TEST_UNSET",
        "Change",
        path.to_str().unwrap(),
    ] {
        assert!(reason.contains(text), "{reason}");
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn catalog_cli_changes_only_the_requested_configuration() {
    let backend = Backend::start().await;
    let a = backend.state.settings.path();
    backend
        .state
        .settings
        .update("scan.on_startup", |_| Some(json!(false)))
        .unwrap();
    let before = std::fs::read(a).unwrap();
    let folder = tempfile::tempdir().unwrap();
    let b = folder.path().join("settings.json");
    #[cfg(unix)]
    std::os::unix::fs::symlink(a, &b).unwrap();
    #[cfg(not(unix))]
    std::fs::write(&b, json!({"version": settings::VERSION}).to_string()).unwrap();
    let result = output(command(&b, backend.address.port()));
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(
        std::fs::read(a).unwrap(),
        before,
        "a different backend must not receive the write"
    );
    assert_eq!(
        settings::Settings::load(&b)
            .unwrap()
            .state()
            .resolved("overlays.default_catalog"),
        Some(json!("built-in:tracking"))
    );
    #[cfg(unix)]
    let alias_parent = folder.path().join("alias-parent");
    #[cfg(unix)]
    std::os::unix::fs::symlink(a.parent().unwrap(), &alias_parent).unwrap();
    #[cfg(unix)]
    let alias = alias_parent.join(a.file_name().unwrap());
    #[cfg(not(unix))]
    let alias = a.parent().unwrap().join(".").join(a.file_name().unwrap());
    let result = output(command(&alias, backend.address.port()));
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("running local backend"));
    assert_eq!(
        backend
            .state
            .settings
            .state()
            .resolved("overlays.default_catalog"),
        Some(json!("built-in:tracking"))
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn catalog_cli_updates_the_owner_before_the_configuration_folder_exists() {
    let folder = tempfile::tempdir().unwrap();
    let config_home = folder.path().join("not-yet-created/config");
    let path = config_home.join("ax-devil/settings.json");
    let backend = Backend::start_with(
        false,
        ax_devil::plugins::Plugins::fixed(handlers::Handlers::built_in()),
        |state| state.settings = std::sync::Arc::new(settings::Settings::load(&path).unwrap()),
    )
    .await;
    assert!(!config_home.exists());
    let mut launch = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
    launch
        .args(["catalog", "use", "built-in:tracking"])
        .env("XDG_CONFIG_HOME", &config_home)
        .env("PORT", backend.address.port().to_string());
    let result = output(launch);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    backend
        .state
        .settings
        .update("playback.prepare_next_video", |_| Some(json!(true)))
        .unwrap();
    let disk: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    assert_eq!(
        disk["settings"]["overlays.default_catalog"], "built-in:tracking",
        "the first-launch CLI choice must survive the owner's next save"
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("running local backend"));
}

#[test]
fn clearing_invalid_cache_storage_refuses_without_touching_the_standard_cache() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let xdg_cache = folder.path().join("cache");
    let protected = xdg_cache.join("ax-devil/frame-index/keep.axidx");
    std::fs::create_dir_all(protected.parent().unwrap()).unwrap();
    std::fs::write(&protected, "standard cache must survive").unwrap();
    let command = |override_folder: Option<&Path>| {
        let mut launch = Command::new(env!("CARGO_BIN_EXE_ax-devil"));
        launch.args(["clear-cache", "--config"]).arg(&path);
        if let Some(folder) = override_folder {
            launch.arg("--cache-dir").arg(folder);
        }
        launch
            .arg("--force")
            .env("XDG_CACHE_HOME", &xdg_cache)
            .env_remove("AX_TEST_BAD_CACHE")
            .env_remove("AX_TEST_BAD_CATALOG")
            .current_dir(folder.path());
        launch
    };
    for value in [
        json!("relative-cache"),
        json!(42),
        json!("$AX_TEST_BAD_CACHE"),
    ] {
        std::fs::write(
            &path,
            json!({"version": settings::VERSION, "settings": {
                "storage.cache_dir": value, "storage.catalog_dir": "$AX_TEST_BAD_CATALOG"
            }})
            .to_string(),
        )
        .unwrap();
        let result = output(command(None));
        assert!(
            !result.status.success(),
            "invalid cache storage must refuse clear-cache"
        );
        let reason = String::from_utf8_lossy(&result.stderr);
        for text in [
            "storage.cache_dir",
            "Invalid storage directory",
            "Change",
            path.to_str().unwrap(),
        ] {
            assert!(reason.contains(text), "{reason}");
        }
        assert_eq!(
            std::fs::read_to_string(&protected).unwrap(),
            "standard cache must survive"
        );
    }
    let explicit = folder.path().join("explicit-cache");
    let removed = explicit.join("frame-index/remove.axidx");
    std::fs::create_dir_all(removed.parent().unwrap()).unwrap();
    std::fs::write(&removed, "explicitly selected cache").unwrap();
    let result = output(command(Some(&explicit)));
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(
        !removed.exists(),
        "an explicit override selects the cache to clear"
    );
    assert!(
        protected.exists(),
        "the standard cache is never an invalid-setting fallback"
    );
}
