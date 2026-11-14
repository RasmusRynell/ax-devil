use super::*;

#[test]
fn invalid_saved_preferences_survive_unrelated_saves_until_edited() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let mut invalid = Map::new();
    for key in [
        "playback.video_cache",
        "overlays.default_sync",
        "overlays.default_sticky",
        "overlays.default_object_filter",
    ] {
        invalid.insert(key.into(), json!("invalid"));
    }
    invalid.insert(
        "overlays.default_sticky".into(),
        json!({"enabled":false,"timeout_ms":"damaged","opacity":0.4}),
    );
    let document = json!({"version": 1, "settings": invalid, "shortcuts": {"exit": "Ctrl+"}});
    fs::write(&path, document.to_string()).unwrap();
    let loaded = Settings::load(&path).unwrap();
    let defaults = Settings::load(&folder.path().join("defaults.json")).unwrap();
    assert_eq!(loaded.state().values(), defaults.state().values());
    assert_eq!(
        loaded.state().shortcuts(shortcuts::COMMANDS),
        defaults.state().shortcuts(shortcuts::COMMANDS)
    );
    assert_eq!(read(&path), document);
    let mut recovered = loaded.state().recovered();
    recovered.sort();
    assert_eq!(recovered, invalid.keys().cloned().collect::<Vec<_>>());
    assert_eq!(
        loaded.state().recovered_shortcuts(shortcuts::COMMANDS),
        vec!["exit"]
    );
    let unrelated = Patch {
        set: BTreeMap::from([("scan.on_startup".into(), json!(false))]),
        ..Patch::default()
    };
    loaded.patch(&unrelated).unwrap();
    for (key, value) in &invalid {
        assert_eq!(read(&path)["settings"][key], *value, "{key}");
    }
    assert_eq!(read(&path)["shortcuts"]["exit"], "Ctrl+");
    loaded
        .patch(&Patch {
            set: BTreeMap::from([("camera.overlay".into(), json!("mqtt"))]),
            ..Patch::default()
        })
        .unwrap();
    assert_eq!(read(&path)["settings"]["camera.overlay"], "mqtt");
    assert_eq!(
        read(&path)["settings"]["overlays.default_sticky"],
        invalid["overlays.default_sticky"]
    );
    // An explicit edit can reset a recovered value to the effective default too.
    loaded
        .update("overlays.default_sticky", |value| Some(value.clone()))
        .unwrap();
    assert!(
        read(&path)["settings"]
            .get("overlays.default_sticky")
            .is_none()
    );
    loaded
        .patch(&Patch {
            shortcuts_reset: ["exit".into()].into(),
            ..Patch::default()
        })
        .unwrap();
    let reloaded = Settings::load(&path).unwrap();
    assert!(
        !reloaded
            .state()
            .recovered()
            .contains(&"overlays.default_sticky".to_owned())
    );
    assert!(
        reloaded
            .state()
            .recovered_shortcuts(shortcuts::COMMANDS)
            .is_empty()
    );
    let mut malformed_shortcut = Patch::default();
    malformed_shortcut
        .shortcuts_set
        .insert("exit".into(), Some("Ctrl+".into()));
    assert_eq!(
        loaded.patch(&malformed_shortcut).unwrap_err().location,
        "shortcuts.exit"
    );
    for (key, value) in invalid {
        let mut edit = Patch::default();
        edit.set.insert(key.clone(), value);
        assert_eq!(loaded.patch(&edit).unwrap_err().location, key);
    }
}

#[cfg(unix)]
#[test]
fn saving_preserves_existing_regular_file_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    fs::write(&path, r#"{"version":1}"#).unwrap();
    for mode in [0o600, 0o640] {
        fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
        Settings::load(&path)
            .unwrap()
            .patch(&change("mqtt"))
            .unwrap();
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            mode
        );
        assert_eq!(read(&path)["settings"]["camera.overlay"], "mqtt");
    }
}

#[test]
fn saved_sticky_opacity_is_clamped_without_changing_valid_neighbors() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    for (opacity, expected) in [(1.5, 1.0), (-0.5, 0.0)] {
        let raw = json!({"enabled": false, "timeout_ms": 5000, "opacity": opacity});
        fs::write(
            &path,
            json!({"version":1, "settings":{"overlays.default_sticky":raw}}).to_string(),
        )
        .unwrap();
        let loaded = Settings::load(&path).unwrap();
        assert_eq!(
            loaded.state().values()["overlays.default_sticky"],
            json!({"enabled":false,"timeout_ms":5000,"opacity":expected})
        );
        loaded
            .update("camera.overlay", |_| Some(json!("mqtt")))
            .unwrap();
        assert_eq!(read(&path)["settings"]["overlays.default_sticky"], raw);
        assert!(
            loaded
                .update("overlays.default_sticky", |_| Some(raw))
                .is_err()
        );
    }
    fs::write(
        &path,
        json!({"version":1, "settings":{"overlays.default_sticky":
        {"enabled":false,"opacity":0.5,"extra":"kept"}}})
        .to_string(),
    )
    .unwrap();
    assert_eq!(
        Settings::load(&path).unwrap().state().values()["overlays.default_sticky"],
        json!({"enabled":false,"timeout_ms":null,"opacity":0.5})
    );
}

#[test]
fn failed_atomic_replacement_cleans_up_and_reports_the_failure() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    fs::create_dir(&path).unwrap();
    assert!(crate::file::write_atomically(&path, b"new contents").is_err());
    let entries: Vec<_> = fs::read_dir(folder.path())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect();
    assert_eq!(entries, vec![path.clone()]);
    assert!(path.is_dir());
}
