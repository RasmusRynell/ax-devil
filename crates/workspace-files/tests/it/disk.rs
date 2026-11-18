//! Saving: atomic replace, permissions, and no leftovers.

#![cfg(unix)]

use std::{fs, os::unix::fs::PermissionsExt};
use workspace::Workspace;
use workspace_files::{SUFFIX, load, save};

fn mode(path: &std::path::Path) -> u32 {
    fs::metadata(path).unwrap().permissions().mode() & 0o777
}

#[test]
fn a_new_file_is_private_and_an_existing_file_keeps_its_permissions() {
    let dir = tempfile::tempdir().unwrap();
    let new = dir.path().join(format!("new{SUFFIX}"));
    save(&Workspace::default(), &new).unwrap();
    assert_eq!(mode(&new), 0o600);

    let old = dir.path().join(format!("old{SUFFIX}"));
    fs::write(&old, "").unwrap();
    fs::set_permissions(&old, fs::Permissions::from_mode(0o644)).unwrap();
    save(&Workspace::default(), &old).unwrap();
    assert_eq!(mode(&old), 0o644);
    load(&old).unwrap();
}

#[test]
fn a_failed_save_keeps_the_old_bytes_and_leaves_no_temporary_file() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join(format!("ws{SUFFIX}"));
    fs::write(&file, "old").unwrap();
    fs::set_permissions(dir.path(), fs::Permissions::from_mode(0o500)).unwrap();
    let probe = fs::File::create(dir.path().join("probe"));
    let result = save(&Workspace::default(), &file);
    fs::set_permissions(dir.path(), fs::Permissions::from_mode(0o700)).unwrap();
    if probe.is_ok() {
        return; // running as a user the folder mode does not stop (root)
    }

    let error = result.unwrap_err();
    assert!(
        error.reason.starts_with("Could not save"),
        "{}",
        error.reason
    );
    assert_eq!(fs::read(&file).unwrap(), b"old");
    assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 1);
}
