use super::*;
use migrate::Migration;
use serde_json::json;
use std::fs;

/// Every setting at its default, with the theme `theme` and the quick setup done.
fn values(theme: &str) -> BTreeMap<String, Value> {
    let mut all: BTreeMap<String, Value> = TABLE
        .iter()
        .map(|setting| (setting.key.to_owned(), setting.kind.default_value()))
        .collect();
    all.insert("ui.theme".into(), json!(theme));
    all.insert("ui.quick_setup_done".into(), json!(true));
    all
}

fn change(theme: &str) -> Change {
    let shortcuts = shortcuts::effective(&BTreeMap::new())
        .into_iter()
        .map(|(id, key)| (id.to_owned(), key))
        .collect();
    Change {
        values: values(theme),
        shortcuts,
    }
}

fn read(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

fn no_env(_: &str) -> Option<String> {
    None
}

#[test]
fn every_default_is_valid_for_its_own_setting() {
    for setting in TABLE {
        assert_eq!(
            setting.kind.validate(&setting.kind.default_value()),
            Ok(()),
            "{}",
            setting.key
        );
    }
}

#[test]
fn the_view_defaults_take_a_whole_object_in_range_and_sticky_is_not_written_while_it_is_the_default()
 {
    let sticky = TABLE
        .iter()
        .find(|setting| setting.key == "overlays.default_sticky")
        .unwrap();
    let sync = TABLE
        .iter()
        .find(|row| matches!(row.kind, Kind::Sync { .. }))
        .unwrap();
    let sticky_ok = [
        json!({"enabled": true, "timeout_ms": null, "opacity": 0.5}),
        json!({"enabled": false, "timeout_ms": 100_000, "opacity": 0}),
        sticky.kind.default_value(),
    ];
    let sticky_bad = [
        json!({"enabled": true, "opacity": 1}),
        json!({"enabled": true, "timeout_ms": 100_001, "opacity": 1}),
        json!({"enabled": true, "timeout_ms": -1, "opacity": 1}),
        json!({"enabled": true, "timeout_ms": null, "opacity": 1.5}),
        json!({"enabled": true, "timeout_ms": null, "opacity": "1"}),
        json!({"enabled": true, "timeout_ms": null, "opacity": 1, "extra": 0}),
        json!(true),
    ];
    let sync_ok = [
        json!({"tolerance_ms": null}),
        json!({"tolerance_ms": 100_000}),
    ];
    let sync_bad = [
        json!({}),
        json!({"tolerance_ms": 100_001}),
        json!({"tolerance_ms": -1}),
        json!({"tolerance_ms": 50, "extra": 0}),
        json!(50),
    ];
    let filter = TABLE
        .iter()
        .find(|row| row.key == "overlays.default_object_filter")
        .unwrap();
    let open = json!({"min": null, "max": null});
    let ranges = json!({"score": open, "confidence": {"min": 0.2, "max": 1}});
    let huge = json!({"score": {"min": 10_000_000_000_000_000_000_u64, "max": -1.5e300}, "confidence": open});
    let filter_ok = [
        json!({"filter": {}, "ranges": huge, "expression": ""}),
        json!({"filter": {"car": false, "": true}, "ranges": ranges, "expression": "score > 0.4"}),
        filter.kind.default_value(),
    ];
    let filter_bad = [
        json!({"filter": {}, "ranges": ranges}),
        json!({"filter": {"car": 0}, "ranges": ranges, "expression": ""}),
        json!({"filter": {}, "ranges": {"score": open}, "expression": ""}),
        json!({"filter": {}, "ranges": {"score": open, "confidence": {"min": "0"}}, "expression": ""}),
        json!({"filter": {}, "ranges": ranges, "expression": "x".repeat(4097)}),
        json!({"filter": {}, "ranges": ranges, "expression": "", "extra": 0}),
        json!("none"),
    ];
    for (row, ok, bad) in [
        (sticky, &sticky_ok[..], &sticky_bad[..]),
        (sync, &sync_ok, &sync_bad),
        (filter, &filter_ok, &filter_bad),
    ] {
        for ok in ok {
            assert_eq!(row.kind.validate(ok), Ok(()), "{ok}");
        }
        for bad in bad {
            assert!(row.kind.validate(bad).is_err(), "{bad}");
        }
    }
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::load(&path).unwrap();
    let mut edit = change("auto");
    edit.values.insert(
        "overlays.default_sticky".into(),
        json!({"enabled": true, "timeout_ms": 2050, "opacity": 1.0}),
    );
    settings.replace(&edit).unwrap();
    assert!(
        read(&path)["settings"]
            .get("overlays.default_sticky")
            .is_none(),
        "the default writes nothing"
    );
    edit.values.insert(
        "overlays.default_sticky".into(),
        json!({"enabled": false, "timeout_ms": null, "opacity": 0.5}),
    );
    settings.replace(&edit).unwrap();
    assert_eq!(
        read(&path)["settings"]["overlays.default_sticky"],
        json!({"enabled": false, "timeout_ms": null, "opacity": 0.5})
    );
}

#[test]
fn a_saved_change_is_sparse_survives_a_restart_and_keeps_what_this_version_does_not_know() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("ax-devil/settings.json");
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(
        &path,
        r#"{"version": 1, "settings": {"ui.theme": "light", "ui.zoom": "large"},
            "shortcuts": {"exit": null, "gone": "Ctrl+J"}, "extra": [1]}"#,
    )
    .unwrap();

    let settings = Settings::load(&path).unwrap();
    assert_eq!(settings.state().values()["ui.theme"], "light");
    assert_eq!(settings.state().shortcuts()["exit"], None);

    let mut edit = change("dark");
    edit.values
        .insert("ui.quick_setup_done".into(), json!(false));
    edit.shortcuts
        .insert("exit".into(), Some("Ctrl+Alt+Q".into()));
    settings.replace(&edit).unwrap();

    assert_eq!(
        read(&path),
        json!({
            "version": 1,
            "settings": {"ui.theme": "dark", "ui.zoom": "large"},
            "shortcuts": {"exit": "Ctrl+Alt+Q", "gone": "Ctrl+J"},
            "extra": [1],
        }),
        "only what differs from the default is written; unknown entries stay"
    );
    let again = Settings::load(&path).unwrap().state();
    assert_eq!(again.values()["ui.theme"], "dark");
    assert_eq!(again.shortcuts()["exit"].as_deref(), Some("Ctrl+Alt+Q"));
    assert_eq!(
        fs::read_dir(path.parent().unwrap()).unwrap().count(),
        1,
        "no temporary file is left behind"
    );

    let mut reset = change("auto");
    reset
        .values
        .insert("ui.quick_setup_done".into(), json!(false));
    settings.replace(&reset).unwrap();
    assert_eq!(
        read(&path)["settings"],
        json!({"ui.zoom": "large"}),
        "resetting deletes the key"
    );
}

