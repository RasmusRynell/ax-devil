//! When an artifact is reused, republished or rebuilt.

use std::{
    fs::{self, File},
    time::{Duration, SystemTime},
};

use scene::Coverage;

use super::*;
use crate::{
    format::{Identity, Tail},
    load::republish,
    stat::Stat,
};

const SHAPE: Shape = Shape::Frames {
    frames: 3,
    boxes: 2,
};

fn identity_of(artifact: &Path) -> Identity {
    let file = File::open(artifact).expect("artifact");
    Tail::read(&file)
        .expect("tail")
        .identity()
        .expect("identity")
}

fn patch_identity(artifact: &Path, change: impl FnOnce(&mut Identity)) {
    let file = File::open(artifact).expect("artifact");
    let tail = Tail::read(&file).expect("tail");
    let mut identity = tail.identity().expect("identity");
    change(&mut identity);
    republish(artifact, &file, &tail, &identity).expect("republished");
}

fn rewrite(artifact: &Path, change: impl FnOnce(&mut Vec<u8>)) {
    let mut bytes = fs::read(artifact).expect("artifact");
    change(&mut bytes);
    fs::write(artifact, bytes).expect("written");
}

#[test]
fn a_warm_reopen_decodes_nothing_and_reports_what_the_decode_did() {
    let mut fixture = Fixture::new(SHAPE);
    let cold = fixture.open();
    let first = cold.scene(0).expect("scene");
    assert_eq!(fixture.decoder.runs(), 1);
    drop(cold);
    fixture.restart();

    let warm = fixture.open();

    assert_eq!(fixture.decoder.runs(), 1, "the artifact was used");
    assert_eq!(warm.snapshot().keys, frame_keys(&[0, 1, 2]));
    assert_eq!(warm.coverage(), Coverage::Dense);
    assert_eq!(warm.warnings().len(), 1);
    assert_eq!(warm.warnings()[0].problem, "Skipped 1 of 3 rows");
    assert_eq!(warm.filter().name, "test");
    assert_eq!(warm.metadata().len(), 1);
    assert_eq!(*warm.scene(0).expect("scene"), *first);
}

