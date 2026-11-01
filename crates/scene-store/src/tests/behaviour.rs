//! Sharing, ordering, memory bound, cancellation and failures.

use std::{sync::mpsc, thread, time::Duration};

use handlers::Handlers;
use overlay_sync::SampleKeys;
use scene::TimeKind;

use super::*;

fn failure(result: Result<Arc<Track>, Interrupt<foundation::UserError>>) -> foundation::UserError {
    match result {
        Err(Interrupt::Failed(error)) => error,
        Err(Interrupt::Cancelled) => panic!("cancelled"),
        Ok(_) => panic!("opened"),
    }
}

#[test]
fn two_opens_share_one_track_until_both_let_go() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 3,
        boxes: 1,
    });
    let (first, second) = (fixture.open(), fixture.open());
    assert!(Arc::ptr_eq(&first, &second));
    let other_size = fixture.open_sized(FrameSize {
        width: 10,
        height: 10,
    });
    assert!(
        !Arc::ptr_eq(&first, &other_size),
        "another frame size is another track"
    );

    drop((first, second));
    let again = fixture.open();

    assert_eq!(
        fixture.decoder.runs(),
        2,
        "reopening reads the artifact, it does not decode"
    );
    assert!(again.scene(0).is_ok());
}

#[test]
fn simultaneous_opens_decode_once() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 20,
        boxes: 1,
    });
    fixture
        .decoder
        .on_scene(|_, _| thread::sleep(Duration::from_millis(5)));

    let tracks: Vec<_> = thread::scope(|scope| {
        let opens: Vec<_> = (0..4).map(|_| scope.spawn(|| fixture.open())).collect();
        opens
            .into_iter()
            .map(|open| open.join().expect("joined"))
            .collect()
    });

    assert_eq!(fixture.decoder.runs(), 1);
    assert!(tracks.iter().all(|track| Arc::ptr_eq(track, &tracks[0])));
}

#[test]
fn cancelling_leaves_no_temporary_file_and_a_waiter_takes_over() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 10,
        boxes: 1,
    });
    let token = CancelToken::new();
    let (reached, signal) = mpsc::channel();
    let reached = Mutex::new(reached);
    fixture.decoder.on_scene({
        let decoder = fixture.decoder.clone();
        move |frame, cancel| {
            if frame == 3 && decoder.runs() == 1 {
                reached.lock().expect("lock").send(()).expect("sent");
                while !cancel.is_cancelled() {
                    thread::sleep(Duration::from_millis(1));
                }
            }
        }
    });

    thread::scope(|scope| {
        let leader = scope.spawn(|| fixture.registry.open(&fixture.overlay(), SIZE, &token));
        signal.recv().expect("the decode started");
        let waiter = scope.spawn(|| fixture.open());
        thread::sleep(Duration::from_millis(60));
        token.cancel();

        assert!(matches!(
            leader.join().expect("joined"),
            Err(Interrupt::Cancelled)
        ));
        let track = waiter.join().expect("joined");
        assert!(track.scene(9).is_ok(), "the waiter decoded it itself");
    });

    assert_eq!(fixture.decoder.runs(), 2);
    let leftovers: Vec<_> = fs::read_dir(fixture.cache().join("scenes"))
        .expect("scenes folder")
        .map(|e| e.expect("entry").file_name())
        .collect();
    assert_eq!(
        leftovers.len(),
        1,
        "only the finished artifact: {leftovers:?}"
    );
    assert!(!leftovers[0].to_string_lossy().ends_with(".tmp"));
}

#[test]
fn a_cancelled_open_leaves_nothing_behind() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 10,
        boxes: 1,
    });
    fixture.decoder.on_scene(|frame, cancel| {
        if frame == 4 {
            cancel.cancel();
        }
    });

    let result = fixture
        .registry
        .open(&fixture.overlay(), SIZE, &CancelToken::new());

    assert!(matches!(result, Err(Interrupt::Cancelled)));
    assert_eq!(fixture.artifacts(), Vec::<PathBuf>::new());
    let before_cancel = CancelToken::new();
    before_cancel.cancel();
    assert!(matches!(
        fixture
            .registry
            .open(&fixture.overlay(), SIZE, &before_cancel),
        Err(Interrupt::Cancelled)
    ));
}

#[test]
fn a_file_mixing_frame_numbers_and_timestamps_is_rejected() {
    let fixture = Fixture::new(Shape::Mixed);

    let error = failure(
        fixture
            .registry
            .open(&fixture.overlay(), SIZE, &CancelToken::new()),
    );

    assert!(
        error.reason.contains("mixes frame numbers and timestamps"),
        "{error}"
    );
    assert!(fixture.is_about_source(&error), "{error:?}");
    assert_eq!(fixture.artifacts(), Vec::<PathBuf>::new());
}

#[test]
fn samples_are_sorted_by_key_and_a_repeated_key_keeps_the_last_record() {
    let fixture = Fixture::new(Shape::Shuffled);

    let track = fixture.open();

    assert_eq!(
        track.keys(),
        &SampleKeys::Times(vec![MediaTime(1_000), MediaTime(2_000), MediaTime(3_000)].into())
    );
    assert_eq!(track.keys().kind(), TimeKind::Timestamp);
    let boxes: Vec<_> = (0..3)
        .map(|i| track.scene(i).expect("scene").entities().len())
        .collect();
    assert_eq!(
        boxes,
        [4, 3, 1],
        "the Scenes with 4, 3 and 1 boxes were emitted last for their keys"
    );
    assert!(
        track
            .warnings()
            .iter()
            .any(|w| w.problem.contains("share their key"))
    );
}

