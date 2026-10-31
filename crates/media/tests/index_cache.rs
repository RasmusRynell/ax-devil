//! The index cache file: when it is reused, when it is ignored and rebuilt, and when nothing is saved. A build
//! reports progress and a cache hit does not, which is how these tests tell the two apart.

mod common;

use common::fixture;
use foundation::{CancelToken, Interrupt};
use media::FrameIndex;
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

/// Breaks the cache file at a path.
type Damage = fn(&Path);

struct Setup {
    dir: TempDir,
    video: PathBuf,
    cache: PathBuf,
}

/// A temporary folder holding a copy of fixture `name` and an empty cache folder.
fn setup(name: &str) -> Setup {
    let dir = tempfile::tempdir().expect("temp dir");
    let video = dir.path().join(name);
    fs::copy(fixture(name), &video).expect("copy fixture");
    let cache = dir.path().join("cache");
    Setup { dir, video, cache }
}

/// The result of indexing, and the progress values reported on the way.
fn load(video: &Path, cache: &Path) -> (FrameIndex, Vec<f32>) {
    let mut reported = Vec::new();
    let index = FrameIndex::load_or_build(video, Some(cache), &CancelToken::new(), &mut |p| {
        reported.push(p)
    });
    (
        index.unwrap_or_else(|e| panic!("did not index: {e:?}")),
        reported,
    )
}

fn index_files(cache: &Path) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(cache.join("frame-index")) else {
        return Vec::new();
    };
    entries.map(|entry| entry.expect("entry").path()).collect()
}

fn same(a: &FrameIndex, b: &FrameIndex) -> bool {
    a.timeline() == b.timeline()
        && a.keyframes() == b.keyframes()
        && a.info() == b.info()
        && a.scan() == b.scan()
}

#[test]
fn an_unchanged_video_reloads_the_same_index_from_its_cache_file() {
    let s = setup("cfr_h264_gop10_bf2.mp4");
    let (built, reported) = load(&s.video, &s.cache);
    assert!(
        reported.len() >= 2 && reported.windows(2).all(|w| w[0] <= w[1]),
        "{reported:?}"
    );
    assert_eq!(
        (reported.first(), reported.last()),
        (Some(&0.0), Some(&1.0))
    );
    assert_eq!(index_files(&s.cache).len(), 1);

    let (reloaded, reported) = load(&s.video, &s.cache);
    assert!(reported.is_empty(), "a cache hit does no scan");
    assert!(same(&built, &reloaded));
}

#[test]
fn a_missing_damaged_or_other_version_file_is_rebuilt_and_saved_again() {
    let damages: [(&str, Damage); 6] = [
        ("deleted", |file| fs::remove_file(file).expect("remove")),
        ("empty", |file| fs::write(file, b"").expect("write")),
        ("text", |file| {
            fs::write(file, b"not an index at all").expect("write")
        }),
        ("cut in half", |file| {
            let bytes = fs::read(file).expect("read");
            fs::write(file, &bytes[..bytes.len() / 2]).expect("write");
        }),
        ("one flipped byte of the body", |file| {
            let mut bytes = fs::read(file).expect("read");
            let middle = bytes.len() * 3 / 4;
            bytes[middle] ^= 0x40;
            fs::write(file, bytes).expect("write");
        }),
        ("another version", |file| {
            let mut bytes = fs::read(file).expect("read");
            bytes[7] += 1; // the version byte follows the 7-byte magic
            fs::write(file, bytes).expect("write");
        }),
    ];
    for (name, damage) in damages {
        let s = setup("long_gop_h264.mp4");
        let (built, _) = load(&s.video, &s.cache);
        let file = index_files(&s.cache).remove(0);

        damage(&file);
        let (rebuilt, reported) = load(&s.video, &s.cache);

        assert!(!reported.is_empty(), "{name}: the damaged file was used");
        assert!(same(&built, &rebuilt), "{name}");
        let (_, reported) = load(&s.video, &s.cache);
        assert!(
            reported.is_empty(),
            "{name}: the rebuilt index was not saved"
        );
    }
}