#[test]
fn each_identity_component_invalidates() {
    type Change = fn(&mut Identity);
    let table: [(&str, Change); 8] = [
        ("handler", |id| id.handler = "OTHER".into()),
        ("artifact version", |id| id.artifact_version += 1),
        ("scene major", |id| id.scene_major += 1),
        ("scene minor", |id| id.scene_minor += 1),
        ("options", |id| id.options = r#"{"a":1}"#.into()),
        ("code", |id| id.code = Some([1; 32])),
        ("digest missing", |id| id.digest = None),
        ("digest differs", |id| {
            id.digest = id.digest.map(|mut digest| {
                digest[0] ^= 1;
                digest
            });
            id.stat.mtime_ns += 1;
        }),
    ];
    for (name, change) in table {
        let mut fixture = Fixture::new(SHAPE);
        drop(fixture.open());
        patch_identity(&fixture.artifacts()[0], change);
        fixture.restart();

        drop(fixture.open());

        assert_eq!(fixture.decoder.runs(), 2, "{name} must invalidate");
    }
}

#[test]
fn the_store_format_version_invalidates() {
    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    rewrite(&fixture.artifacts()[0], |bytes| bytes[4] ^= 0xFF);
    fixture.restart();

    drop(fixture.open());

    assert_eq!(fixture.decoder.runs(), 2);
}

#[test]
fn options_and_decoder_version_make_artifacts_that_coexist() {
    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    let mut overlay = fixture.overlay();
    overlay.options.insert("profile".into(), "other".into());
    let token = CancelToken::new();
    fixture
        .registry
        .open(&overlay, &token)
        .map(drop)
        .expect("opens");
    assert_eq!(
        fixture.decoder.runs(),
        2,
        "options are part of the identity"
    );
    assert_eq!(fixture.artifacts().len(), 2);

    fixture.restart();
    drop(fixture.open());
    assert_eq!(fixture.decoder.runs(), 2, "every variant is still reusable");

    let newer = Arc::new(Counting {
        spec: spec(2),
        shape: SHAPE,
        runs: AtomicUsize::new(0),
        hook: Mutex::new(None),
    });
    let registry = Fixture::registry_over(&newer, Some(&fixture.cache()));
    registry
        .open(&fixture.overlay(), &token)
        .map(drop)
        .expect("opens");
    assert_eq!(newer.runs(), 1, "a new decoder version rebuilds");
    assert_eq!(fixture.artifacts().len(), 2, "in place");
}

#[test]
fn a_stat_only_difference_republishes_without_decoding() {
    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    let before = identity_of(&fixture.artifacts()[0]);
    let later = SystemTime::now() + Duration::from_secs(5);
    File::options()
        .write(true)
        .open(&fixture.source)
        .and_then(|file| file.set_modified(later))
        .expect("touched");
    fixture.restart();

    let track = fixture.open();

    assert_eq!(fixture.decoder.runs(), 1);
    let after = identity_of(&fixture.artifacts()[0]);
    assert_ne!(after.stat, before.stat);
    assert_eq!(after.digest, before.digest);
    let source = File::open(&fixture.source).expect("source");
    assert_eq!(after.stat, Stat::of(&source).expect("stat"));
    assert!(
        track.scene(2).is_ok(),
        "the republished artifact serves Scenes"
    );
    assert_eq!(fixture.artifacts().len(), 1, "no temporary file is left");
}

#[test]
fn an_edit_that_restores_the_modification_time_is_still_noticed() {
    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    let modified = fs::metadata(&fixture.source)
        .and_then(|m| m.modified())
        .expect("mtime");
    fs::write(&fixture.source, b"rows rows ROWS\n").expect("edited, same size");
    File::options()
        .write(true)
        .open(&fixture.source)
        .and_then(|file| file.set_modified(modified))
        .expect("mtime restored");
    fixture.restart();

    drop(fixture.open());

    assert_eq!(fixture.decoder.runs(), 2);
}

#[test]
fn a_source_that_changes_during_the_build_is_stored_without_a_digest() {
    let mut fixture = Fixture::new(SHAPE);
    let source = fixture.source.clone();
    fixture.decoder.on_scene(move |frame, _| {
        if frame == 1 {
            let mut file = File::options().append(true).open(&source).expect("source");
            std::io::Write::write_all(&mut file, b"more\n").expect("appended");
        }
    });

    let track = fixture.open();

    assert!(
        track.scene(2).is_err(),
        "the file is not the one the track began with"
    );
    assert_eq!(identity_of(&fixture.artifacts()[0]).digest, None);
    *fixture.decoder.hook.lock().expect("hook lock") = None;
    drop(track);
    fixture.restart();
    drop(fixture.open());
    assert_eq!(
        fixture.decoder.runs(),
        2,
        "an artifact without a digest is never reused"
    );
    assert!(identity_of(&fixture.artifacts()[0]).digest.is_some());
    fixture.restart();
    drop(fixture.open());
    assert_eq!(fixture.decoder.runs(), 2, "the rebuilt one is");
}

#[test]
fn an_in_place_edit_stops_serving_unread_scenes_but_not_the_ones_already_read() {
    let fixture = Fixture::new(SHAPE);
    let track = fixture.open();
    let read = track.scene(0).expect("scene");

    fs::write(&fixture.source, b"rows rows rows rows\n").expect("rewritten in place");

    assert_eq!(*track.scene(0).expect("still cached"), *read);
    let error = track.scene(1).expect_err("not served");
    assert!(error.reason.contains("changed while open"), "{error}");
    assert!(fixture.is_about_source(&error));
    assert!(track.scene(2).is_err(), "and it stays so");
    let reopened = fixture.open();
    assert!(
        !Arc::ptr_eq(&track, &reopened),
        "the edited file is read again"
    );
    assert_eq!(fixture.decoder.runs(), 2);
    assert!(reopened.scene(2).is_ok());
}

#[test]
fn replacing_the_path_leaves_the_opened_file_served() {
    let fixture = Fixture::new(SHAPE);
    let track = fixture.open();
    let replacement = fixture.dir.path().join("replacement.txt");
    fs::write(&replacement, b"other\n").expect("replacement");
    fs::rename(&replacement, &fixture.source).expect("replaced");

    assert!(track.scene(1).is_ok() && track.scene(2).is_ok());
    assert!(Arc::ptr_eq(&track, &fixture.open()), "still the same track");
}

#[test]
fn corrupt_or_foreign_artifacts_are_rebuilt() {
    type Damage = fn(&mut Vec<u8>);
    let table: [(&str, Damage); 6] = [
        ("truncated", |b| b.truncate(b.len() / 2)),
        ("empty", |b| b.clear()),
        ("foreign", |b| {
            *b = b"this is not a scene artifact at all".repeat(10)
        }),
        ("magic", |b| b[0] = b'X'),
        ("end mark", |b| {
            let last = b.len() - 1;
            b[last] ^= 0xFF;
        }),
        ("tail checksum", |b| {
            let at = b.len() - 48;
            b[at] ^= 0xFF;
        }),
    ];
    for (name, damage) in table {
        let mut fixture = Fixture::new(SHAPE);
        drop(fixture.open());
        rewrite(&fixture.artifacts()[0], damage);
        fixture.restart();

        let track = fixture.open();

        assert_eq!(fixture.decoder.runs(), 2, "{name} is rebuilt");
        assert!(track.scene(1).is_ok());
        drop(track);
        fixture.restart();
        drop(fixture.open());
        assert_eq!(
            fixture.decoder.runs(),
            2,
            "{name}: the rebuilt artifact is valid"
        );
    }
}

#[test]
fn a_damaged_record_is_an_error_for_that_scene_and_the_next_open_rebuilds() {
    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    rewrite(&fixture.artifacts()[0], |bytes| bytes[10] ^= 0xFF);
    fixture.restart();

    let track = fixture.open();

    assert_eq!(fixture.decoder.runs(), 1, "records are checked when read");
    let error = track.scene(0).expect_err("damaged");
    assert!(error.reason.contains("damaged"), "{error}");
    assert!(error.reason.contains("sample 0"), "{error}");
    assert!(track.scene(1).is_ok() && track.scene(2).is_ok());

    // Opening again does not share or reuse the damaged artifact, and the track in hand keeps its file.
    assert!(
        fixture.artifacts().is_empty(),
        "the damaged artifact is unlinked"
    );
    let rebuilt = fixture.open();
    assert_eq!(fixture.decoder.runs(), 2);
    assert!(rebuilt.scene(0).is_ok() && track.scene(2).is_ok());
    drop((track, rebuilt));
    fixture.restart();
    assert!(fixture.open().scene(0).is_ok());
    assert_eq!(fixture.decoder.runs(), 2, "the rebuilt artifact is valid");
}

#[test]
fn an_artifact_claiming_a_huge_section_is_discarded_without_allocating_it() {
    use std::io::{Seek, SeekFrom, Write};

    let mut fixture = Fixture::new(SHAPE);
    drop(fixture.open());
    // A sparse file whose footer puts every section offset at the start, leaving a terabyte as the identity.
    let len = 1_u64 << 40;
    let mut file = fs::OpenOptions::new()
        .write(true)
        .open(&fixture.artifacts()[0])
        .expect("artifact");
    file.set_len(len).expect("sparse");
    file.seek(SeekFrom::Start(len - 40)).expect("seek");
    for word in [8_u64; 4] {
        file.write_all(&word.to_le_bytes()).expect("offset");
    }
    file.write_all(&[0; 4]).expect("checksum");
    file.write_all(b"AXSE").expect("end mark");
    drop(file);
    fixture.restart();

    assert!(fixture.open().scene(1).is_ok());
    assert_eq!(fixture.decoder.runs(), 2, "it is rebuilt");
}
