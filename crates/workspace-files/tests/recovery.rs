//! Recovery copies and the recent workspaces list.

use std::{collections::HashSet, fs, path::PathBuf, sync::atomic::AtomicU64, thread};
use workspace::Workspace;
use workspace_files::{Claim, Recents, RecoveryFolder, SUFFIX};

#[test]
fn a_copy_belongs_to_one_holder_at_a_time_and_names_never_collide() {
    let dir = tempfile::tempdir().unwrap();
    let folder = RecoveryFolder::new(dir.path().join("unsaved"));
    let first = folder.reserve("Untitled").unwrap();
    let second = folder.reserve("Untitled").unwrap();
    assert_ne!(first.path(), second.path());
    assert!(folder.contains(first.path()));
    assert!(first.path().ends_with(format!("Untitled{SUFFIX}")));

    let path = first.path().to_owned();
    assert!(matches!(folder.claim(&path), Err(Claim::InUse)));
    first.write(&Workspace::default(), None).unwrap();
    drop(first);
    let claimed = folder.claim(&path).unwrap();
    assert_eq!(claimed.read().unwrap(), (Workspace::default(), None));

    claimed.delete().unwrap();
    assert!(!path.exists());
    assert!(matches!(folder.claim(&path), Err(Claim::Failed(_))));
    assert_eq!(
        fs::read_dir(dir.path().join("unsaved")).unwrap().count(),
        2,
        "the other copy and its lock, and none left by the failed claim"
    );

    // A deleted copy leaves no lock behind, so its name is free for a new copy that is alone in holding it.
    let again = folder.reserve("Untitled").unwrap();
    assert_eq!(again.path(), path);
    assert!(matches!(folder.claim(&path), Err(Claim::InUse)));
}

#[test]
fn recents_keep_the_newest_eight_and_lose_no_concurrent_change() {
    let dir = tempfile::tempdir().unwrap();
    let recents = Recents::new(dir.path().join("recent.json"));
    let clock = AtomicU64::new(1);
    let path = |thread: usize, n: usize| dir.path().join(format!("w{thread}-{n}{SUFFIX}"));
    let record = |threads: usize, each: usize| {
        thread::scope(|scope| {
            for t in 0..threads {
                let (recents, clock, path) = (&recents, &clock, &path);
                scope.spawn(move || {
                    for n in 0..each {
                        let now = clock.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                        recents.record(&path(t, n), now).unwrap();
                    }
                });
            }
        });
    };

    record(4, 2);
    let mut listed: Vec<PathBuf> = recents
        .list()
        .into_iter()
        .map(|recent| recent.path)
        .collect();
    listed.sort();
    let mut expected: Vec<PathBuf> = (0..4)
        .flat_map(|t| (0..2).map(move |n| (t, n)))
        .map(|(t, n)| path(t, n))
        .collect();
    expected.sort();
    assert_eq!(listed, expected, "eight concurrent records all land");

    record(8, 3);
    let listed = recents.list();
    assert_eq!(listed.len(), 8);
    let distinct: HashSet<_> = listed.iter().map(|recent| &recent.path).collect();
    assert_eq!(distinct.len(), 8);
}

#[test]
fn recording_moves_a_path_first_and_lists_paths_that_do_not_exist() {
    let dir = tempfile::tempdir().unwrap();
    let recents = Recents::new(dir.path().join("state/recent.json"));
    let (a, b) = (dir.path().join("a"), dir.path().join("gone/../b"));
    recents.record(&a, 1).unwrap();
    recents.record(&b, 2).unwrap();
    recents.record(&a, 3).unwrap();
    let listed = recents.list();
    assert_eq!(listed[0].path, a);
    assert_eq!(listed[0].used_ms, 3);
    assert_eq!(listed[1].path, dir.path().join("b"));
    assert_eq!(listed.len(), 2);
    recents.forget(&b).unwrap();
    assert_eq!(recents.list().len(), 1);
}

#[cfg(unix)]
#[test]
fn a_symlink_stays_distinct_from_its_target() {
    let dir = tempfile::tempdir().unwrap();
    let (target, link) = (dir.path().join("target"), dir.path().join("link"));
    fs::write(&target, "").unwrap();
    std::os::unix::fs::symlink(&target, &link).unwrap();
    let recents = Recents::new(dir.path().join("recent.json"));
    recents.record(&target, 1).unwrap();
    recents.record(&link, 2).unwrap();
    assert_eq!(recents.list().len(), 2);
}

#[test]
fn a_saved_file_with_a_recovery_copy_is_listed_once_as_the_copy() {
    let dir = tempfile::tempdir().unwrap();
    let recents = Recents::new(dir.path().join("recent.json"));
    let file = |n: usize| dir.path().join(format!("w{n}{SUFFIX}"));
    let copy = dir.path().join(format!("unsaved/w0{SUFFIX}"));
    for n in 0..8 {
        recents.record(&file(n), n as u64).unwrap();
    }
    recents.record_copy(&copy, Some(&file(0)), 10).unwrap();

    // One row for the workspace, and the eight rows are eight workspaces: the file is hidden, not counted.
    let listed = recents.list();
    assert_eq!(listed.len(), 8);
    assert_eq!(listed[0].path, copy);
    assert_eq!(listed[0].recovers, Some(file(0)));
    assert!(listed.iter().all(|recent| recent.path != file(0)));

    // Without the copy the file is back.
    recents.forget(&copy).unwrap();
    assert!(recents.list().iter().any(|recent| recent.path == file(0)));

    // A file used after its copy was written is what the user did last: both are listed, the file first.
    recents.record_copy(&copy, Some(&file(0)), 11).unwrap();
    recents.record(&file(0), 12).unwrap();
    let listed = recents.list();
    assert_eq!((&listed[0].path, &listed[1].path), (&file(0), &copy));
}
