//! One containment check and explicit regeneration path for backend-derived frontend fixtures.

use std::{collections::BTreeMap, fs, io, path::Path};

const REGENERATE: &str = "UPDATE_GOLDENS=1 make test-backend TEST=frontend_fixture";

/// Compare backend-derived files without writing; use the repository's golden-update switch to regenerate.
pub fn check(fixtures: &Path, folder: &str, files: &BTreeMap<String, String>) {
    compare(
        fixtures,
        folder,
        files,
        std::env::var_os("UPDATE_GOLDENS").is_some(),
    )
    .unwrap_or_else(|reason| panic!("{reason}"));
}

fn compare(
    fixtures: &Path,
    folder: &str,
    files: &BTreeMap<String, String>,
    update: bool,
) -> Result<(), String> {
    // These are single-file writes, never relative paths or recursive copies.
    for name in std::iter::once(folder).chain(files.keys().map(String::as_str)) {
        if name.is_empty()
            || !name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.'))
            || matches!(name, "." | "..")
        {
            return Err(format!("{name:?} is not a file name"));
        }
    }
    check_folder(fixtures, folder)?;
    let folder = fixtures.join(folder);
    if update {
        fs::create_dir_all(&folder).map_err(|error| error.to_string())?;
    }
    for (name, wanted) in files {
        let path = folder.join(name);
        if fs::read_to_string(&path).ok().as_ref() != Some(wanted) {
            if !update {
                return Err(format!(
                    "{} is missing or stale; regenerate with {REGENERATE}",
                    path.display()
                ));
            }
            fs::write(path, wanted).map_err(|error| error.to_string())?;
        }
    }
    if folder.exists() {
        for entry in fs::read_dir(&folder).map_err(|error| error.to_string())? {
            let path = entry.map_err(|error| error.to_string())?.path();
            if path
                .extension()
                .is_some_and(|extension| extension == "json")
                && !path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| files.contains_key(name))
            {
                if !update {
                    return Err(format!(
                        "{} is stale; regenerate with {REGENERATE}",
                        path.display()
                    ));
                }
                fs::remove_file(path).map_err(|error| error.to_string())?;
            }
        }
    }
    Ok(())
}

/// Checks, before anything is written, that `fixtures` holds the named plain folder (or none yet) of regular files
/// only: a symlink or a sub-folder there would make the writes and the pruning reach outside it.
fn check_folder(fixtures: &Path, folder: &str) -> Result<(), String> {
    let plain = |path: &Path| match fs::symlink_metadata(path) {
        Ok(found) if found.is_dir() => Ok(true),
        Ok(_) => Err(format!("{} is not a plain folder", path.display())),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!("{} cannot be read: {error}", path.display())),
    };
    if !plain(fixtures)? {
        return Err(format!("{} does not exist", fixtures.display()));
    }
    let directory = fixtures.join(folder);
    if !plain(&directory)? {
        return Ok(());
    }
    let entries = fs::read_dir(&directory).map_err(|error| format!("{directory:?}: {error}"))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("{directory:?}: {error}"))?;
        let kind = entry
            .file_type()
            .map_err(|error| format!("{:?}: {error}", entry.path()))?;
        if !kind.is_file() {
            let path = entry.path();
            return Err(format!("{} is not a regular file", path.display()));
        }
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn a_fixtures_folder_that_leaves_itself_is_refused_untouched() {
    use std::os::unix::fs::symlink;
    let listing = |folder: &Path| {
        let mut found: Vec<String> = fs::read_dir(folder)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        found.sort();
        found
    };
    let temp = tempfile::tempdir().unwrap();
    let outside = temp.path().join("outside");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("kept.json"), "kept").unwrap();
    let files = BTreeMap::from([("a.json".into(), "new".into())]);
    let refused = |name: &str, setup: &dyn Fn(&Path)| {
        let fixtures = temp.path().join(name);
        fs::create_dir_all(fixtures.join("session")).unwrap();
        setup(&fixtures);
        let before = listing(&fixtures.join("session"));
        for update in [false, true] {
            assert!(
                compare(&fixtures, "session", &files, update).is_err(),
                "{name} is accepted"
            );
        }
        assert_eq!(listing(&fixtures.join("session")), before);
        assert_eq!(listing(&outside), ["kept.json"]);
        assert_eq!(
            fs::read_to_string(outside.join("kept.json")).unwrap(),
            "kept"
        );
    };
    refused("file", &|fixtures| {
        symlink(outside.join("kept.json"), fixtures.join("session/a.json")).unwrap();
    });
    refused("folder", &|fixtures| {
        fs::remove_dir(fixtures.join("session")).unwrap();
        symlink(&outside, fixtures.join("session")).unwrap();
    });
    refused("parent", &|fixtures| {
        let real = fixtures.with_extension("real");
        fs::rename(fixtures, &real).unwrap();
        symlink(&real, fixtures).unwrap();
    });
    refused("sub", &|fixtures| {
        fs::create_dir(fixtures.join("session/sub")).unwrap();
    });
    let fresh = temp.path().join("fresh");
    fs::create_dir(&fresh).unwrap();
    assert!(check_folder(&fresh, "session").is_ok());
}

#[test]
fn an_ordinary_fixture_check_never_rewrites_stale_files() {
    let temp = tempfile::tempdir().unwrap();
    fs::create_dir(temp.path().join("session")).unwrap();
    let path = temp.path().join("session/a.json");
    fs::write(&path, "old").unwrap();
    let files = BTreeMap::from([("a.json".into(), "new".into())]);
    let reason = compare(temp.path(), "session", &files, false).unwrap_err();
    assert!(reason.contains(REGENERATE));
    assert_eq!(fs::read_to_string(&path).unwrap(), "old");
    compare(temp.path(), "session", &files, true).unwrap();
    assert_eq!(fs::read_to_string(&path).unwrap(), "new");
    assert!(compare(temp.path(), "session", &files, false).is_ok());
}