#[test]
fn a_cache_file_of_a_video_that_changed_is_ignored() {
    let s = setup("cfr_h264_gop10_bf2.mp4");
    let (before, _) = load(&s.video, &s.cache);
    assert_eq!(before.timeline().len(), 90);

    // Same name, other content.
    fs::copy(fixture("long_gop_h264.mp4"), &s.video).expect("replace");
    let (replaced, reported) = load(&s.video, &s.cache);
    assert!(!reported.is_empty());
    assert_eq!(replaced.timeline().len(), 180);

    // Same content, new modification time.
    let file = fs::File::options()
        .write(true)
        .open(&s.video)
        .expect("open");
    file.set_modified(std::time::SystemTime::now() - std::time::Duration::from_secs(3600))
        .expect("touch");
    drop(file);
    let (_, reported) = load(&s.video, &s.cache);
    assert!(
        !reported.is_empty(),
        "a changed modification time is a changed file"
    );
}

#[test]
fn a_video_replaced_during_the_build_is_not_saved() {
    let s = setup("cfr_h264_gop10_bf2.mp4");
    let mut replaced = false;
    let index =
        FrameIndex::load_or_build(&s.video, Some(&s.cache), &CancelToken::new(), &mut |_| {
            if !replaced {
                replaced = true;
                let mut bytes = fs::read(&s.video).expect("read");
                bytes.push(0);
                fs::write(&s.video, bytes).expect("replace while indexing");
            }
        });
    assert!(index.is_ok(), "the build itself still finishes");
    assert!(replaced);
    assert!(
        index_files(&s.cache).is_empty(),
        "nothing was saved for a source that changed during the build"
    );

    let (_, reported) = load(&s.video, &s.cache);
    assert!(!reported.is_empty());
    assert_eq!(
        index_files(&s.cache).len(),
        1,
        "an unchanged source is saved"
    );
}

#[test]
fn a_cancelled_build_saves_nothing() {
    let s = setup("cfr_h264_gop10_bf2.mp4");
    let cancel = CancelToken::new();
    let result =
        FrameIndex::load_or_build(&s.video, Some(&s.cache), &cancel, &mut |_| cancel.cancel());
    assert!(matches!(result, Err(Interrupt::Cancelled)));
    assert!(index_files(&s.cache).is_empty());
}

#[test]
fn file_names_are_cut_by_utf8_bytes_and_stay_apart_by_folder() {
    // 3-byte and 2-byte characters, with an ASCII one in front so the cut does not fall on a boundary.
    for name in [
        format!("a{}.mp4", "漢".repeat(70)),
        format!("{}.mp4", "é".repeat(100)),
    ] {
        assert!(name.len() <= 255, "the video itself needs a valid name");
        let dir = tempfile::tempdir().expect("temp dir");
        let video = dir.path().join(&name);
        fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &video).expect("copy");
        let cache = dir.path().join("cache");

        load(&video, &cache);
        let files = index_files(&cache);
        assert_eq!(files.len(), 1);
        let cached = files[0]
            .file_name()
            .and_then(|n| n.to_str())
            .expect("a valid UTF-8 name")
            .to_string();
        let stem = cached.strip_suffix(".axidx").expect("the extension");
        let (name_part, hash) = stem.rsplit_once('-').expect("a hash after the name");
        assert!(name_part.len() <= 120, "{} bytes", name_part.len());
        assert!(
            name.starts_with(name_part) && name_part.len() >= 118,
            "the name is only cut, at most one character"
        );
        assert_eq!(hash.len(), 16);

        let (_, reported) = load(&video, &cache);
        assert!(reported.is_empty(), "the capped name is found again");
    }

    let (first, second) = (
        setup("cfr_h264_gop10_bf2.mp4"),
        setup("cfr_h264_gop10_bf2.mp4"),
    );
    let cache = first.dir.path().join("shared-cache");
    load(&first.video, &cache);
    load(&second.video, &cache);
    assert_eq!(
        index_files(&cache).len(),
        2,
        "the same name in two folders has two index files"
    );
}
