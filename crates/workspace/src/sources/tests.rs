use super::*;
use crate::intake::{NewOverlay, playlist, video};
use handler_api::Settings;
use std::time::{Duration, SystemTime};

fn write_at(path: &Path, text: &str, seconds: u64) {
    fs::write(path, text).unwrap();
    let file = fs::File::options().write(true).open(path).unwrap();
    file.set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(seconds))
        .unwrap();
}

#[test]
fn a_video_item_watches_its_overlay_files_and_not_the_video() {
    let folder = tempfile::tempdir().unwrap();
    let (video_file, overlay_file) = (folder.path().join("a.mp4"), folder.path().join("gt.txt"));
    let overlay = NewOverlay {
        path: overlay_file.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    let handlers = Handlers::built_in();
    let item = video(&video_file, &[overlay], &handlers).unwrap();
    let paths = SourcePaths::of_recipe(&item.recipe, &handlers);
    assert_eq!(paths.files, BTreeSet::from([overlay_file]));
    assert!(paths.folders.is_empty());
}

#[test]
fn a_playlist_item_watches_the_folders_its_resolver_declares() {
    let folder = tempfile::tempdir().unwrap();
    let settings = Settings::from_iter([("root".into(), folder.path().to_str().unwrap().into())]);
    let handlers = Handlers::built_in();
    let item = playlist("mot_challenge", settings, &handlers).unwrap();
    let paths = SourcePaths::of_recipe(&item.recipe, &handlers);
    assert_eq!(paths.folders, BTreeSet::from([folder.path().to_owned()]));
    assert!(paths.files.is_empty());
}

#[test]
fn a_snapshot_tells_which_watched_paths_were_written_added_or_removed() {
    let folder = tempfile::tempdir().unwrap();
    let (file, absent, sub) = (
        folder.path().join("gt.txt"),
        folder.path().join("absent.txt"),
        folder.path().join("sub"),
    );
    write_at(&file, "one", 1_000);
    fs::create_dir(&sub).unwrap();
    let paths = SourcePaths {
        files: BTreeSet::from([file.clone(), absent.clone()]),
        folders: BTreeSet::from([folder.path().to_owned()]),
    };
    let token = CancelToken::new();
    let before = paths.snapshot(&token).unwrap();
    assert!(before.changes(&paths.snapshot(&token).unwrap()).is_empty());

    // A file written in place changes itself; a file added in a subfolder changes that folder.
    write_at(&file, "two!", 2_000);
    fs::write(sub.join("later.txt"), "x").unwrap();
    let after = paths.snapshot(&token).unwrap();
    assert_eq!(before.changes(&after), BTreeSet::from([file, sub]));

    // A watched path that did not exist appears.
    fs::write(&absent, "x").unwrap();
    let appeared = paths.snapshot(&token).unwrap();
    assert!(after.changes(&appeared).contains(&absent));
}

#[test]
fn a_file_that_appears_while_the_item_is_read_is_a_change_and_a_recipe_with_no_paths_records_nothing()
 {
    let folder = tempfile::tempdir().unwrap();
    let (video_file, overlay_file) = (folder.path().join("a.mp4"), folder.path().join("gt.txt"));
    let overlay = NewOverlay {
        path: overlay_file.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    let handlers = Handlers::built_in();
    let item = video(&video_file, &[overlay], &handlers).unwrap();
    let token = CancelToken::new();

    // The overlay is missing when the item is first looked at and written before it finishes resolving.
    let (result, watch) = Watch::read(&item.recipe, &handlers, &token, || {
        fs::write(&overlay_file, "x").unwrap();
        Err::<Content, ()>(())
    });
    assert!(result.is_err());
    let watch = watch.unwrap();
    let now = watch.look(&token).unwrap();
    assert_eq!(watch.changes(&now), BTreeSet::from([overlay_file]));

    let bare = video(&video_file, &[], &handlers).unwrap();
    let (_, nothing) = Watch::read(&bare.recipe, &handlers, &token, || Err::<Content, ()>(()));
    assert!(nothing.is_none());
}

/// Sets the modification time of `folder` far in the past, so a later entry added to it shows as a change however
/// coarse the clock of the file system is.
fn age(folder: &Path) {
    let mut options = fs::File::options();
    // Windows opens a folder only with backup semantics, and sets its times only from a handle that can write.
    #[cfg(windows)]
    std::os::windows::fs::OpenOptionsExt::custom_flags(options.write(true), 0x0200_0000);
    #[cfg(not(windows))]
    options.read(true);
    let old = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000);
    options.open(folder).unwrap().set_modified(old).unwrap();
}

#[cfg(unix)]
#[test]
fn a_symbolic_link_to_a_folder_outside_is_not_followed_but_a_real_subfolder_is() {
    let (watched, outside) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let real = watched.path().join("real");
    fs::create_dir(&real).unwrap();
    std::os::unix::fs::symlink(outside.path(), watched.path().join("link")).unwrap();
    for folder in [watched.path(), real.as_path(), outside.path()] {
        age(folder);
    }
    let paths = SourcePaths {
        files: BTreeSet::new(),
        folders: BTreeSet::from([watched.path().to_owned()]),
    };
    let token = CancelToken::new();
    let before = paths.snapshot(&token).unwrap();

    fs::write(outside.path().join("elsewhere.txt"), "x").unwrap();
    assert!(before.changes(&paths.snapshot(&token).unwrap()).is_empty());

    fs::write(real.join("here.txt"), "x").unwrap();
    assert_eq!(
        before.changes(&paths.snapshot(&token).unwrap()),
        BTreeSet::from([real])
    );
}

#[test]
fn a_tree_past_the_folder_limit_still_gives_a_snapshot_of_the_folders_before_the_limit() {
    let root = tempfile::tempdir().unwrap();
    let [first, limit, past] = ["a", "b", "c"].map(|name| root.path().join(name));
    // Folders are walked in order: `first` and its subfolders, then `limit`, which is the last one the budget covers,
    // then `past`, which is one beyond it.
    fs::create_dir_all(&limit).unwrap();
    fs::create_dir_all(&past).unwrap();
    for number in 0..MAX_FOLDERS - 2 {
        fs::create_dir_all(first.join(format!("sub{number}"))).unwrap();
    }
    for folder in [&first, &limit, &past] {
        age(folder);
    }
    let paths = SourcePaths {
        files: BTreeSet::new(),
        folders: BTreeSet::from([first.clone(), limit.clone(), past.clone()]),
    };
    let token = CancelToken::new();
    let before = paths.snapshot(&token).unwrap();

    fs::write(past.join("late.txt"), "x").unwrap();
    assert!(before.changes(&paths.snapshot(&token).unwrap()).is_empty());

    fs::write(first.join("early.txt"), "x").unwrap();
    fs::write(limit.join("last.txt"), "x").unwrap();
    assert_eq!(
        before.changes(&paths.snapshot(&token).unwrap()),
        BTreeSet::from([first, limit])
    );
}
