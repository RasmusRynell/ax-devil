//! `$NAME` references: stored as written, expanded where used, and checked as expanded.

use super::*;
use serde_json::json;
use std::fs;

const fn row(key: &'static str, kind: Kind, secret: bool) -> Setting {
    Setting {
        key,
        kind,
        label: "Row",
        description: None,
        page: None,
        section: "",
        live: false,
        env: true,
        secret,
        quick_setup: false,
        scope: Scope::Server,
    }
}

const NET: &[Setting] = &[
    row("net.address", Kind::Text { default: "" }, true),
    row(
        "net.mode",
        Kind::Choice {
            options: &[("a", "A"), ("b", "B")],
            toggle: false,
            default: "a",
        },
        false,
    ),
    row(
        "net.port",
        Kind::Whole {
            min: 1,
            max: 65535,
            default: 554,
        },
        false,
    ),
    row("net.dir", Kind::Folder, false),
];

fn lookup(name: &str) -> Option<String> {
    match name {
        "ADDRESS" => Some("10.0.0.2".to_owned()),
        "MODE" => Some("secret-mode".to_owned()),
        "PORT" => Some(" 8554 ".to_owned()),
        "LOW" => Some("0".to_owned()),
        "PORT2" => Some("9000".to_owned()),
        "WORD" => Some("rtsp".to_owned()),
        "DIR" => Some("/data/cache".to_owned()),
        "REL" => Some("cache".to_owned()),
        "HOME" => Some("/home/me".to_owned()),
        _ => None,
    }
}

/// The settings over `NET` in a fresh folder, and the file's path.
fn open() -> (tempfile::TempDir, PathBuf, Settings) {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, NET, &[], Arc::new(lookup)).unwrap();
    (folder, path, settings)
}

/// A change of the four `NET` rows; the rows not given are at their defaults.
fn edit(given: &[(&str, Value)]) -> Change {
    let mut values: BTreeMap<String, Value> = NET
        .iter()
        .map(|setting| (setting.key.to_owned(), setting.kind.default_value()))
        .collect();
    values.extend(
        given
            .iter()
            .map(|(key, value)| ((*key).to_owned(), value.clone())),
    );
    let shortcuts = shortcuts::effective(&BTreeMap::new())
        .into_iter()
        .map(|(id, key)| (id.to_owned(), key))
        .collect();
    Change { values, shortcuts }
}

#[test]
fn a_reference_is_stored_as_written_and_a_text_or_choice_is_read_only_when_used() {
    let (_folder, path, settings) = open();
    // A choice is not checked on save; used, what is not an option gives the default.
    let state = settings
        .replace(&edit(&[("net.mode", json!("$MODE"))]))
        .unwrap();
    assert_eq!(state.values()["net.mode"], "$MODE");
    assert_eq!(state.resolved("net.mode"), Some(json!("a")));
    let state = settings
        .replace(&edit(&[("net.address", json!("$UNSET"))]))
        .unwrap();
    assert_eq!(
        state.resolved("net.address"),
        Some(json!("")),
        "an unset variable is empty text"
    );

    let state = settings
        .replace(&edit(&[("net.address", json!("$ADDRESS"))]))
        .unwrap();
    let stored: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(
        stored["settings"]["net.address"], "$ADDRESS",
        "the reference is stored, not the value"
    );
    assert_eq!(state.resolved("net.address"), Some(json!("10.0.0.2")));
}

