//! Folder Pair resolver behavior through the public registry.

use std::{fs, path::Path};

use foundation::{CancelToken, Interrupt};
use playlist_resolvers::{ResolvedPlaylist, Settings, VideoRef, WatchPaths, resolver};
use serde_json::json;

fn touch(path: impl AsRef<Path>) {
    let path = path.as_ref();
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, "data").unwrap();
}

fn settings(videos: &Path, overlays: &Path) -> Settings {
    let value = json!({
        "videos_dir": videos,
        "overlays_dir": overlays,
        "handler_type": "ADF_BETA_FRAME",
    });
    value.as_object().unwrap().clone()
}

fn resolve(
    settings: &Settings,
    cancel: &CancelToken,
) -> Result<ResolvedPlaylist, Interrupt<foundation::UserError>> {
    resolver("folder_pair").unwrap().resolve(settings, cancel)
}

fn failure(settings: &Settings) -> foundation::UserError {
    match resolve(settings, &CancelToken::new()) {
        Err(Interrupt::Failed(error)) => error,
        other => panic!("expected a failure, got {other:?}"),
    }
}

#[test]
fn pairs_files_by_stripped_name_in_name_order() {
    let dir = tempfile::tempdir().unwrap();
    let (videos, overlays) = (dir.path().join("videos"), dir.path().join("annotations"));
    for file in [
        "beta.mov",
        "alpha.mp4",
        "camera.1.mp4",
        "unmatched.mp4",
        ".hidden.mp4",
        "dir.mp4/inner.mp4",
    ] {
        touch(videos.join(file));
    }
    for file in [
        "alpha.json",
        "beta.jsonl",
        "camera.1.json",
        "orphan.json",
        ".hidden.json",
    ] {
        touch(overlays.join(file));
    }
    fs::create_dir(overlays.join("folder.json")).unwrap();

    let playlist = resolve(&settings(&videos, &overlays), &CancelToken::new()).unwrap();

    assert_eq!(playlist.display_name, "Folder Pair");
    let names: Vec<_> = playlist
        .entries
        .iter()
        .map(|entry| entry.name.as_str())
        .collect();
    assert_eq!(names, ["alpha", "beta", "camera.1"]);
    let beta = &playlist.entries[1];
    assert_eq!(beta.video, VideoRef::File(videos.join("beta.mov")));
    let [overlay] = beta.overlays.as_slice() else {
        panic!("one overlay per entry");
    };
    assert_eq!(
        (
            overlay.label.as_str(),
            overlay.handler.as_str(),
            &overlay.path
        ),
        ("beta", "ADF_BETA_FRAME", &overlays.join("beta.jsonl"))
    );
    assert!(overlay.options.is_empty());
}

#[test]
fn a_matched_compressed_file_is_refused_by_name() {
    let dir = tempfile::tempdir().unwrap();
    let (videos, overlays) = (dir.path().join("v"), dir.path().join("o"));
    touch(videos.join("clip.mp4"));
    touch(overlays.join("clip.txt.GZ"));
    touch(overlays.join("orphan.txt.gz"));

    let error = failure(&settings(&videos, &overlays));

    assert!(error.reason.contains("Compressed"), "{error:?}");
    assert_eq!(
        error.location,
        overlays.join("clip.txt.GZ").display().to_string()
    );
}

#[cfg(unix)]
#[test]
fn follows_symlinked_files() {
    let dir = tempfile::tempdir().unwrap();
    let (videos, overlays, store) = (
        dir.path().join("v"),
        dir.path().join("o"),
        dir.path().join("store"),
    );
    touch(store.join("real.mp4"));
    touch(overlays.join("clip.json"));
    fs::create_dir(&videos).unwrap();
    std::os::unix::fs::symlink(store.join("real.mp4"), videos.join("clip.mp4")).unwrap();
    std::os::unix::fs::symlink(store.join("gone.mp4"), videos.join("broken.mp4")).unwrap();

    let playlist = resolve(&settings(&videos, &overlays), &CancelToken::new()).unwrap();

    assert_eq!(playlist.entries.len(), 1);
    assert_eq!(playlist.entries[0].name, "clip");
}

#[test]
fn settings_and_folders_must_be_usable_and_name_the_problem() {
    let dir = tempfile::tempdir().unwrap();
    let present = settings(dir.path(), dir.path());
    for key in ["videos_dir", "overlays_dir", "handler_type"] {
        let mut missing = present.clone();
        missing.remove(key);
        assert_eq!(failure(&missing).location, key);
        missing.insert(key.to_string(), json!(""));
        assert_eq!(failure(&missing).location, key);
    }

    let gone = dir.path().join("gone");
    assert_eq!(
        failure(&settings(&gone, dir.path())).location,
        gone.display().to_string()
    );
    assert_eq!(
        failure(&settings(dir.path(), &gone)).location,
        gone.display().to_string()
    );
}

#[test]
fn duplicate_names_and_no_pairs_are_errors() {
    let dir = tempfile::tempdir().unwrap();
    let (videos, overlays) = (dir.path().join("v"), dir.path().join("o"));
    touch(videos.join("sample.mp4"));
    touch(overlays.join("other.json"));
    let no_pairs = failure(&settings(&videos, &overlays));
    assert!(
        no_pairs.reason.starts_with("No video file name"),
        "{no_pairs:?}"
    );

    touch(videos.join("sample.mov"));
    let duplicate = failure(&settings(&videos, &overlays));
    assert!(
        duplicate.reason.contains("Duplicate video file name(s)")
            && duplicate.reason.contains("sample")
    );
    assert_eq!(duplicate.location, videos.display().to_string());
}

#[test]
fn a_cancelled_token_stops_resolving_and_watching() {
    let dir = tempfile::tempdir().unwrap();
    touch(dir.path().join("v/a.mp4"));
    touch(dir.path().join("o/a.json"));
    let settings = settings(&dir.path().join("v"), &dir.path().join("o"));
    let cancel = CancelToken::new();
    cancel.cancel();

    assert!(matches!(
        resolve(&settings, &cancel),
        Err(Interrupt::Cancelled)
    ));
    assert!(
        resolver("folder_pair")
            .unwrap()
            .watch_paths(&settings, &cancel)
            .is_err()
    );
}

#[test]
fn watches_both_folders() {
    let (videos, overlays) = (Path::new("/videos"), Path::new("/overlays"));
    let watch = resolver("folder_pair")
        .unwrap()
        .watch_paths(&settings(videos, overlays), &CancelToken::new())
        .unwrap();

    assert_eq!(
        watch,
        WatchPaths {
            folders: [videos.into(), overlays.into()].into(),
            ..WatchPaths::default()
        }
    );
}
