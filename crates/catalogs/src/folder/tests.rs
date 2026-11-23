use super::*;
use crate::{CatalogId, Library, Served};
use std::{
    ffi::OsStr,
    os::unix::{ffi::OsStrExt, fs::symlink},
    process::Command,
    sync::mpsc,
    thread,
    time::Duration,
};

fn served(library: &Library, file: &str) -> Served {
    library.document(&CatalogId::User(file.into()))
}

fn problem(library: &Library, file: &str) -> String {
    match served(library, file) {
        Served::Unreadable { problem, .. } => problem,
        other => panic!("{file} is served as {other:?}"),
    }
}

fn mkfifo(path: &Path) {
    assert!(Command::new("mkfifo").arg(path).status().unwrap().success());
}

/// Runs `work` and fails, rather than hangs, when it does not finish.
fn quickly<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> T {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || sender.send(work()));
    receiver
        .recv_timeout(Duration::from_secs(10))
        .expect("the poll blocked")
}

fn fingerprint(path: &Path) -> Fingerprint {
    Fingerprint::of(&fs::symlink_metadata(path).unwrap())
}

#[test]
fn links_are_reported_and_never_followed_so_nothing_outside_the_folder_is_served() {
    let outside = tempfile::tempdir().unwrap();
    let secret = outside.path().join("secret.json");
    fs::write(&secret, "TOP SECRET").unwrap();
    let folder = tempfile::tempdir().unwrap();
    let root = folder.path();
    symlink(&secret, root.join("private.json")).unwrap();
    symlink(root.join("b.json"), root.join("a.json")).unwrap();
    symlink(root.join("a.json"), root.join("b.json")).unwrap();
    symlink(
        outside.path().join("missing.json"),
        root.join("dangling.json"),
    )
    .unwrap();
    symlink(outside.path(), root.join("dir.json")).unwrap();

    let library = Library::with_folder(root.to_path_buf());

    for link in [
        "private.json",
        "a.json",
        "b.json",
        "dangling.json",
        "dir.json",
    ] {
        assert_eq!(problem(&library, link), NOT_REGULAR, "{link}");
    }
    assert!(!format!("{:?}", library.documents()).contains("SECRET"));
}

#[test]
fn a_file_replaced_by_a_link_or_a_pipe_between_polls_stops_being_served() {
    let outside = tempfile::tempdir().unwrap();
    let secret = outside.path().join("secret.json");
    fs::write(&secret, "TOP SECRET").unwrap();
    let folder = tempfile::tempdir().unwrap();
    let root = folder.path();
    fs::write(root.join("link.json"), "{}").unwrap();
    fs::write(root.join("pipe.json"), "{}").unwrap();
    let library = Library::with_folder(root.to_path_buf());
    assert!(matches!(
        served(&library, "link.json"),
        Served::Document { .. }
    ));

    fs::remove_file(root.join("link.json")).unwrap();
    symlink(&secret, root.join("link.json")).unwrap();
    fs::remove_file(root.join("pipe.json")).unwrap();
    mkfifo(&root.join("pipe.json"));
    let library = quickly(move || {
        assert!(!library.rescan(), "a changed file waits a poll");
        assert!(library.rescan());
        library
    });

    assert_eq!(problem(&library, "link.json"), NOT_REGULAR);
    assert_eq!(problem(&library, "pipe.json"), NOT_REGULAR);
}

#[test]
fn a_pipe_in_the_folder_does_not_block_the_poll() {
    let folder = tempfile::tempdir().unwrap();
    mkfifo(&folder.path().join("pipe.json"));
    let root = folder.path().to_path_buf();

    let library = quickly(move || {
        let library = Library::with_folder(root);
        library.rescan();
        library.rescan();
        library
    });

    assert_eq!(problem(&library, "pipe.json"), NOT_REGULAR);
}

#[test]
fn a_file_swapped_for_something_else_after_the_listing_is_judged_on_the_opened_handle() {
    let folder = tempfile::tempdir().unwrap();
    let root = folder.path();
    let file = root.join("night.json");
    fs::write(&file, "{}").unwrap();
    let listed = fingerprint(&file);

    // The listing saw a regular file; the open finds a pipe, a link or other bytes.
    let pipe = root.join("pipe.json");
    mkfifo(&pipe);
    let swapped = quickly(move || read(&pipe, &listed));
    assert!(matches!(swapped, Read::Done(Content::Problem(p)) if p == NOT_REGULAR));

    let link = root.join("link.json");
    symlink(&file, &link).unwrap();
    assert!(matches!(read(&link, &listed), Read::Failed(_)));

    fs::write(&file, "{ \"grown\": true }").unwrap();
    assert!(matches!(read(&file, &listed), Read::Changed));
    assert!(matches!(
        read(&file, &fingerprint(&file)),
        Read::Done(Content::Text(_))
    ));
}

#[test]
fn a_file_over_the_limit_is_a_problem_however_it_grew() {
    let folder = tempfile::tempdir().unwrap();
    let file = folder.path().join("big.json");
    fs::write(&file, vec![b' '; MAX_CATALOG_BYTES as usize + 1]).unwrap();

    let read = read(&file, &fingerprint(&file));

    assert!(matches!(read, Read::Done(Content::Problem(p)) if p.contains("at most")));
}

#[test]
fn names_that_are_no_catalog_id_are_listed_by_a_safe_name_with_a_problem() {
    let folder = tempfile::tempdir().unwrap();
    let root = folder.path();
    fs::write(root.join("esc\u{1b}ape.json"), "{}").unwrap();
    fs::write(root.join("back\\slash.json"), "{}").unwrap();
    // macOS file systems refuse a name that is not UTF-8, so there the file cannot exist to be listed.
    let latin = fs::write(root.join(OsStr::from_bytes(b"latin\xe9.json")), "{}").is_ok();
    fs::write(root.join("fine.json"), "{}").unwrap();

    let library = Library::with_folder(root.to_path_buf());

    let ids: Vec<String> = library
        .documents()
        .into_iter()
        .skip(6)
        .map(|info| info.id.to_string())
        .collect();
    let mut expected = vec![
        "user:back\u{fffd}slash.json",
        "user:esc\u{fffd}ape.json",
        "user:fine.json",
    ];
    if latin {
        expected.push("user:latin\u{fffd}.json");
    }
    assert_eq!(ids, expected);
    for id in &ids {
        let CatalogId::User(file) = CatalogId::parse(id).unwrap() else {
            panic!("a user id");
        };
        if file != "fine.json" {
            assert_eq!(problem(&library, &file), ODD_NAME);
        }
    }
}
