//! How a literal secret is shown, and how a masked one sent back is matched to the stored one.

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
        quick_setup: false,
        scope: Scope::Server,
    },
];

#[test]
fn a_literal_secret_is_masked_and_the_masked_value_sent_back_keeps_the_stored_one() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, MIXED, &[], Arc::new(no_env)).unwrap();
    let edit = |token: &str| Change {
        values: BTreeMap::from([
            ("me.mode".to_owned(), json!("b")),
            ("server.token".to_owned(), json!(token)),
        ]),
        shortcuts: BTreeMap::new(),
    };

    let state = settings.replace(&edit("hunter2")).unwrap();
    assert_eq!(read(&path)["settings"]["server.token"], "hunter2");
    assert_eq!(state.values()["server.token"], MASK);
    assert_eq!(state.resolved("server.token"), Some(json!("hunter2")));

    settings.replace(&edit(MASK)).unwrap();
    assert_eq!(
        read(&path)["settings"]["server.token"],
        "hunter2",
        "the masked secret sent back keeps the stored one"
    );
    let state = settings.replace(&edit("$TOKEN")).unwrap();
    assert_eq!(state.values()["server.token"], "$TOKEN");
    assert_eq!(read(&path)["settings"]["me.mode"], "b");
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
    quick_setup: false,
    scope: Scope::Server,
}];

#[test]
fn a_literal_password_in_a_list_of_logins_is_masked_and_a_masked_one_keeps_its_login_s_password() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = Settings::open(&path, LOGINS, &[], Arc::new(no_env)).unwrap();
    let login = |user: &str, password: &str| json!({"user": user, "password": password});
    let replace = |logins: Vec<Value>| {
        let change = Change {
            values: BTreeMap::from([("server.logins".to_owned(), Value::Array(logins))]),
            shortcuts: BTreeMap::new(),
        };
        settings.replace(&change)
    };
    let shown = |state: &State| state.values()["server.logins"].clone();

    let state = replace(vec![login("root", "hunter2"), login("ops", "$OPS_PASS")]).unwrap();
    assert_eq!(
        shown(&state),
        json!([login("root", MASK), login("ops", "$OPS_PASS")])
    );
    // Sent back in another order with one more login: each masked password finds its own login.
    let state = replace(vec![
        login("ops", "$OPS_PASS"),
        login("root", MASK),
        login("new", "typed"),
    ])
    .unwrap();
    let stored = read(&path)["settings"]["server.logins"].clone();
    assert_eq!(
        stored,
        json!([
            login("ops", "$OPS_PASS"),
            login("root", "hunter2"),
            login("new", "typed")
        ])
    );
    assert_eq!(shown(&state)[1], login("root", MASK));
    // Only the user of a login changed: its masked password is the one at that position.
    let state = replace(vec![
        login("ops", "$OPS_PASS"),
        login("admin", MASK),
        login("new", MASK),
    ])
    .unwrap();
    assert_eq!(
        read(&path)["settings"]["server.logins"],
        json!([
            login("ops", "$OPS_PASS"),
            login("admin", "hunter2"),
            login("new", "typed")
        ])
    );
    assert_eq!(shown(&state)[1], login("admin", MASK));
    // A masked password with no stored one to keep is refused, and nothing is saved.
    let error = replace(vec![
        login("ops", "$OPS_PASS"),
        login("x", MASK),
        login("y", MASK),
        login("z", MASK),
    ])
    .unwrap_err();
    assert!(
        error.invalid && error.location == "server.logins",
        "{error}"
    );
    assert_eq!(read(&path)["settings"]["server.logins"][1]["user"], "admin");
    // Two logins of one user differ only in their passwords: each masked one keeps the password at its position.
    replace(vec![login("root", "one"), login("root", "two")]).unwrap();
    replace(vec![login("root", MASK), login("root", MASK)]).unwrap();
    assert_eq!(
        read(&path)["settings"]["server.logins"],
        json!([login("root", "one"), login("root", "two")])
    );
    replace(vec![login("root", MASK), login("root", "three")]).unwrap();
    assert_eq!(
        read(&path)["settings"]["server.logins"],
        json!([login("root", "one"), login("root", "three")])
    );
    // Deleting the first of them leaves one masked row that could stand for either: refused, nothing saved.
    let error = replace(vec![login("root", MASK)]).unwrap_err();
    assert!(
        error.invalid && error.to_string().contains("root"),
        "{error}"
    );
    assert_eq!(
        read(&path)["settings"]["server.logins"],
        json!([login("root", "one"), login("root", "three")])
    );
    assert!(
        replace(vec![json!({"user": "x"})]).is_err(),
        "a record has all its fields"
    );
}
