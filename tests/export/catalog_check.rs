//! `catalog check`, `use` and `new` as a user runs them: the real command and the real Electron-as-Node checker.
//! Skipped without Electron (see `common::export_kit::can_export`).

use crate::common::export_kit::can_export;
use std::{fs, path::Path, process::Command};

#[test]
fn catalog_check_reports_a_catalog_or_every_problem_in_it_located() {
    if !can_export() {
        return;
    }
    let catalog = include_str!("../../assets/catalogs/minimal.v4.json");
    let folder = tempfile::tempdir().unwrap();
    let write = |name: &str, text: &str| {
        let path = folder.path().join(name);
        fs::write(&path, text).unwrap();
        path
    };
    let good = write("good.json", catalog);
    let bad = write(
        "bad.json",
        &catalog.replace("\"stroke\": \"color\"", "\"stroke\": \"colr\""),
    );
    let run = |args: &[&str], file: &Path| {
        Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .args(["catalog"])
            .args(args)
            .arg(file)
            .output()
            .unwrap()
    };
    let text = |bytes: &[u8]| String::from_utf8_lossy(bytes).into_owned();

    let checked = run(&["check"], &good);
    assert!(checked.status.success(), "{}", text(&checked.stderr));
    let report = text(&checked.stdout);
    assert!(
        report.contains("Minimal") && report.contains("overview: "),
        "the catalog and the sheet it drew: {report}"
    );

    let checked = run(&["check"], &bad);
    assert!(!checked.status.success());
    let problems = text(&checked.stderr);
    assert!(
        problems.contains("/layers/0/marks/0/stroke: unknown name 'colr'"),
        "{problems}"
    );
    assert!(problems.contains("  17:"), "line and column: {problems}");

    // A catalog that compiles but skips a mark on the example sheet fails `check`.
    let skipping = write(
        "skipping.json",
        r#"{"schema":4,"name":"T","description":"d","layers":[{"marks":[{"mark":"text","text":"id",
        "color":"top(attr.tint)","size":"10px","place":[{"at":"top-left","anchor":"bottom-left"}]}]}]}"#,
    );
    let checked = run(&["check"], &skipping);
    assert!(!checked.status.success());
    assert!(
        text(&checked.stderr).contains("skipped"),
        "{}",
        text(&checked.stderr)
    );
    let missing = run(&["check"], &folder.path().join("missing.json"));
    assert!(!missing.status.success());
    assert!(text(&missing.stderr).contains("missing.json"));

    // A catalog file named on the command line does not depend on the settings file; listing does.
    let broken = write("broken.json", "{ not json");
    let with_config = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("catalog")
            .args(args)
            .arg("--config")
            .arg(&broken)
            .output()
            .unwrap()
    };
    let checked = with_config(&["check", good.to_str().unwrap()]);
    assert!(checked.status.success(), "{}", text(&checked.stderr));
    let listed = with_config(&["list"]);
    assert!(!listed.status.success());
    assert!(
        text(&listed.stderr).contains("broken.json"),
        "{}",
        text(&listed.stderr)
    );
}

#[test]
fn catalog_use_makes_a_catalog_of_the_folder_the_default_and_new_copies_a_catalog_file_from_anywhere()
 {
    if !can_export() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let folder = home.path().join("ax-devil/catalogs");
    fs::create_dir_all(&folder).unwrap();
    let night = folder.join("Night.json");
    fs::write(
        &night,
        include_str!("../../assets/catalogs/minimal.v4.json"),
    )
    .unwrap();
    let settings = home.path().join("settings.json");
    fs::write(&settings, r#"{"version": 1}"#).unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("--config")
            .arg(&settings)
            .args(["catalog"])
            .args(args)
            .env("XDG_DATA_HOME", home.path())
            .output()
            .unwrap()
    };
    let text = |bytes: &[u8]| String::from_utf8_lossy(bytes).into_owned();

    let standard = run(&["check"]);
    assert!(standard.status.success(), "{}", text(&standard.stderr));
    assert!(text(&standard.stdout).contains("built-in:standard: Standard"));

    let used = run(&["use", night.to_str().unwrap()]);
    assert!(used.status.success(), "{}", text(&used.stderr));
    assert_eq!(
        text(&used.stdout),
        format!("Default catalog: {}\n", night.display())
    );
    assert!(
        fs::read_to_string(&settings)
            .unwrap()
            .contains("user:Night.json")
    );
    let checked = run(&["check"]);
    assert!(
        text(&checked.stdout).contains("Minimal"),
        "{}",
        text(&checked.stderr)
    );

    // `new` takes any catalog file, not only the folder's: here the one beside the settings.
    let elsewhere = home.path().join("elsewhere.json");
    fs::write(
        &elsewhere,
        include_str!("../../assets/catalogs/minimal.v4.json"),
    )
    .unwrap();
    let copied = run(&["new", "Copy", "--from", elsewhere.to_str().unwrap()]);
    assert!(copied.status.success(), "{}", text(&copied.stderr));
    let copy = folder.join("copy.json");
    assert_eq!(text(&copied.stdout), format!("{}\n", copy.display()));
    assert!(
        fs::read_to_string(&copy)
            .unwrap()
            .contains("\"description\": \"Based on Minimal.\"")
    );
}
