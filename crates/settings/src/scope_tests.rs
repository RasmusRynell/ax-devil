//! Scopes: which settings a person reads and changes, and how a literal secret is shown.

use super::*;
use serde_json::json;
use std::fs;

fn read(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

fn no_env(_: &str) -> Option<String> {
    None
}

/// The shortcuts as they are by default, which a Personal change must hold.
fn default_shortcuts() -> BTreeMap<String, Option<String>> {
    shortcuts::effective(&BTreeMap::new())
        .into_iter()
        .map(|(id, key)| (id.to_owned(), key))
        .collect()
}

const MIXED: &[Setting] = &[
    Setting {
        key: "me.mode",
        kind: Kind::Choice {
            options: &[("a", "A"), ("b", "B")],
            default: "a",
        },
        label: "Mode",
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "server.token",
        kind: Kind::Text { default: "" },
        label: "Token",
        page: None,
        section: "",
        live: true,
        env: true,
        secret: true,
        quick_setup: false,
        scope: Scope::Server,
    },
];

#[test]
fn a_person_reads_and_changes_only_their_scopes_and_a_literal_secret_is_masked() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, MIXED, &[], Arc::new(no_env)).unwrap();
    let edit = |values: &[(&str, &str)], shortcuts: BTreeMap<String, Option<String>>| Change {
        values: values
            .iter()
            .map(|(key, value)| ((*key).to_owned(), json!(value)))
            .collect(),
        shortcuts,
    };
    let none = BTreeMap::new;

    let state = settings
        .replace(
            &[Scope::Server],
            &edit(&[("server.token", "hunter2")], none()),
        )
        .unwrap();
    assert_eq!(read(&path)["settings"]["server.token"], "hunter2");
    assert_eq!(state.values(&[Scope::Server])["server.token"], MASK);
    assert_eq!(state.resolved("server.token"), Some(json!("hunter2")));
    assert!(
        !state
            .values(&[Scope::Personal])
            .contains_key("server.token")
    );
    assert!(!state.values(&[Scope::Server]).contains_key("me.mode"));

    settings
        .replace(&[Scope::Server], &edit(&[("server.token", MASK)], none()))
        .unwrap();
    assert_eq!(
        read(&path)["settings"]["server.token"],
        "hunter2",
        "the masked secret sent back keeps the stored one"
    );
    let state = settings
        .replace(
            &[Scope::Server],
            &edit(&[("server.token", "$TOKEN")], none()),
        )
        .unwrap();
    assert_eq!(state.values(&[Scope::Server])["server.token"], "$TOKEN");

    let outside = edit(&[("server.token", "x"), ("me.mode", "b")], none());
    let error = settings.replace(&[Scope::Server], &outside).unwrap_err();
    assert_eq!(error.location, "me.mode");
    let own_shortcuts = default_shortcuts();
    let error = settings
        .replace(
            &[Scope::Server],
            &edit(&[("server.token", "x")], own_shortcuts.clone()),
        )
        .unwrap_err();
    assert!(error.location.starts_with("shortcuts."), "{error}");

    settings
        .replace(
            &[Scope::Personal],
            &edit(&[("me.mode", "b")], own_shortcuts),
        )
        .unwrap();
    let saved = read(&path);
    assert_eq!(saved["settings"]["me.mode"], "b");
    assert_eq!(
        saved["settings"]["server.token"], "$TOKEN",
        "a change of one scope leaves the other"
    );
}
