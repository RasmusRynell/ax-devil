use super::*;
use handler_api::Settings;
use handlers::Handlers;
use std::{
    fs,
    time::{Duration, SystemTime},
};
use workspace::intake::{NewOverlay, video};

/// An item with one overlay file, recorded as read from it now.
fn recorded(sources: &Sources, folder: &std::path::Path) -> (ItemId, PathBuf) {
    let overlay = folder.join("gt.txt");
    fs::write(&overlay, "one").unwrap();
    let new = NewOverlay {
        path: overlay.clone(),
        handler: None,
        label: None,
        options: Settings::from_iter([("width".into(), 64.into()), ("height".into(), 48.into())]),
    };
    let item = video(&folder.join("a.mp4"), &[new], &Handlers::built_in()).unwrap();
    let paths = SourcePaths::of_recipe(&item.recipe, &Handlers::built_in());
    let baseline = paths.snapshot(&CancelToken::new()).unwrap();
    sources.record(item.id, paths, baseline);
    (item.id, overlay)
}

fn rewrite(file: &std::path::Path, text: &str, seconds: u64) {
    fs::write(file, text).unwrap();
    let handle = fs::File::options().write(true).open(file).unwrap();
    handle
        .set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(seconds))
        .unwrap();
}

#[test]
fn a_change_is_reported_once_it_looked_the_same_on_two_looks() {
    let folder = tempfile::tempdir().unwrap();
    let sources = Sources::default();
    let (item, overlay) = recorded(&sources, folder.path());
    let live = HashSet::from([item]);
    assert!(sources.look(&live).is_empty());

    rewrite(&overlay, "second!", 5_000);
    assert!(sources.look(&live).is_empty(), "still being written");
    rewrite(&overlay, "third!!!", 6_000);
    assert!(sources.look(&live).is_empty(), "written again since");
    assert_eq!(sources.look(&live), [item]);
    assert!(sources.look(&live).is_empty(), "reported once");

    rewrite(&overlay, "fourth!!!!", 7_000);
    sources.look(&live);
    assert_eq!(
        sources.look(&live),
        [item],
        "a later change is a new report"
    );
}

#[test]
fn a_change_made_while_the_item_was_read_is_found_and_a_removed_item_is_forgotten() {
    let folder = tempfile::tempdir().unwrap();
    let sources = Sources::default();
    let (item, overlay) = recorded(&sources, folder.path());
    // The recorded baseline is from before the read; the file changed after it.
    rewrite(&overlay, "changed while reading", 9_000);
    let live = HashSet::from([item]);
    sources.look(&live);
    assert_eq!(sources.look(&live), [item]);

    rewrite(&overlay, "again", 10_000);
    let none = HashSet::new();
    sources.look(&none);
    sources.look(&none);
    assert!(
        sources.look(&live).is_empty(),
        "nothing is watched for it any more"
    );
}
