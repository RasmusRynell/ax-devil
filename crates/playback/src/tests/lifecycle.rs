use crate::Direction::Forward;
use crate::session::Pull;
use crate::testing::{Fixture, Log, block_on, deliveries, factory, poll_now, spec, want};
use crate::worker::Step;
use crate::{CachePool, DecoderFactory, Delivery, Registry, Session};
use foundation::{CancelToken, ErrorCode, HostPath, UserError};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

/// Generous only so a broken test fails instead of hanging; no test asserts how long anything takes.
const FAILSAFE: Duration = Duration::from_secs(30);

fn crate_file(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(name)
}

#[test]
fn closing_stops_the_worker_at_the_next_frame_and_returns_the_share_at_once() {
    let mut fx = Fixture::new(120, 10, 24);
    let other = Arc::new(fx.pool.add_source());
    let session = Arc::new(Mutex::new(Some(fx.session())));
    session
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .want(want(0, 20, 0, Forward));

    let observed = Arc::new(AtomicUsize::new(0));
    let (closer, watcher, seen) = (
        Arc::clone(&session),
        Arc::clone(&other),
        Arc::clone(&observed),
    );
    fx.log.on_frame(move |id| {
        if id == 3 {
            drop(closer.lock().unwrap().take());
            seen.store(watcher.budget_bytes(), Ordering::SeqCst);
        }
    });

    assert_eq!(fx.step(0), Step::Ran);
    assert_eq!(
        fx.log.decoded(),
        [0, 1, 2, 3],
        "no frame after the one in progress"
    );
    assert_eq!(
        observed.load(Ordering::SeqCst),
        24 * 100,
        "the share was back while the worker was still inside the decode"
    );
    assert_eq!((fx.step(0), fx.step(1)), (Step::Closed, Step::Closed));
    assert!(
        matches!(fx.source.pull(0), Pull::Closed),
        "nothing more is delivered"
    );
}

#[test]
fn sessions_of_one_file_share_one_source_and_the_last_one_out_returns_the_share() {
    let pool = CachePool::new(1000);
    let registry = Registry::new(Arc::clone(&pool));
    let log = Arc::new(Log::default());
    let open = |path: &Path| {
        registry
            .open(path, spec(60, 10), factory(&log, 60))
            .unwrap()
    };

    let first = open(&crate_file("Cargo.toml"));
    let alias = open(&crate_file("src/../Cargo.toml"));
    assert_eq!(pool.source_count(), 1, "one canonical path, one source");
    let other = open(&crate_file("src/lib.rs"));
    assert_eq!(pool.source_count(), 2);

    drop(first);
    assert_eq!(
        pool.source_count(),
        2,
        "the second viewer keeps the source open"
    );
    drop(alias);
    assert_eq!(pool.source_count(), 1);
    assert!(registry.attach(&crate_file("Cargo.toml")).is_none());

    drop(other);
    assert!(registry.wait_retired(FAILSAFE));
    assert_eq!(registry.running_workers(), 0);
}

#[test]
fn a_stuck_decoder_blocks_neither_its_own_close_nor_another_source() {
    let pool = CachePool::new(2000);
    let registry = Registry::new(Arc::clone(&pool));
    let stuck_log = Arc::new(Log::default());
    let (entered, entered_rx) = mpsc::channel();
    let (release, release_rx) = mpsc::channel::<()>();
    stuck_log.on_frame(move |id| {
        if id == 3 {
            let _ = entered.send(());
            let _ = release_rx.recv_timeout(FAILSAFE);
        }
    });
    let stuck = registry
        .open(
            &crate_file("Cargo.toml"),
            spec(60, 10),
            factory(&stuck_log, 60),
        )
        .unwrap();
    let mut healthy = registry
        .open(
            &crate_file("src/lib.rs"),
            spec(60, 10),
            factory(&Arc::new(Log::default()), 60),
        )
        .unwrap();

    stuck.want(want(0, 10, 0, Forward));
    entered_rx
        .recv_timeout(FAILSAFE)
        .expect("a worker entered the blocked decode");
    stuck.close();
    assert_eq!(
        pool.source_count(),
        1,
        "closing returned its share without waiting for the decode"
    );
    assert!(
        registry.running_workers() >= 3,
        "the stuck worker is still inside its frame"
    );

    healthy.want(want(0, 5, 0, Forward));
    let first =
        block_on(healthy.next_delivery(), FAILSAFE).expect("the healthy source still delivers");
    assert!(matches!(first, Some(Delivery::Frame(frame)) if frame.id().sequence_id == 0));

    drop(healthy);
    release.send(()).unwrap();
    assert!(
        registry.wait_retired(FAILSAFE),
        "every worker ends once the stuck decode returns"
    );
}

