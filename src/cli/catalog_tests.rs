use super::{tests::parse, *};
use serde_json::Value;

/// The catalog commands on a fresh settings file and catalog folder in `dir`, as a new launch reads them.
fn catalog(dir: &std::path::Path, args: &[&str]) -> catalog::Report {
    let Some(Command::Catalog(command)) = parse(&[&["catalog"], args].concat()).unwrap().command
    else {
        panic!("a catalog command is a catalog command");
    };
    let home = catalog::Home {
        folder: dir.join("catalogs"),
        settings: ::settings::Settings::load(&dir.join("settings.json")).unwrap(),
    };
    catalog::run(&command, Ok(home))
}

fn default_catalog(dir: &std::path::Path) -> Value {
    ::settings::Settings::load(&dir.join("settings.json"))
        .unwrap()
        .state()
        .resolved("overlays.default_catalog")
        .unwrap()
}

#[test]
fn catalog_new_copies_without_overwriting_and_leaves_the_default_alone() {
    assert!(parse(&["catalog"]).is_err());
    assert!(parse(&["catalog", "render", "night.json"]).is_err());
    let dir = tempfile::tempdir().unwrap();
    let file = |name: &str| dir.path().join("catalogs").join(name);

    let first = catalog(dir.path(), &["new", "Night Shift"]);
    assert_eq!(
        first.text,
        format!("{}\n", file("night-shift.json").display())
    );
    let second = catalog(
        dir.path(),
        &["new", "Night Shift", "--from", "built-in:tracking"],
    );
    assert_eq!(
        second.text,
        format!("{}\n", file("night-shift-2.json").display())
    );
    assert!(first.ok && second.ok && file("night-shift.json").is_file());
    assert_eq!(default_catalog(dir.path()), "");

    let listing = catalog(dir.path(), &["list"]).text;
    let lines: Vec<&str> = listing.lines().collect();
    assert!(lines[0].starts_with("* Standard") && lines[0].ends_with("(built-in, read-only)"));
    for (line, file) in lines[6..]
        .iter()
        .zip(["night-shift-2.json", "night-shift.json"])
    {
        assert!(
            line.contains("Night Shift") && line.ends_with(file),
            "{listing}"
        );
    }
    assert_eq!(lines.len(), 8);
    assert!(!catalog(dir.path(), &["new", "Blank", "--from", "built-in:nope"]).ok);
}

#[test]
fn catalog_use_stores_one_setting_and_standard_clears_it() {
    let dir = tempfile::tempdir().unwrap();
    let personal = || {
        let state = ::settings::Settings::load(&dir.path().join("settings.json"))
            .unwrap()
            .state();
        state.values(&[::settings::Scope::Personal])
    };
    let before = personal();

    let used = catalog(dir.path(), &["use", "built-in:tracking"]);
    assert!(used.ok, "{}", used.text);
    assert_eq!(used.text, "Default catalog: built-in:tracking\n");
    assert_eq!(default_catalog(dir.path()), "built-in:tracking");
    assert!(catalog(dir.path(), &["list"]).text.contains("* Tracking"));
    let mut changed = personal();
    changed.insert("overlays.default_catalog".into(), "".into());
    assert_eq!(changed, before, "no other setting changed");

    assert!(catalog(dir.path(), &["use", "built-in:standard"]).ok);
    assert_eq!(default_catalog(dir.path()), "");
}

#[test]
fn catalog_use_refuses_a_file_outside_the_catalog_folder() {
    let dir = tempfile::tempdir().unwrap();
    let outside = dir.path().join("outside.json");
    std::fs::write(&outside, r#"{"name":"Outside","description":"Elsewhere"}"#).unwrap();
    let refused = catalog(dir.path(), &["use", outside.to_str().unwrap()]);
    assert!(!refused.ok);
    assert!(refused.text.contains("catalog folder"), "{}", refused.text);
    assert_eq!(default_catalog(dir.path()), "");
    assert!(!dir.path().join("catalogs").exists());
}

#[cfg(unix)]
#[test]
fn catalog_use_refuses_a_linked_catalog_the_app_would_not_serve() {
    let dir = tempfile::tempdir().unwrap();
    let folder = dir.path().join("catalogs");
    std::fs::create_dir(&folder).unwrap();
    let target = dir.path().join("elsewhere.json");
    std::fs::write(&target, r#"{"name":"Linked","description":"A link"}"#).unwrap();
    std::os::unix::fs::symlink(&target, folder.join("linked.json")).unwrap();
    let before = default_catalog(dir.path());

    let refused = catalog(dir.path(), &["use", "user:linked.json"]);
    assert!(!refused.ok, "{}", refused.text);
    assert!(refused.text.contains("linked.json"), "{}", refused.text);
    assert_eq!(default_catalog(dir.path()), before);
}