#[test]
fn the_scene_cache_stays_under_its_bound_over_a_long_track() {
    const BOUND: usize = 256 * 1024;
    let mut fixture = Fixture::new(Shape::Frames {
        frames: 10_000,
        boxes: 20,
    });
    fixture.registry =
        Fixture::registry_over(&fixture.decoder, Some(&fixture.cache())).with_scene_cache(BOUND);
    let track = fixture.open();
    assert_eq!(track.keys().len(), 10_000);

    let mut peak = 0;
    let mut frame = 1_u64;
    for _ in 0..30_000 {
        frame = frame
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407)
            % 10_000;
        let scene = track.scene(frame as usize).expect("scene");
        assert_eq!(scene.time().start(), SceneTime::Frame(frame));
        peak = peak.max(track.cached_bytes());
        assert!(track.cached_bytes() <= BOUND);
    }
    assert!(peak > BOUND / 2, "the cache is used: {peak}");
    let recent = track.scene(42).expect("scene");
    assert!(
        Arc::ptr_eq(&recent, &track.scene(42).expect("scene")),
        "a resident Scene is shared"
    );
    assert!(track.scene(10_000).is_err(), "past the last sample");
}

#[test]
fn a_scene_larger_than_the_whole_cache_is_served_but_not_kept() {
    let mut fixture = Fixture::new(Shape::Frames {
        frames: 2,
        boxes: 50,
    });
    fixture.registry =
        Fixture::registry_over(&fixture.decoder, Some(&fixture.cache())).with_scene_cache(100);

    let track = fixture.open();

    assert_eq!(track.scene(0).expect("scene").entities().len(), 50);
    assert_eq!(track.cached_bytes(), 0);
}

#[test]
fn without_a_cache_folder_nothing_is_written_and_nothing_is_reused() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 3,
        boxes: 1,
    });
    let registry = Fixture::registry_over(&fixture.decoder, None);
    let token = CancelToken::new();

    let track = registry
        .open(&fixture.overlay(), SIZE, &token)
        .expect("opens");
    assert!(track.scene(2).is_ok());
    drop(track);
    registry
        .open(&fixture.overlay(), SIZE, &token)
        .map(drop)
        .expect("opens");

    assert_eq!(fixture.decoder.runs(), 2);
    assert!(!fixture.cache().exists());
}

#[test]
fn failures_name_the_file_and_leave_no_artifact() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 3,
        boxes: 1,
    });
    let token = CancelToken::new();

    let mut missing = fixture.overlay();
    missing.path = fixture.dir.path().join("nope.txt");
    assert!(
        failure(fixture.registry.open(&missing, SIZE, &token))
            .reason
            .contains("Cannot find")
    );

    let mut unknown = fixture.overlay();
    unknown.handler = "NOPE".into();
    let error = failure(fixture.registry.open(&unknown, SIZE, &token));
    assert!(
        error.reason.contains("No decoder") && error.reason.contains("NOPE"),
        "{error}"
    );

    fs::write(&fixture.source, b"broken").expect("source");
    let error = failure(fixture.registry.open(&fixture.overlay(), SIZE, &token));
    assert_eq!(error.reason, "line 1: not a test file");
    assert!(fixture.is_about_source(&error), "{error:?}");
    assert_eq!(fixture.artifacts(), Vec::<PathBuf>::new());
    assert!(
        fixture
            .registry
            .open(&fixture.overlay(), SIZE, &token)
            .is_err(),
        "a failure is not remembered as a track"
    );
}

#[cfg(unix)]
#[test]
fn artifacts_and_their_folder_are_private() {
    use std::os::unix::fs::PermissionsExt;
    let fixture = Fixture::new(Shape::Frames {
        frames: 2,
        boxes: 1,
    });
    drop(fixture.open());

    let mode = |path: &Path| fs::metadata(path).expect("metadata").permissions().mode() & 0o777;

    assert_eq!(mode(&fixture.artifacts()[0]), 0o600);
    assert_eq!(mode(&fixture.cache().join("scenes")), 0o700);
}

#[test]
fn equal_options_make_one_key_whatever_their_order() {
    let json = |text: &str| match serde_json::from_str(text).expect("json") {
        serde_json::Value::Object(map) => map,
        other => panic!("{other}"),
    };

    let a = crate::registry::canonical_json(&json(r#"{"b":{"y":1,"x":[{"d":1,"c":2}]},"a":null}"#));
    let b = crate::registry::canonical_json(&json(r#"{"a":null,"b":{"x":[{"c":2,"d":1}],"y":1}}"#));

    assert_eq!(a, b);
    assert_eq!(a, r#"{"a":null,"b":{"x":[{"c":2,"d":1}],"y":1}}"#);
}

#[test]
fn the_real_mot_decoder_round_trips_through_an_artifact() {
    let dir = tempfile::tempdir().expect("temp dir");
    let gt = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/oracle/mot/gt.txt");
    let overlay = OverlayRef {
        label: "GT".into(),
        path: gt,
        handler: "MOT_FILE".into(),
        options: Settings::new(),
    };
    let size = FrameSize {
        width: 640,
        height: 480,
    };
    let token = CancelToken::new();
    let open = || {
        TrackRegistry::new(Handlers::built_in(), Some(dir.path()))
            .open(&overlay, size, &token)
            .map_err(|_| "open failed")
            .expect("opens")
    };

    let cold = open();
    let warm = open();

    for track in [&cold, &warm] {
        assert_eq!(track.keys(), &SampleKeys::Frames(vec![0, 1, 3].into()));
        assert_eq!(track.coverage(), Coverage::Dense);
        assert_eq!(track.warnings().len(), 1);
        assert_eq!(track.filter().options.len(), 14);
    }
    assert_eq!(cold.scene(2).expect("scene"), warm.scene(2).expect("scene"));
    assert_eq!(cold.scene(0).expect("scene").entities().len(), 3);
}
