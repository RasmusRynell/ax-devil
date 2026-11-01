//! MOT Challenge resolver behavior through the public registry.

use std::{fs, path::Path};

use foundation::{CancelToken, FrameRate, Interrupt, UserError};
use playlist_resolvers::{
    Choice, ImageSequence, ResolvedEntry, ResolvedPlaylist, Settings, VideoRef, resolver,
};
use serde_json::json;

fn make_sequence(root: &Path, name: &str, width: u32, height: u32, det: bool, gt: bool) {
    let dir = root.join(name);
    fs::create_dir_all(dir.join("img1")).unwrap();
    let ini = format!(
        "[Sequence]\nname={name}\nimDir=img1\nframeRate=30\nseqLength=100\nimWidth={width}\nimHeight={height}\nimExt=.jpg\n"
    );
    fs::write(dir.join("seqinfo.ini"), ini).unwrap();
    for (wanted, file) in [(det, "det/det.txt"), (gt, "gt/gt.txt")] {
        if wanted {
            fs::create_dir_all(dir.join(file).parent().unwrap()).unwrap();
            fs::write(dir.join(file), "1,1,1,1,1,1,1\n").unwrap();
        }
    }
}

fn simple(root: &Path, name: &str) {
    make_sequence(root, name, 1920, 1080, true, false);
}

fn settings(value: serde_json::Value) -> Settings {
    value.as_object().unwrap().clone()
}

fn root_settings(root: &Path) -> Settings {
    settings(json!({ "root": root }))
}

fn resolve(settings: &Settings) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
    resolver("mot_challenge")
        .unwrap()
        .resolve(settings, &CancelToken::new())
}

fn entries(settings: &Settings) -> Vec<ResolvedEntry> {
    resolve(settings).unwrap().entries
}

fn names(entries: &[ResolvedEntry]) -> Vec<&str> {
    entries.iter().map(|entry| entry.name.as_str()).collect()
}

fn labels(entry: &ResolvedEntry) -> Vec<&str> {
    entry
        .overlays
        .iter()
        .map(|overlay| overlay.label.as_str())
        .collect()
}

#[test]
fn lists_sorted_sequences_with_image_sequence_metadata_and_skips_other_folders() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::create_dir(root.join("random_folder")).unwrap();
    fs::create_dir(root.join("MOT16-02")).unwrap();
    fs::write(root.join("MOT16-02/seqinfo.ini"), "[Other]\nkey=val\n").unwrap();
    make_sequence(root, "MOT16-06", 640, 480, true, false);
    simple(root, "MOT16-01");

    let entries = entries(&root_settings(root));

    assert_eq!(names(&entries), ["MOT16-01", "MOT16-06"]);
    assert_eq!(
        entries[1].video,
        VideoRef::ImageSequence(ImageSequence {
            pattern: root.join("MOT16-06/img1/%06d.jpg"),
            start_number: 1,
            frame_rate: FrameRate::new(30, 1).unwrap(),
            width: 640,
            height: 480,
            frame_count: 100,
        })
    );
}

#[test]
fn scans_train_and_test_and_watches_the_root_and_each_seqinfo() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    simple(&root.join("train"), "MOT17-02-DPM");
    simple(&root.join("test"), "MOT17-01-DPM");
    let settings = root_settings(root);

    assert_eq!(names(&entries(&settings)), ["MOT17-01", "MOT17-02"]);

    let resolver = resolver("mot_challenge").unwrap();
    let watch = resolver
        .watch_paths(&settings, &CancelToken::new())
        .unwrap();
    assert_eq!(watch.folders, [root.to_path_buf()].into());
    assert_eq!(
        watch.files,
        [
            root.join("test/MOT17-01-DPM/seqinfo.ini"),
            root.join("train/MOT17-02-DPM/seqinfo.ini")
        ]
        .into()
    );
    let gone = root.join("gone");
    let watch = resolver
        .watch_paths(&root_settings(&gone), &CancelToken::new())
        .unwrap();
    assert_eq!((watch.files.len(), watch.folders), (0, [gone].into()));
}

#[test]
fn lanes_are_detections_then_one_ground_truth() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    make_sequence(root, "SEQ-01", 640, 480, true, true);
    make_sequence(root, "SEQ-02", 640, 480, false, false);
    make_sequence(root, "SEQ-03", 640, 480, false, true);
    for detector in ["DPM", "FRCNN", "SDP"] {
        make_sequence(
            root,
            &format!("MOT17-01-{detector}"),
            1920,
            1080,
            true,
            true,
        );
    }
    simple(root, "MOT17-02-DPM");

    let entries = entries(&root_settings(root));

    let by_name = |name: &str| entries.iter().find(|entry| entry.name == name).unwrap();
    assert_eq!(labels(by_name("SEQ-01")), ["DET", "GT"]);
    assert!(by_name("SEQ-02").overlays.is_empty());
    assert_eq!(labels(by_name("SEQ-03")), ["GT"]);
    assert_eq!(labels(by_name("MOT17-01")), ["DPM", "GT", "FRCNN", "SDP"]);
    assert_eq!(labels(by_name("MOT17-02")), ["DPM"]);
    assert_eq!(
        entries.len(),
        5,
        "variants share an entry, other sequences stay apart"
    );

    let det = &by_name("SEQ-01").overlays[0];
    assert_eq!(
        (det.handler.as_str(), &det.path),
        ("MOT_FILE", &root.join("SEQ-01/det/det.txt"))
    );
    assert!(
        det.options.is_empty(),
        "the host passes the decoded frame size"
    );
}

