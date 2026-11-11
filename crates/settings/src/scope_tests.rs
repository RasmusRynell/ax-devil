//! A secret is write-only: what a client reads of it, and how a list of logins is saved by id.

use super::*;
use serde_json::json;
use std::fs;

fn read(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

fn no_env(_: &str) -> Option<String> {
    None
}

const MIXED: &[Setting] = &[
    Setting {
        key: "me.mode",
        kind: Kind::Choice {
            options: &[("a", "A"), ("b", "B")],
            toggle: false,
            default: "a",
        },
        label: "Mode",
        description: None,
        page: None,
        section: "",
        live: true,
        env: false,
        secret: false,
        full_access: false,
        quick_setup: false,
        scope: Scope::Personal,
    },
    Setting {
        key: "server.token",
        kind: Kind::Text { default: "" },
        label: "Token",
        description: None,
        page: None,
        section: "",
        live: true,
        env: true,
        secret: true,
        full_access: false,
        quick_setup: false,
        scope: Scope::Server,
    },
];

#[test]
fn a_secret_reads_back_only_as_its_state_and_a_patch_that_does_not_name_it_keeps_it() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, MIXED, &[], Arc::new(no_env)).unwrap();
    let edit = |token: &str| Patch {
        set: BTreeMap::from([
            ("me.mode".to_owned(), json!("b")),
            ("server.token".to_owned(), json!(token)),
        ]),
        ..Patch::default()
    };

    let state = settings.patch(&edit("hunter2")).unwrap();
    assert_eq!(read(&path)["settings"]["server.token"], "hunter2");
    assert_eq!(state.values()["server.token"], json!({"state": "literal"}));
    assert!(!json!(state.values()).to_string().contains("hunter2"));
    assert_eq!(state.resolved("server.token"), Some(json!("hunter2")));

    let other = Patch {
        set: BTreeMap::from([("me.mode".to_owned(), json!("a"))]),
        ..Patch::default()
    };
    settings.patch(&other).unwrap();
    assert_eq!(
        read(&path)["settings"]["server.token"],
        "hunter2",
        "a secret the patch does not name is untouched"
    );
    let state = settings.patch(&edit("$TOKEN")).unwrap();
    assert_eq!(
        state.values()["server.token"],
        json!({"state": "reference", "name": "TOKEN"})
    );
    let state = settings.patch(&edit("")).unwrap();
    assert_eq!(state.values()["server.token"], json!({"state": "unset"}));
}

const LOGINS: &[Setting] = &[Setting {
    key: "server.logins",
    kind: Kind::Records {
        fields: &["user", "password"],
        default: &[&["$TARGET_USER", "$TARGET_PASS"]],
    },
    label: "Logins",
    description: None,
    page: None,
    section: "",
    live: true,
    env: true,
    secret: true,
    full_access: false,
    quick_setup: false,
    scope: Scope::Server,
}];

fn logins(path: &Path) -> Value {
    read(path)["settings"]["server.logins"].clone()
}

#[test]
fn logins_are_saved_whole_by_id_and_no_password_is_read_back() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, LOGINS, &[], Arc::new(no_env)).unwrap();
    let replace = |records: Vec<Value>| {
        settings.patch(&Patch {
            set: BTreeMap::from([("server.logins".to_owned(), Value::Array(records))]),
            ..Patch::default()
        })
    };
    let new = |user: &str, password: &str| json!({"user": user, "password": password});
    let with_id = |id: &Value, user: &str| json!({"id": id, "user": user});

    let state = replace(vec![new("root", "hunter2"), new("ops", "$OPS_PASS")]).unwrap();
    let shown = state.values()["server.logins"].clone();
    assert!(!json!(state.values()).to_string().contains("hunter2"));
    assert_eq!(shown[0]["password"], json!({"state": "literal"}));
    assert_eq!(
        shown[1]["password"],
        json!({"state": "reference", "name": "OPS_PASS"})
    );
    let (root, ops) = (shown[0]["id"].clone(), shown[1]["id"].clone());
    assert_ne!(root, ops);

    // Reordered, one user changed and one record added: each kept record keeps its own password.
    replace(vec![
        with_id(&ops, "ops"),
        with_id(&root, "admin"),
        new("new", "typed"),
    ])
    .unwrap();
    let stored = logins(&path);
    assert_eq!(
        stored[0],
        json!({"id": ops, "user": "ops", "password": "$OPS_PASS"})
    );
    assert_eq!(
        stored[1],
        json!({"id": root, "user": "admin", "password": "hunter2"})
    );
    assert_eq!(stored[2]["password"], "typed");
    assert_ne!(stored[2]["id"], root);

    // A password with an id sets that record's password; a record left out is removed.
    replace(vec![
        json!({"id": root, "user": "admin", "password": "changed"}),
    ])
    .unwrap();
    assert_eq!(
        logins(&path),
        json!([{"id": root, "user": "admin", "password": "changed"}])
    );

    // An unknown or repeated id, and a new record without a password, are refused and nothing is saved.
    for refused in [
        vec![with_id(&json!("nobody"), "x")],
        vec![with_id(&root, "a"), with_id(&root, "b")],
        vec![json!({"user": "x"})],
    ] {
        let error = replace(refused).unwrap_err();
        assert!(
            error.invalid && error.location == "server.logins",
            "{error}"
        );
    }
    assert_eq!(logins(&path)[0]["password"], "changed");
}

#[test]
fn a_file_without_login_ids_loads_and_each_record_gets_one() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let old = json!({"version": 1, "settings": {"server.logins": [
        {"user": "root", "password": "one"}, {"id": "position-0", "user": "root", "password": "two"},
        {"user": "root", "password": "three"}
    ]}});
    fs::write(&path, old.to_string()).unwrap();
    let settings = Settings::open(&path, LOGINS, &[], Arc::new(no_env)).unwrap();
    let shown = settings.state().values()["server.logins"].clone();
    let (first, second) = (shown[0]["id"].clone(), shown[1]["id"].clone());
    assert_eq!(first, "position-1");
    // Ids come from position, skipping one already present, and the same file loads to the same ids.
    let ids = |shown: &Value| {
        shown
            .as_array()
            .unwrap()
            .iter()
            .map(|record| record["id"].clone())
            .collect::<Vec<_>>()
    };
    assert_eq!(
        ids(&shown),
        [
            json!("position-1"),
            json!("position-0"),
            json!("position-2")
        ]
    );
    let again = Settings::open(&path, LOGINS, &[], Arc::new(no_env)).unwrap();
    assert_eq!(ids(&again.state().values()["server.logins"]), ids(&shown));
    assert_ne!(first, second);

    settings
        .patch(&Patch {
            set: BTreeMap::from([("server.logins".to_owned(), json!([with_user(&second, "x")]))]),
            ..Patch::default()
        })
        .unwrap();
    assert_eq!(
        logins(&path),
        json!([{"id": second, "user": "x", "password": "two"}])
    );
}

fn with_user(id: &Value, user: &str) -> Value {
    json!({"id": id, "user": user})
}
