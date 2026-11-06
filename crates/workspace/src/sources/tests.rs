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