#[test]
fn lists_all_sequences_or_only_the_named_ones() {
    let dir = tempfile::tempdir().unwrap();
    simple(dir.path(), "SEQ-01");
    simple(dir.path(), "SEQ-02");

    assert_eq!(
        names(&entries(&root_settings(dir.path()))),
        ["SEQ-01", "SEQ-02"]
    );
    let subset = settings(json!({ "root": dir.path(), "sequences": ["SEQ-02", "GONE"] }));
    assert_eq!(names(&entries(&subset)), ["SEQ-02"]);
}

#[test]
fn bad_settings_and_empty_datasets_name_the_problem() {
    let dir = tempfile::tempdir().unwrap();
    simple(dir.path(), "SEQ-01");
    let root = dir.path();
    let gone = root.join("gone");
    let table = [
        (json!({}), "root"),
        (json!({ "root": "" }), "root"),
        (json!({ "root": root, "sequences": "SEQ-01" }), "sequences"),
        (json!({ "root": root, "sequences": [1] }), "sequences"),
        (
            json!({ "root": root, "sequences": ["GONE"] }),
            root.to_str().unwrap(),
        ),
        (json!({ "root": gone }), gone.to_str().unwrap()),
    ];
    for (value, location) in table {
        match resolve(&settings(value.clone())) {
            Err(Interrupt::Failed(error)) => assert_eq!(error.location, location, "{value}"),
            other => panic!("{value}: expected a failure, got {other:?}"),
        }
    }
}

#[test]
fn imdir_and_imext_set_the_image_pattern_and_a_hostile_imdir_fails_at_the_seqinfo() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    simple(root, "SEQ-01");
    let ini = root.join("SEQ-01/seqinfo.ini");
    let original = fs::read_to_string(&ini).unwrap();

    fs::write(
        &ini,
        original
            .replace("imDir=img1", "imDir=frames")
            .replace(".jpg", "png"),
    )
    .unwrap();
    let entries = entries(&root_settings(root));
    let VideoRef::ImageSequence(video) = &entries[0].video else {
        panic!("expected an image sequence");
    };
    assert_eq!(video.pattern, root.join("SEQ-01/frames/%06d.png"));

    fs::write(&ini, original.replace("imDir=img1", "imDir=../x")).unwrap();
    match resolve(&root_settings(root)) {
        Err(Interrupt::Failed(error)) => assert_eq!(error.location, ini.to_str().unwrap()),
        other => panic!("expected a failure, got {other:?}"),
    }
}

#[test]
fn a_cancelled_token_stops_resolving_and_watching() {
    let dir = tempfile::tempdir().unwrap();
    simple(dir.path(), "SEQ-01");
    let settings = root_settings(dir.path());
    let cancel = CancelToken::new();
    cancel.cancel();
    let resolver = resolver("mot_challenge").unwrap();

    assert!(matches!(
        resolver.resolve(&settings, &cancel),
        Err(Interrupt::Cancelled)
    ));
    assert!(resolver.watch_paths(&settings, &cancel).is_err());
}

fn scan(settings: &Settings, cancel: &CancelToken) -> Result<Vec<Choice>, Interrupt<UserError>> {
    resolver("mot_challenge")
        .unwrap()
        .scan("sequences", settings, cancel)
}

#[test]
fn a_scan_offers_each_sequence_by_name_with_its_size_rate_and_length() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    simple(&root.join("train"), "MOT17-02-DPM");
    make_sequence(&root.join("test"), "MOT17-01-DPM", 640, 480, true, false);
    fs::write(
        root.join("test/MOT17-01-DPM/seqinfo.ini"),
        "[Sequence]\nframeRate=14.5\nseqLength=7\nimWidth=640\nimHeight=480\n",
    )
    .unwrap();
    fs::create_dir(root.join("random")).unwrap();

    let choices = scan(&root_settings(root), &CancelToken::new()).unwrap();

    let offered: Vec<_> = choices
        .iter()
        .map(|choice| (&*choice.value, &*choice.label, &*choice.detail))
        .collect();
    assert_eq!(
        offered,
        [
            (
                "MOT17-01-DPM",
                "MOT17-01-DPM",
                "640×480 · 14.5 fps · 7 frames"
            ),
            (
                "MOT17-02-DPM",
                "MOT17-02-DPM",
                "1920×1080 · 30 fps · 100 frames"
            ),
        ]
    );
}

#[test]
fn a_scan_attaches_a_missing_root_to_the_root_setting_and_honours_cancel() {
    let dir = tempfile::tempdir().unwrap();
    simple(dir.path(), "SEQ-01");
    let gone = root_settings(&dir.path().join("gone"));
    for settings in [settings(json!({})), gone] {
        match scan(&settings, &CancelToken::new()) {
            Err(Interrupt::Failed(error)) => assert_eq!(error.location, "root"),
            other => panic!("expected a failure at root, got {other:?}"),
        }
    }
    assert!(
        scan(&root_settings(dir.path()), &CancelToken::new()).is_ok(),
        "an empty folder is no failure"
    );

    let cancel = CancelToken::new();
    cancel.cancel();
    assert!(matches!(
        scan(&root_settings(dir.path()), &cancel),
        Err(Interrupt::Cancelled)
    ));
    let other = resolver("mot_challenge").unwrap().scan(
        "root",
        &root_settings(dir.path()),
        &CancelToken::new(),
    );
    assert!(matches!(other, Err(Interrupt::Failed(error)) if error.location == "root"));
}
