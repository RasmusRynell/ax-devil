use super::*;

fn map(entries: &[(&str, Option<&str>)]) -> BTreeMap<String, Option<String>> {
    entries
        .iter()
        .map(|(id, key)| ((*id).to_owned(), key.map(str::to_owned)))
        .collect()
}

#[test]
fn overrides_win_and_round_trip() {
    let none = effective(COMMANDS, &BTreeMap::new());
    assert_eq!(none["save-workspace"].as_deref(), Some("Ctrl+S"));
    assert_eq!(none["new-workspace"], None);
    let all: BTreeMap<String, Option<String>> = none
        .iter()
        .map(|(id, key)| (id.clone(), key.clone()))
        .collect();
    assert!(
        overrides_from(COMMANDS, &all).unwrap().is_empty(),
        "defaults are valid, unique and unreserved"
    );

    // Exit takes Ctrl+S: Save Workspace loses its default instead of sharing it.
    let chosen = map(&[
        ("exit", Some("ctrl+s")),
        ("new-workspace", Some("Ctrl+Shift+W")),
        ("about", None),
    ]);
    let bound = effective(COMMANDS, &chosen);
    assert_eq!(bound["exit"].as_deref(), Some("ctrl+s"));
    assert_eq!(bound["save-workspace"], None);
    assert_eq!(bound["open-workspace"].as_deref(), Some("Ctrl+O"));

    let edited: BTreeMap<String, Option<String>> = bound
        .iter()
        .map(|(id, key)| (id.clone(), key.clone()))
        .collect();
    let saved = overrides_from(COMMANDS, &edited).unwrap();
    assert_eq!(
        saved["exit"].as_deref(),
        Some("Ctrl+S"),
        "stored in canonical form"
    );
    assert_eq!(
        saved["save-workspace"], None,
        "an unbound default is stored as null"
    );
    assert_eq!(saved["new-workspace"].as_deref(), Some("Ctrl+Shift+W"));
    assert!(
        !saved.contains_key("about"),
        "a value equal to the default is not stored"
    );

    for (change, location, reason) in [
        (
            map(&[("exit", Some("Ctrl+S"))]),
            "shortcuts.exit",
            "Ctrl+S is already used by Save Workspace",
        ),
        (
            map(&[("exit", Some("Ctrl+0"))]),
            "shortcuts.exit",
            "reserved",
        ),
        (map(&[("nope", None)]), "shortcuts.nope", "not a command"),
    ] {
        let mut full = all.clone();
        full.extend(change);
        let error = overrides_from(COMMANDS, &full).unwrap_err();
        assert_eq!(error.location, location);
        assert!(error.reason.contains(reason), "{}", error.reason);
    }
}

#[test]
fn a_plugin_command_is_rebindable_and_a_taken_default_ships_unbound() {
    let plugin = |id: &'static str, key: &'static str| Command {
        id: Cow::Borrowed(id),
        label: Cow::Borrowed(id),
        category: Category::Plugins,
        key: Some(Cow::Borrowed(key)),
        repeats: false,
        description: None,
    };
    let commands = with_plugins([
        plugin("p.free", "ctrl+alt+e"),
        plugin("p.taken", "Ctrl+S"),
        plugin("p.zoom", "Ctrl+0"),
        plugin("q.again", "Ctrl+Alt+E"),
    ]);
    let none = effective(&commands, &BTreeMap::new());
    assert_eq!(none["p.free"].as_deref(), Some("Ctrl+Alt+E"));
    assert_eq!(none["p.taken"], None);
    assert_eq!(none["p.zoom"], None);
    assert_eq!(none["q.again"], None);
    assert_eq!(none["save-workspace"].as_deref(), Some("Ctrl+S"));

    let saved = overrides_from(&commands, &map(&[("p.taken", Some("ctrl+j"))])).unwrap();
    assert_eq!(saved, map(&[("p.taken", Some("Ctrl+J"))]));
    let bound = effective(&commands, &saved);
    assert_eq!(bound["p.taken"].as_deref(), Some("Ctrl+J"));

    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = crate::Settings::load(&path).unwrap();
    let edit = crate::Patch {
        shortcuts_set: map(&[("p.taken", Some("ctrl+j"))]),
        ..Default::default()
    };
    settings.patch_if(&edit, &commands, |_| Ok(())).unwrap();
    let loaded = crate::Settings::load(&path).unwrap();
    let state = loaded.state();
    assert_eq!(
        state.shortcuts(&commands)["p.taken"].as_deref(),
        Some("Ctrl+J")
    );
    assert!(state.recovered_shortcuts(&commands).is_empty());
}

#[test]
fn meta_binding_survives_save_reload_and_conflicts_are_refused() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("settings.json");
    let settings = crate::Settings::load(&path).unwrap();
    let edit = crate::Patch {
        shortcuts_set: map(&[("exit", Some("ctrl+meta+alt+shift+q"))]),
        ..Default::default()
    };
    settings.patch(&edit).unwrap();
    let saved: serde_json::Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    assert_eq!(saved["shortcuts"]["exit"], "Meta+Ctrl+Alt+Shift+Q");
    let loaded = crate::Settings::load(&path).unwrap();
    assert_eq!(
        loaded.state().shortcuts(COMMANDS)["exit"].as_deref(),
        Some("Meta+Ctrl+Alt+Shift+Q")
    );
    let duplicate = crate::Patch {
        shortcuts_set: map(&[("about", Some("Meta+Ctrl+Alt+Shift+Q"))]),
        ..Default::default()
    };
    assert!(
        loaded
            .patch(&duplicate)
            .unwrap_err()
            .reason
            .contains("already used")
    );
}

#[test]
fn canonical_keys_match_the_shared_vectors() {
    let vectors: Vec<serde_json::Value> =
        serde_json::from_str(include_str!("../../../../fixtures/shortcuts/keys.json")).unwrap();
    for vector in vectors {
        let input = vector["input"].as_str().unwrap();
        match vector.get("canonical") {
            Some(expected) => assert_eq!(
                canonical(input).as_deref(),
                Ok(expected.as_str().unwrap()),
                "{input:?}"
            ),
            None => assert!(canonical(input).is_err(), "{input:?} should be refused"),
        }
    }
}
