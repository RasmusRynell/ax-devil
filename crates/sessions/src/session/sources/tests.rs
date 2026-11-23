use super::*;
use handler_api::Settings;
use handlers::Handlers;
use std::{
    fs,
    path::Path,
    time::{Duration, SystemTime},
};
use workspace::intake::{NewOverlay, video};

/// What a video with one overlay file in `folder` is read from, as it is now, and that file.
fn read_from(folder: &Path) -> (Watch, PathBuf) {
    let overlay = folder.join("gt.txt");
    fs::write(&overlay, "one").unwrap();
    let new = NewOverlay {
        path: overlay.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    let handlers = Handlers::built_in();
    let item = video(&folder.join("a.mp4"), &[new], &handlers).unwrap();
    let (_, watch) = Watch::read(&item.recipe, &handlers, &CancelToken::new(), || {
        Err::<workspace::Content, ()>(())
    });
    (watch.unwrap(), overlay)
}

fn rewrite(file: &Path, text: &str, seconds: u64) {
    fs::write(file, text).unwrap();
    let handle = fs::File::options().write(true).open(file).unwrap();
    handle
        .set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(seconds))
        .unwrap();
}

#[test]
fn a_change_is_reported_once_it_looked_the_same_on_two_looks() {
    let folder = tempfile::tempdir().unwrap();
    let (sources, key) = (
        Sources::default(),
        (SessionId::generate(), ItemId::generate()),
    );
    let (watch, overlay) = read_from(folder.path());
    sources.record(key.0, key.1, watch);
    let live = HashSet::from([key]);
    let look = || {
        sources
            .look(&live, sources.version())
            .into_iter()
            .map(|(key, _)| key)
            .collect::<Vec<_>>()
    };
    assert!(look().is_empty());

    rewrite(&overlay, "second!", 5_000);
    assert!(look().is_empty(), "still being written");
    rewrite(&overlay, "third!!!", 6_000);
    assert!(look().is_empty(), "written again since");
    assert_eq!(look(), [key]);
    assert!(look().is_empty(), "reported once");

    rewrite(&overlay, "fourth!!!!", 7_000);
    look();
    assert_eq!(look(), [key], "a later change is a new report");
}

#[test]
fn one_item_in_two_sessions_is_watched_in_each_and_a_record_made_after_the_items_were_listed_is_kept()
 {
    let folder = tempfile::tempdir().unwrap();
    let (sources, item) = (Sources::default(), ItemId::generate());
    let (first, second) = (SessionId::generate(), SessionId::generate());
    // The same saved workspace open in two windows has the same item ids in both.
    let (watch, overlay) = read_from(folder.path());
    sources.record(first, item, watch.clone());
    sources.record(second, item, watch);
    let known = sources.version();
    let live = HashSet::from([(first, item), (second, item)]);

    rewrite(&overlay, "changed!", 9_000);
    sources.look(&live, known);
    let changed: HashSet<_> = sources
        .look(&live, known)
        .into_iter()
        .map(|(key, _)| key)
        .collect();
    assert_eq!(changed, live);

    // An item added and resolved while the looks run is not in the list they took, and is not forgotten for it.
    let added = (first, ItemId::generate());
    let other = tempfile::tempdir().unwrap();
    let (watch, overlay) = read_from(other.path());
    sources.record(added.0, added.1, watch);
    let nobody = HashSet::new();
    sources.look(&nobody, known);
    rewrite(&overlay, "again", 10_000);
    sources.look(&nobody, known);
    assert_eq!(
        sources
            .look(&nobody, known)
            .into_iter()
            .map(|(key, _)| key)
            .collect::<Vec<_>>(),
        [added]
    );
    // Once it was known and its item is gone, it is forgotten.
    rewrite(&overlay, "and again", 11_000);
    sources.look(&nobody, sources.version());
    assert!(sources.look(&nobody, sources.version()).is_empty());
}
