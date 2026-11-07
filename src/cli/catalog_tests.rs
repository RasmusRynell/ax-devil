use super::{tests::parse, *};
use catalog::{Report, Target, through_file};
use serde_json::Value;
use std::fs;

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
    for (line, (file, note)) in lines[6..].iter().zip([
        ("night-shift-2.json", "copy of Tracking"),
        ("night-shift.json", "copy of Standard"),
    ]) {
        assert!(
            line.contains("Night Shift") && line.ends_with(&format!("{file}  ({note})")),
            "{listing}"
        );
    }
    assert_eq!(lines.len(), 8);
    let edited = fs::read_to_string(file("night-shift.json"))
        .unwrap()
        .replacen("{\n", "{\n  \"extra\": 1,\n", 1);
    fs::write(file("night-shift.json"), edited).unwrap();
    let listing = catalog(dir.path(), &["list"]).text;
    assert!(
        listing
            .lines()
            .any(|line| line.ends_with("(copy of Standard, modified)"))
    );
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

#[test]
fn catalog_reference_prints_the_language_without_the_artifact_header() {
    let dir = tempfile::tempdir().unwrap();
    let reference = catalog(dir.path(), &["reference"]);
    assert!(reference.ok);
    assert!(
        reference
            .text
            .starts_with("Render catalog language, schema 4\n")
    );
    for word in [
        "\nMARKS",
        "\n  polygon\n",
        "  ramp(",
        "\nFIELDS",
        "\nLIMITS",
    ] {
        assert!(reference.text.contains(word), "{word}");
    }
    assert!(!reference.text.contains("Generated by"));
}

/// Fails when the compiler's tables changed and `src/cli/catalog_reference.txt` was not regenerated.
#[test]
#[allow(clippy::print_stderr)] // The skip notice: a test cannot be skipped any other way.
fn the_reference_artifact_is_what_the_compiler_tables_generate() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    if !root.join("node_modules/vite-plus").is_dir()
        && std::env::var_os("AX_DEVIL_REQUIRE_ELECTRON").is_none()
    {
        eprintln!("SKIPPED: the frontend's node_modules are not installed");
        return;
    }
    let generated = std::process::Command::new("node")
        .args([
            "--disable-warning=MODULE_TYPELESS_PACKAGE_JSON",
            "scripts/catalog-reference.ts",
        ])
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        generated.status.success(),
        "{}",
        String::from_utf8_lossy(&generated.stderr)
    );
    assert!(
        generated.stdout == include_bytes!("catalog_reference.txt"),
        "stale: regenerate with node --disable-warning=MODULE_TYPELESS_PACKAGE_JSON scripts/catalog-reference.ts --write"
    );
}

#[test]
fn a_built_in_is_checked_from_a_scratch_file_and_a_catalog_the_folder_lacks_is_refused() {
    let checked = through_file(&Target::BuiltIn("standard"), |path| {
        let text = fs::read_to_string(path).unwrap();
        assert!(text.contains("\"schema\": 4"));
        Ok(Report {
            text: format!("{} is bad\n", path.display()),
            ok: false,
        })
    })
    .unwrap();
    assert_eq!(checked.text, "built-in:standard is bad\n");

    let dir = tempfile::tempdir().unwrap();
    let missing = catalog(dir.path(), &["check", "user:gone.json"]);
    assert!(
        !missing.ok && missing.text.contains("gone.json"),
        "{}",
        missing.text
    );
}
