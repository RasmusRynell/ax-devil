//! What a patch may change: only the keys it names, and without Full access nothing that reaches outside ax-devil.

use crate::{tests::change, *};
use serde_json::json;

#[test]
fn without_full_access_a_save_cannot_change_a_full_access_row_or_add_a_reference_but_can_resend_the_stored()
 {
    let folder = tempfile::tempdir().unwrap();
    let settings = Settings::load(&folder.path().join("settings.json")).unwrap();
    let mut edit = change("rtsp");
    let login = |user: &str, password: &str| json!({"user": user, "password": password});
    edit.set.insert(
        "scan.logins".into(),
        json!([login("$U", "secret"), login("a", "$P")]),
    );
    settings.patch(&edit).unwrap();
    let refuse = |edit: &Patch| settings.state().refuse_without_full_access(edit);
    let with = |key: &str, value: Value| {
        let mut edit = edit.clone();
        edit.set.insert(key.into(), value);
        edit
    };
    assert!(
        refuse(&edit).is_ok(),
        "the stored values, references included"
    );
    let masked = json!([login("a", "$P"), login("$U", MASK)]);
    assert!(
        refuse(&with("scan.logins", masked)).is_ok(),
        "reordered, one password masked"
    );

    for (key, value) in [
        ("storage.cache_dir", json!("/tmp/other")),
        ("scan.range", json!("$RANGE")),
        ("camera.overlay", json!("$THEME")),
        ("scan.logins", json!([login("$U", "$NEW")])),
        ("scan.logins", json!([login("$NEW", MASK)])),
        ("scan.logins", json!([login("$P", MASK)])),
    ] {
        let refused = refuse(&with(key, value)).unwrap_err();
        assert!(
            (refused.location.as_str(), refused.forbidden) == (key, true),
            "{key}"
        );
    }
}

#[test]
fn patches_from_stale_copies_keep_each_other_and_the_revision_rises_with_each_write() {
    let folder = tempfile::tempdir().unwrap();
    let settings = Settings::load(&folder.path().join("settings.json")).unwrap();
    let first = settings.state().revision();
    let only = |key: &str, value: Value| Patch {
        set: BTreeMap::from([(key.to_owned(), value)]),
        ..Patch::default()
    };

    settings
        .patch(&only("camera.overlay", json!("mqtt")))
        .unwrap();
    let state = settings
        .patch(&Patch {
            set: BTreeMap::from([("scan.range".to_owned(), json!("10.0.0.0/24"))]),
            shortcuts_set: BTreeMap::from([("exit".to_owned(), Some("Ctrl+Alt+Q".to_owned()))]),
            ..Patch::default()
        })
        .unwrap();
    assert_eq!(
        state.values()["camera.overlay"],
        "mqtt",
        "a key not named is not touched"
    );
    assert_eq!(state.values()["scan.range"], "10.0.0.0/24");
    assert_eq!(state.shortcuts()["exit"].as_deref(), Some("Ctrl+Alt+Q"));
    assert_eq!(state.revision(), first + 2);

    let reset = Patch {
        reset: BTreeSet::from(["camera.overlay".to_owned()]),
        shortcuts_reset: BTreeSet::from(["exit".to_owned()]),
        ..Patch::default()
    };
    let state = settings.patch(&reset).unwrap();
    assert_eq!(state.values()["camera.overlay"], "datahub");
    assert_eq!(
        state.shortcuts()["exit"],
        shortcuts::effective(&BTreeMap::new())["exit"]
    );
    assert_eq!(state.values()["scan.range"], "10.0.0.0/24");
}