#[test]
fn a_decoder_panic_fails_the_frames_of_its_span_and_the_worker_recovers() {
    let mut fx = Fixture::new(120, 10, 40);
    *crate::lock(&fx.log.panic_at) = Some(5);
    let mut session = fx.session();
    session.want(want(0, 9, 0, Forward));
    fx.drain();
    let sent = deliveries(&mut session);
    assert_eq!(
        sent,
        (0..10).map(|id| (id, id >= 5)).collect::<Vec<_>>(),
        "frames before the panic arrive, the rest of that span fail"
    );

    session.want(want(50, 3, 0, Forward));
    fx.drain();
    assert_eq!(
        deliveries(&mut session),
        (50..=53).map(|id| (id, false)).collect::<Vec<_>>()
    );
}

#[test]
fn a_viewer_joining_as_the_last_one_leaves_never_gets_a_closing_source() {
    let fx = Fixture::new(60, 10, 24);
    let leaver = fx.session();
    let joined = Arc::new(Mutex::new(None));
    let (slot, source) = (Arc::clone(&joined), Arc::clone(&fx.source));
    *crate::lock(&fx.source.after_last_leave) = Some(Box::new(move || {
        *slot.lock().unwrap() = Some(Session::join(&source));
    }));

    drop(leaver);

    let attempt = joined.lock().unwrap().take().expect("the hook ran");
    assert!(
        attempt.is_none(),
        "a source whose last viewer left is closed to newcomers"
    );
}

#[test]
fn a_viewer_who_joined_before_the_last_other_left_keeps_a_working_source() {
    let mut fx = Fixture::new(60, 10, 24);
    let first = fx.session();
    let mut second = fx.session();
    second.want(want(3, 2, 0, Forward));
    drop(first);
    fx.drain();
    assert_eq!(
        deliveries(&mut second),
        [(3, false), (4, false), (5, false)]
    );
}

#[test]
fn a_file_reopened_right_after_its_last_viewer_left_gets_a_fresh_working_source() {
    let registry = Registry::new(CachePool::new(1000));
    let log = Arc::new(Log::default());
    let path = crate_file("Cargo.toml");
    let first = registry
        .open(&path, spec(60, 10), factory(&log, 60))
        .unwrap();
    drop(first);
    assert!(registry.attach(&path).is_none());

    let mut second = registry
        .open(&path, spec(60, 10), factory(&log, 60))
        .unwrap();
    second.want(want(0, 3, 0, Forward));
    let got = block_on(second.next_delivery(), FAILSAFE).expect("the fresh source delivers");
    assert!(matches!(got, Some(Delivery::Frame(frame)) if frame.id().sequence_id == 0));
    drop(second);
    assert!(registry.wait_retired(FAILSAFE));
}