#[test]
fn a_file_that_cannot_be_trusted_stops_loading_naming_the_file_and_key_and_is_left_alone() {
    let cases = [
        ("malformed", "[1, 2", ""),
        ("not an object", "[]", "not a JSON object"),
        ("no version", r#"{"settings": {}}"#, "version"),
        ("fractional version", r#"{"version": 1.5}"#, "version"),
        (
            "newer version",
            r#"{"version": 99}"#,
            "newer ax-devil (v99; this one reads up to v1)",
        ),
        (
            "invalid value",
            r#"{"version": 1, "settings": {"ui.theme": "neon"}}"#,
            "ui.theme",
        ),
        (
            "bad shortcut key",
            r#"{"version": 1, "shortcuts": {"exit": "Ctrl+"}}"#,
            "shortcuts.exit",
        ),
        (
            "duplicate shortcut",
            r#"{"version": 1, "shortcuts": {"exit": "Ctrl+J", "about": "ctrl+j"}}"#,
            "already used by",
        ),
    ];
    for (name, text, reason) in cases {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("settings.json");
        fs::write(&path, text).unwrap();
        let error = Settings::load(&path)
            .err()
            .unwrap_or_else(|| panic!("{name} was accepted"));
        assert_eq!(error.location, path.display().to_string(), "{name}");
        assert!(error.reason.contains(reason), "{name}: {}", error.reason);
        assert_eq!(fs::read_to_string(&path).unwrap(), text, "{name}");
        assert_eq!(
            fs::read_dir(folder.path()).unwrap().count(),
            1,
            "{name}: no backup or other file"
        );
    }
}

fn rename_theme(doc: &mut Map<String, Value>) -> Result<(), String> {
    let settings = doc["settings"].as_object_mut().ok_or("no settings")?;
    let theme = settings.remove("theme").ok_or("no theme")?;
    settings.insert("ui.theme".into(), theme);
    Ok(())
}

fn rename_night(doc: &mut Map<String, Value>) -> Result<(), String> {
    let theme = doc["settings"]["ui.theme"].as_str() == Some("night");
    if theme {
        doc["settings"]["ui.theme"] = json!("dark");
    }
    Ok(())
}

#[test]
fn an_older_file_is_backed_up_migrated_in_order_and_written_at_the_current_version() {
    let chain: [Migration; 2] = [rename_theme, rename_night];
    let load = |text: &str| {
        let folder = tempfile::tempdir().unwrap();
        let path = folder.path().join("settings.json");
        fs::write(&path, text).unwrap();
        let settings = Settings::open(&path, TABLE, &chain, Arc::new(no_env)).unwrap();
        (folder, path, settings)
    };

    let v1 = r#"{"version": 1, "settings": {"theme": "night"}}"#;
    let (folder, path, settings) = load(v1);
    assert_eq!(
        settings.state().values()["ui.theme"],
        "dark",
        "step 2 saw the result of step 1"
    );
    assert_eq!(
        fs::read_to_string(folder.path().join("settings.v1.json")).unwrap(),
        v1
    );
    assert_eq!(
        read(&path)["version"],
        3,
        "the migrated file is written at once"
    );

    let (folder, _, settings) = load(r#"{"version": 2, "settings": {"ui.theme": "night"}}"#);
    assert_eq!(
        settings.state().values()["ui.theme"],
        "dark",
        "a v2 file skips step 1"
    );
    assert!(folder.path().join("settings.v2.json").exists());
}

#[test]
fn a_migration_backup_never_overwrites_an_earlier_one() {
    let chain: [Migration; 1] = [rename_theme];
    let old = r#"{"version": 1, "settings": {"theme": "dark"}}"#;
    let launch = |folder: &Path, text: &str| {
        let path = folder.join("settings.json");
        fs::write(&path, text).unwrap();
        Settings::open(&path, TABLE, &chain, Arc::new(no_env)).unwrap();
    };
    let folder = tempfile::tempdir().unwrap();
    let backup = |name: &str| fs::read_to_string(folder.path().join(name)).unwrap();

    launch(folder.path(), old);
    launch(folder.path(), old);
    assert_eq!(backup("settings.v1.json"), old);
    assert_eq!(
        fs::read_dir(folder.path()).unwrap().count(),
        2,
        "the same bytes reuse the backup"
    );

    let edited = r#"{"version": 1, "settings": {"theme": "light"}}"#;
    launch(folder.path(), edited);
    assert_eq!(backup("settings.v1.json"), old, "the first copy is kept");
    assert_eq!(backup("settings.v1.1.json"), edited);

    let third = r#"{"version": 1, "settings": {"theme": "auto"}}"#;
    launch(folder.path(), third);
    assert_eq!(backup("settings.v1.json"), old);
    assert_eq!(backup("settings.v1.1.json"), edited);
    assert_eq!(backup("settings.v1.2.json"), third);
}

#[test]
fn every_released_settings_file_still_loads() {
    let folder = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/settings");
    let mut fixtures = 0;
    for entry in fs::read_dir(folder).unwrap() {
        let source = entry.unwrap().path();
        let name = source.file_name().unwrap().to_string_lossy().into_owned();
        if !(name.starts_with('v') && name.ends_with(".json")) {
            continue;
        }
        fixtures += 1;
        let copy = tempfile::tempdir().unwrap();
        let path = copy.path().join("settings.json");
        fs::copy(&source, &path).unwrap();

        let settings = Settings::load(&path).unwrap_or_else(|error| panic!("{name}: {error}"));
        let state = settings.state();
        let again = Change {
            values: state.values(),
            shortcuts: state
                .shortcuts()
                .into_iter()
                .map(|(id, key)| (id.to_owned(), key))
                .collect(),
        };
        settings.replace(&again).unwrap();
        assert_eq!(read(&path)["version"], VERSION, "{name}");
        assert_eq!(
            Settings::load(&path).unwrap().state().values(),
            state.values(),
            "{name}"
        );
    }
    assert!(
        fixtures > 0,
        "fixtures/settings holds a file per released version"
    );
}

#[cfg(unix)]
#[test]
fn saving_replaces_a_linked_settings_file_with_a_private_file_and_leaves_the_target_alone() {
    use std::os::unix::fs::PermissionsExt;
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("ax-devil/settings.json");
    let target = folder.path().join("elsewhere.json");
    let original = r#"{"version": 1, "settings": {"ui.theme": "light"}}"#;
    fs::write(&target, original).unwrap();
    fs::create_dir(path.parent().unwrap()).unwrap();
    std::os::unix::fs::symlink(&target, &path).unwrap();

    let settings = Settings::load(&path).unwrap();
    assert_eq!(settings.state().values()["ui.theme"], "light");
    settings.replace(&change("dark")).unwrap();

    assert_eq!(fs::read_to_string(&target).unwrap(), original);
    assert!(!fs::symlink_metadata(&path).unwrap().is_symlink());
    assert_eq!(read(&path)["settings"]["ui.theme"], "dark");
    let mode = fs::metadata(&path).unwrap().permissions().mode();
    assert_eq!(mode & 0o777, 0o600, "the file may hold credentials");

    let private = folder.path().join("a/b/settings.json");
    Settings::load(&private)
        .unwrap()
        .replace(&change("dark"))
        .unwrap();
    let mode = fs::metadata(private.parent().unwrap())
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o077, 0, "the new folder may hold credentials");
}

#[test]
fn a_rejected_replace_changes_neither_state_nor_file() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::load(&path).unwrap();

    let mut unknown = change("dark");
    unknown.values.insert("ui.zoom".into(), json!(2));
    let mut missing = change("dark");
    missing.values.remove("ui.quick_setup_done");
    let mut unknown_command = change("dark");
    unknown_command.shortcuts.insert("teleport".into(), None);
    let mut duplicate = change("dark");
    duplicate
        .shortcuts
        .insert("exit".into(), Some("Ctrl+S".into()));
    let mut reserved = change("dark");
    reserved
        .shortcuts
        .insert("exit".into(), Some("Ctrl+0".into()));
    for (rejected, location) in [
        (change("neon"), "ui.theme"),
        (unknown, "ui.zoom"),
        (missing, "ui.quick_setup_done"),
        (unknown_command, "shortcuts.teleport"),
        (duplicate, "shortcuts.exit"),
        (reserved, "shortcuts.exit"),
    ] {
        let error = settings.replace(&rejected).unwrap_err();
        assert_eq!(error.location, location);
        assert!(error.invalid);
    }

    assert_eq!(settings.state().values()["ui.theme"], "auto");
    assert!(!path.exists());
}

#[test]
fn a_replace_is_checked_against_the_stored_state_and_a_refusal_changes_nothing() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::load(&path).unwrap();
    settings.replace(&change("dark")).unwrap();

    let refused = settings
        .replace_if(&change("light"), |stored| {
            assert_eq!(stored.values()["ui.theme"], "dark");
            Err(Error {
                location: "ui.theme".into(),
                reason: "no".into(),
                invalid: true,
                forbidden: false,
            })
        })
        .unwrap_err();

    assert_eq!(refused.reason, "no");
    assert_eq!(settings.state().values()["ui.theme"], "dark");
    assert_eq!(read(&path)["settings"]["ui.theme"], "dark");
}

