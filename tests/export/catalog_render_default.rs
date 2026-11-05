//! `catalog render` without a catalog file, as a user runs it: the default for new lanes, and a built-in by name. Skipped
//! without Electron (see `common::export_kit::can_export`).

use crate::common;

use common::export_kit::{can_export, read_png};
use std::{fs, process::Command};

#[test]
fn catalog_render_draws_the_default_catalog_and_a_built_in_by_name() {
    if !can_export() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let settings = home.path().join("settings.json");
    fs::write(&settings, r#"{"version": 1}"#).unwrap();
    let render = |args: &[&str], out: &str| {
        let out = home.path().join(out);
        let run = Command::new(env!("CARGO_BIN_EXE_ax-devil"))
            .arg("--config")
            .arg(&settings)
            .args(["catalog", "render"])
            .args(args)
            .arg("--out")
            .arg(&out)
            .env("XDG_DATA_HOME", home.path())
            .env("TMPDIR", home.path())
            .output()
            .unwrap();
        let (stdout, stderr) = (
            String::from_utf8_lossy(&run.stdout).into_owned(),
            String::from_utf8_lossy(&run.stderr).into_owned(),
        );
        assert!(run.status.success(), "{stdout}{stderr}");
        let (width, height, _) = read_png(&out.join("overview.png"));
        assert_eq!((width, height), (1280, 720));
        stdout
    };

    let default = render(&[], "default");
    assert!(default.starts_with("Wrote "), "{default}");
    let named = render(&["built-in:tracking"], "named");
    assert!(named.starts_with("Wrote "), "{named}");
}