#[test]
fn a_session_reports_the_spec_of_the_source_it_joined_never_a_stale_one() {
    let registry = Registry::new(CachePool::new(1000));
    let log = Arc::new(Log::default());
    let path = crate_file("Cargo.toml");

    let first = registry
        .open(&path, spec(60, 10), factory(&log, 60))
        .unwrap();
    // Two openers racing for one file end up on one source: the second's spec is not the source's.
    let second = registry
        .open(&path, spec(30, 5), factory(&log, 30))
        .unwrap();
    assert_eq!(second.spec().timeline.len(), 60);
    let retained = first.spec().clone();
    drop(first);
    drop(second);

    let reopened = registry
        .open(&path, spec(30, 5), factory(&log, 30))
        .unwrap();
    assert_eq!(
        retained.timeline.len(),
        60,
        "the old spec describes the old recording"
    );
    assert_eq!(
        reopened.spec().timeline.len(),
        30,
        "the new session has the new recording"
    );
    assert_eq!(reopened.spec().keyframes.as_slice().len(), 6);
    let attached = registry.attach(&path).unwrap();
    assert_eq!(attached.spec().timeline.len(), 30);
}

#[test]
fn the_decoder_gets_the_sources_token_and_a_thread_budget_and_closing_cancels_the_token() {
    let seen: Arc<Mutex<Vec<(CancelToken, usize)>>> = Arc::default();
    let record = Arc::clone(&seen);
    let log = Arc::new(Log::default());
    let inner = factory(&log, 60);
    let recording: DecoderFactory = Arc::new(move |token, threads| {
        record.lock().unwrap().push((token.clone(), threads));
        inner(token, threads)
    });
    let mut fx = Fixture::with_factory(60, 10, 24, recording);
    let session = fx.session();
    session.want(want(0, 3, 0, Forward));
    fx.drain();

    let (token, threads) = seen.lock().unwrap()[0].clone();
    assert!((1..=8).contains(&threads), "{threads} decode threads");
    assert!(!token.is_cancelled());
    drop(session);
    assert!(
        token.is_cancelled(),
        "a decoder blocked in input is interrupted when the source closes"
    );
}

#[test]
fn a_decoder_that_cannot_open_ends_every_session_once_instead_of_failing_frame_by_frame() {
    let failing: DecoderFactory = Arc::new(|_, _| {
        Err(
            UserError::new(ErrorCode::FileChanged, "the video was replaced")
                .at(HostPath::new("video.mp4")),
        )
    });
    let mut fx = Fixture::with_factory(60, 10, 24, failing);
    let mut first = fx.session();
    let mut second = fx.session();
    first.want(want(5, 4, 0, Forward));
    second.want(want(40, 4, 0, Forward));
    fx.drain();

    for session in [&mut first, &mut second] {
        let told = poll_now(session.next_delivery());
        assert!(
            matches!(&told, Some(Some(Delivery::Failed(error))) if error.reason == "the video was replaced" && error.location == Some(HostPath::new("video.mp4").into())),
            "{told:?}"
        );
        assert!(
            matches!(poll_now(session.next_delivery()), Some(None)),
            "the session is over after the one error"
        );
    }
    assert!(
        crate::lock(&fx.source.state).failed.is_empty(),
        "no per-frame failures"
    );
    assert_eq!(
        fx.step(0),
        crate::worker::Step::Idle,
        "the source stops decoding"
    );
}

#[test]
fn errors_playback_raises_name_the_file_and_only_the_cause() {
    let mut fx = Fixture::new(120, 10, 40);
    *crate::lock(&fx.log.panic_at) = Some(5);
    let mut session = fx.session();
    session.want(want(0, 9, 0, Forward));
    fx.drain();

    let mut failures = Vec::new();
    while let Some(Some(delivery)) = poll_now(session.next_delivery()) {
        if let Delivery::Failure(failure) = delivery {
            failures.push(failure);
        }
    }
    assert!(!failures.is_empty());
    for failure in failures {
        assert_eq!(
            failure.error.location,
            Some(HostPath::new("video.mp4").into()),
            "the file, not a subsystem"
        );
        assert!(
            !failure.error.reason.contains("rame"),
            "{}",
            failure.error.reason
        );
    }
}