#[test]
fn the_video_cache_is_auto_or_whole_mib_in_range_and_a_saved_number_survives_a_restart() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::load(&path).unwrap();
    assert_eq!(settings.state().auto_or_whole("playback.video_cache"), None);
    let with = |cache: Value| {
        let mut all = change("dark");
        all.values.insert("playback.video_cache".into(), cache);
        all
    };

    for rejected in [
        json!(255),
        json!(1048577),
        json!(512.5),
        json!("512"),
        json!(true),
    ] {
        let error = settings.replace(&with(rejected)).unwrap_err();
        assert_eq!(error.location, "playback.video_cache");
    }
    settings.replace(&with(json!(512))).unwrap();
    assert_eq!(
        Settings::load(&path)
            .unwrap()
            .state()
            .auto_or_whole("playback.video_cache"),
        Some(512)
    );
    settings.replace(&with(json!("auto"))).unwrap();
    assert_eq!(settings.state().auto_or_whole("playback.video_cache"), None);
}

#[test]
fn subscribers_see_each_saved_change_and_nothing_for_a_rejected_one() {
    let folder = tempfile::tempdir().unwrap();
    let settings = Settings::load(&folder.path().join("settings.json")).unwrap();
    let mut changes = settings.subscribe();
    assert!(!changes.has_changed().unwrap());

    settings.replace(&change("neon")).unwrap_err();
    assert!(
        !changes.has_changed().unwrap(),
        "a rejected change is not announced"
    );

    settings.replace(&change("dark")).unwrap();
    assert!(changes.has_changed().unwrap());
    assert_eq!(changes.borrow_and_update().values()["ui.theme"], "dark");
}

#[test]
fn without_full_access_a_save_cannot_change_a_full_access_row_or_add_a_reference_but_can_resend_the_stored()
 {
    let folder = tempfile::tempdir().unwrap();
    let settings = Settings::load(&folder.path().join("settings.json")).unwrap();
    let mut edit = change("light");
    settings.replace(&edit).unwrap();
    let refuse = |edit: &Change| settings.state().refuse_without_full_access(edit);
    assert!(
        refuse(&edit).is_ok(),
        "the stored values, references included"
    );

    edit.values
        .insert("storage.cache_dir".into(), json!("/tmp/other"));
    assert_eq!(refuse(&edit).unwrap_err().location, "storage.cache_dir");

    let mut edit = change("light");
    edit.values.insert("scan.range".into(), json!("$RANGE"));
    let refused = refuse(&edit).unwrap_err();
    assert!((refused.location.as_str(), refused.forbidden) == ("scan.range", true));

    let mut edit = change("light");
    edit.values
        .insert("scan.logins".into(), json!([["admin", "$SECRET"]]));
    assert_eq!(refuse(&edit).unwrap_err().location, "scan.logins");
}