#[test]
fn a_number_may_be_a_reference_that_resolves_to_a_whole_number_in_range() {
    let (_folder, path, settings) = open();
    let port = |value: Value| edit(&[("net.port", value)]);

    let state = settings.replace(&port(json!("$PORT"))).unwrap();
    assert_eq!(
        state.values()["net.port"],
        "$PORT",
        "the reference stays as written for the editor and the file"
    );
    assert_eq!(state.resolved("net.port"), Some(json!(8554)));
    assert_eq!(
        state.references(),
        BTreeMap::from([("net.port".to_owned(), json!(8554))]),
        "a form that starts from the value is given it; a secret is never listed"
    );
    assert_eq!(
        state.stored("net.port"),
        Some(&json!(8554)),
        "a caller that copies the number never meets text"
    );
    assert_eq!(state.auto_or_whole("net.port"), Some(8554));
    assert_eq!(
        Settings::open(&path, NET, &[], Arc::new(lookup))
            .unwrap()
            .state()
            .resolved("net.port"),
        Some(json!(8554))
    );

    let state = settings
        .update("net.port", |_| Some(json!("$PORT2")))
        .unwrap();
    assert_eq!(
        state.stored("net.port"),
        Some(&json!(9000)),
        "an update refreshes it"
    );
    let state = settings.replace(&port(json!(7000))).unwrap();
    assert_eq!(
        state.stored("net.port"),
        Some(&json!(7000)),
        "a literal replaces it"
    );
    let state = settings.replace(&port(json!("$PORT"))).unwrap();
    assert_eq!(state.stored("net.port"), Some(&json!(8554)));

    for rejected in [
        json!("$LOW"),
        json!("$WORD"),
        json!("$UNSET"),
        json!("8554"),
        json!("port"),
    ] {
        let error = settings.replace(&port(rejected)).unwrap_err();
        assert_eq!(error.location, "net.port");
        assert!(error.invalid);
    }
    assert_eq!(settings.state().resolved("net.port"), Some(json!(8554)));
}

#[test]
fn a_folder_is_a_reference_or_a_home_relative_or_absolute_path_that_need_not_exist() {
    let (_folder, _path, settings) = open();
    let dir = |value: Value| edit(&[("net.dir", value)]);
    let resolved = |settings: &Settings| settings.state().folder("net.dir");

    assert_eq!(resolved(&settings), None, "empty is the standard folder");
    for rejected in [
        json!("cache"),
        json!("$UNSET"),
        json!("$REL"),
        json!("~me/x"),
        json!(7),
    ] {
        let error = settings.replace(&dir(rejected)).unwrap_err();
        assert_eq!(error.location, "net.dir");
    }
    for (stored, used) in [
        (json!("/unplugged/drive"), "/unplugged/drive"),
        (json!("$DIR"), "/data/cache"),
        (json!("~"), "/home/me"),
        (json!("~/cache"), "/home/me/cache"),
    ] {
        let state = settings.replace(&dir(stored.clone())).unwrap();
        assert_eq!(state.values()["net.dir"], stored);
        assert_eq!(resolved(&settings), Some(PathBuf::from(used)));
    }
    settings.replace(&dir(json!(""))).unwrap();
    assert_eq!(resolved(&settings), None);
}

#[test]
fn a_saved_reference_that_does_not_resolve_loads_and_falls_back_but_is_refused_on_save() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    fs::write(
        &path,
        r#"{"version": 1, "settings": {"net.port": "$UNSET", "net.dir": "$REL", "net.mode": "$MODE"}}"#,
    )
    .unwrap();
    let settings = Settings::open(&path, NET, &[], Arc::new(lookup)).unwrap();

    let state = settings.state();
    assert_eq!(state.values()["net.port"], "$UNSET", "kept as written");
    assert_eq!(
        state.resolved("net.port"),
        Some(json!(554)),
        "the default is used"
    );
    assert_eq!(state.stored("net.port"), Some(&json!(554)));
    assert_eq!(state.auto_or_whole("net.port"), Some(554));
    assert_eq!(
        state.stored("net.mode"),
        Some(&json!("$MODE")),
        "text stays as written"
    );
    assert_eq!(state.folder("net.dir"), None, "the standard folder is used");

    let kept = settings.replace(&edit(&[("net.port", json!("$UNSET"))]));
    assert_eq!(kept.unwrap_err().location, "net.port");
    let other = settings.update("net.address", |_| Some(json!("literal")));
    assert!(other.is_ok(), "another setting can still be changed");
    let fixed = edit(&[("net.port", json!(8554))]);
    let state = settings.replace(&fixed).unwrap();
    assert_eq!(state.resolved("net.port"), Some(json!(8554)));
}
