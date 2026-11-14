//! A track still being read: the lane sends the frames its samples have decided, once, and no others.

use std::{
    path::{Path, PathBuf},
    sync::{
        Arc, LazyLock, Mutex,
        mpsc::{Receiver, RecvTimeoutError, Sender, channel},
    },
    thread,
    time::Duration,
};

use foundation::{CancelToken, FrameRate, Interrupt, MediaTime, UserError};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Header, Input, OnEqualKey, OverlayRef,
    Record, SceneSink, Settings, Spec, Values,
};
use scene::matching::{MatchPolicy, Sticky};
use scene::{
    BoundingBox, Coverage, Entity, EntityId, FilterPolicy, Geometry, MatchBasis, Observation,
    Scene, SceneTime, TimeSlice,
};
use scene_store::{Admission, Feed, Ingest, LiveTrack, TrackRead};

use super::{
    Env, SAMPLE_BUDGET, Sent, cells, next, overlay_at, sample_ids, settle_into, timeline, want,
};
use crate::{
    FileScopeBlocked, LaneStatus, OverlayEvent, OverlayService, ViewerOverlays,
    sources::TrackSource,
};

static PACED: LazyLock<Spec> = LazyLock::new(|| Spec {
    id: "PACED".into(),
    plugin: "test".into(),
    name: "Paced".into(),
    extensions: vec![".paced".into()],
    description: None,
    framing: Framing::Whole,
    bases: vec![MatchBasis::Frame],
    default_basis: MatchBasis::Frame,
    options: Form::default(),
    version: 1,
});

/// The same, keyed by timestamps that count from a recording's UTC time, not from the video's 0.
static PACED_TIMED: LazyLock<Spec> = LazyLock::new(|| Spec {
    id: "PACED_TIMED".into(),
    bases: vec![MatchBasis::Frame, MatchBasis::Time],
    ..PACED.clone()
});

/// 2023-11-14T22:13:20Z in microseconds.
const RECORDING_START: i64 = 1_700_000_000_000_000;

/// The store publishes at most every 250 ms, so the decoder gives each Scene that long after the last.
const PUBLISH_GAP: Duration = Duration::from_millis(260);

/// A decoder that gives one Scene for each key the test sends, frame-keyed or, for [`PACED_TIMED`], keyed by the
/// recording's time of the frame, and ends when the sender is dropped.
struct Paced(Mutex<Receiver<u64>>, &'static Spec);

fn scene_at(key: u64, timed: bool) -> Scene {
    let time = if timed {
        SceneTime::Timestamp(MediaTime(RECORDING_START + key as i64 * 40_000))
    } else {
        SceneTime::Frame(key as i64)
    };
    let mut entity = Entity::new(EntityId::from(key));
    entity.push_observation(Observation::new(
        time,
        Geometry::Box(BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box")),
    ));
    Scene::new(TimeSlice::at(time))
        .with_entities(vec![entity])
        .expect("one clock")
}

fn policy() -> FilterPolicy {
    FilterPolicy {
        name: "paced",
        options: Vec::new(),
    }
}

impl Decoder for Paced {
    fn spec(&self) -> &Spec {
        self.1
    }

    fn decode(
        &self,
        _input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        sink.header(Header {
            filter: policy(),
            coverage: Coverage::Sparse,
            metadata: Vec::new(),
        });
        let keys = self.0.lock().expect("keys lock");
        loop {
            cancel.check()?;
            match keys.recv_timeout(Duration::from_millis(20)) {
                Ok(key) => {
                    thread::sleep(PUBLISH_GAP);
                    if sink
                        .sample(
                            scene_at(key, self.1.bases.contains(&MatchBasis::Time)),
                            OnEqualKey::Merge,
                        )
                        .is_break()
                    {
                        break;
                    }
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: policy(),
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// A viewer of ten frames over a paced file; dropping the sender ends the file. The lane keeps a sample until the
/// next, so every decided frame has a cell.
fn viewer_over_paced_file() -> (Env, ViewerOverlays, Sender<u64>) {
    viewer_over(&PACED)
}

fn viewer_over(spec: &'static Spec) -> (Env, ViewerOverlays, Sender<u64>) {
    let (keys, receiver) = channel();
    let env = Env::with_decoder(Arc::new(Paced(Mutex::new(receiver), spec)));
    let overlay: OverlayRef = env.overlay("a.paced", spec.id.as_str(), "rows\n");
    let viewer = env.open(vec![Some(overlay)], 10);
    viewer.want(want(0, 0, 9));
    let sticky = MatchPolicy {
        sticky: Sticky {
            enabled: true,
            timeout: None,
        },
        ..MatchPolicy::default()
    };
    viewer.set_policy(0, 1, Some(sticky), true);
    (env, viewer, keys)
}

/// Records events until `done` holds of what was sent, then until nothing more comes.
async fn record_until(viewer: &mut ViewerOverlays, sent: &mut Sent, done: impl Fn(&Sent) -> bool) {
    while !done(sent) {
        sent.record(next(viewer).await);
    }
    settle_into(viewer, sent).await;
}

#[tokio::test]
async fn frames_are_sent_once_when_the_ingest_decides_them_and_not_before() {
    let (_env, mut viewer, keys) = viewer_over_paced_file();
    let mut sent = Sent::default();

    // Samples at frames 0 and 3 decide frames 0 to 2, and the file has nothing for later frames yet.
    keys.send(0).expect("sent");
    keys.send(3).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 3).await;
    assert_eq!(cells(&sent, 0), [(0, 0), (1, 0), (2, 0)]);
    assert_eq!(sample_ids(&sent), [0]);

    keys.send(6).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 6).await;
    assert_eq!(
        cells(&sent, 0),
        [(0, 0), (1, 0), (2, 0), (3, 1), (4, 1), (5, 1)]
    );
    assert_eq!(sample_ids(&sent), [0, 1]);

    drop(keys);
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 10).await;
    let expected: Vec<(u64, u32)> = (0..10)
        .map(|frame| (frame, [0, 0, 0, 1, 1, 1, 2, 2, 2, 2][frame as usize]))
        .collect();
    assert_eq!(cells(&sent, 0), expected, "every frame once, as decided");
    assert_eq!(sample_ids(&sent), [0, 1, 2]);
    assert!(sent.evicts.is_empty());
    assert!(sent.readies.iter().all(|ready| ready.data_revision == 1));
    assert_eq!(sent.readies[0].ingest, Ingest::Reading);
    let last = sent.readies.last().expect("a status");
    assert_eq!((&last.ingest, last.samples), (&Ingest::Complete, 3));
}

#[tokio::test]
async fn a_timestamp_keyed_file_read_by_frame_decides_frames_by_record_position_not_by_time() {
    let (_env, mut viewer, keys) = viewer_over(&PACED_TIMED);
    let mut sent = Sent::default();

    // Records 0 and 1 are in; only record 0 is final, so only frame 0 is decided, however far the recording's own
    // times are from the video's.
    keys.send(0).expect("sent");
    keys.send(1).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| !cells(sent, 0).is_empty()).await;
    assert_eq!(cells(&sent, 0), [(0, 0)]);

    keys.send(2).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 2).await;
    assert_eq!(cells(&sent, 0), [(0, 0), (1, 1)]);

    drop(keys);
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 10).await;
    let expected: Vec<(u64, u32)> = (0..10).map(|frame| (frame, frame.min(2) as u32)).collect();
    assert_eq!(cells(&sent, 0), expected, "every frame once, as decided");
}

#[tokio::test]
async fn renumbered_samples_start_a_new_revision_that_its_ready_precedes_in_full() {
    let (_env, mut viewer, keys) = viewer_over_paced_file();
    let mut sent = Sent::default();
    keys.send(0).expect("sent");
    keys.send(3).expect("sent");
    keys.send(6).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| cells(sent, 0).len() >= 6).await;

    // An inspection admitted now is of the sample numbered 1 under revision 1, the one keyed at frame 3.
    let (track, admitted, _) = viewer.inspect_target(0, 1, 1).expect("the sample was sent");

    // A sample for a frame already decided arrives: the published samples are renumbered when the file ends.
    keys.send(1).expect("sent");
    drop(keys);
    // `(revision, is a Ready)` in the order the viewer was told.
    let mut told: Vec<(u32, bool)> = Vec::new();
    while sent
        .readies
        .last()
        .is_none_or(|r| r.ingest != Ingest::Complete)
    {
        let event = next(&mut viewer).await;
        match &event {
            OverlayEvent::Status {
                status: LaneStatus::Ready(ready),
                ..
            } => told.push((ready.data_revision, true)),
            OverlayEvent::Map(run) => told.push((run.data_revision, false)),
            OverlayEvent::Sample(item) => told.push((item.data_revision, false)),
            _ => {}
        }
        sent.record(event);
    }
    settle_into(&mut viewer, &mut sent).await;

    let last = sent.readies.last().expect("a status");
    assert_eq!((last.data_revision, last.samples), (2, 4));
    // The new revision's Ready is followed by one with the alignment.
    assert!(
        sent.readies
            .iter()
            .all(|ready| (ready.data_revision == 1) == (ready.ingest == Ingest::Reading))
    );
    let new_ready = told
        .iter()
        .position(|&told| told == (2, true))
        .expect("a Ready");
    assert!(
        told[..new_ready].iter().all(|&(revision, _)| revision == 1),
        "no data of the new revision before its Ready"
    );
    assert!(
        told[new_ready..].iter().all(|&(revision, _)| revision == 2),
        "no data of the old numbering after it"
    );
    let mut renumbered = vec![u32::MAX; 10];
    for run in sent.maps.iter().filter(|run| run.data_revision == 2) {
        for (at, &cell) in run.samples.iter().enumerate() {
            renumbered[run.first as usize + at] = cell;
        }
    }
    assert_eq!(renumbered, [0, 1, 1, 2, 2, 2, 3, 3, 3, 3]);
    let key_of = |scene: Arc<Scene>| scene.time().start();
    assert_eq!(
        key_of(track.scene_in(&admitted, 1).expect("reads")),
        SceneTime::Frame(3),
        "the admitted snapshot still reads the sample it admitted"
    );
    assert_eq!(
        key_of(track.scene(1).expect("reads")),
        SceneTime::Frame(1),
        "the track's own sample 1 is another one now"
    );
    let mut samples: Vec<u64> = sent
        .samples
        .iter()
        .filter(|item| item.data_revision == 2)
        .map(|item| item.sample)
        .collect();
    samples.sort_unstable();
    assert_eq!(samples, [0, 1, 2, 3]);
}

#[tokio::test]
async fn a_lane_offers_its_file_scope_once_the_ingest_has_ended_and_under_its_current_revisions() {
    let (env, mut viewer, keys) = viewer_over_paced_file();
    let mut sent = Sent::default();
    keys.send(0).expect("sent");
    keys.send(3).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| !sent.readies.is_empty()).await;
    assert!(matches!(
        viewer.file_scope(0),
        Err(FileScopeBlocked::Reading)
    ));

    drop(keys);
    record_until(&mut viewer, &mut sent, |sent| {
        sent.readies
            .last()
            .is_some_and(|ready| ready.ingest == Ingest::Complete)
    })
    .await;
    let input = viewer.file_scope(0).expect("offered once complete");
    let ready = sent.readies.last().expect("a status");
    assert_eq!(
        (input.data_revision, input.policy_revision),
        (ready.data_revision, 1)
    );

    let plain = env.open(vec![None], 10);
    assert!(matches!(plain.file_scope(0), Err(FileScopeBlocked::Plain)));
    assert!(matches!(viewer.file_scope(1), Err(FileScopeBlocked::Plain)));
}

/// Gives each timestamp the test sends, in microseconds, as a record, and ends when the sender is dropped.
struct Ticks {
    ticks: Receiver<i64>,
    text: String,
}

impl Input for Ticks {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        loop {
            cancel.check()?;
            match self.ticks.recv_timeout(Duration::from_millis(20)) {
                Ok(time) => {
                    self.text = time.to_string();
                    return Ok(Some(Record {
                        bytes: self.text.as_bytes(),
                        position: 1,
                        capture_time: None,
                    }));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => return Ok(None),
            }
        }
    }
}

/// A one-box Scene at each record's timestamp.
struct Stamps;

impl Decoder for Stamps {
    fn spec(&self) -> &Spec {
        &PACED_TIMED
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        while let Some(record) = input.next(cancel)? {
            let time: i64 = std::str::from_utf8(record.bytes)
                .expect("text")
                .parse()
                .expect("microseconds");
            let mut entity = Entity::new(EntityId::from(1_u64));
            let time = SceneTime::Timestamp(MediaTime(time));
            entity.push_observation(Observation::new(
                time,
                Geometry::Box(BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box")),
            ));
            let scene = Scene::new(TimeSlice::at(time))
                .with_entities(vec![entity])
                .expect("one clock");
            let _ = sink.sample(scene, OnEqualKey::Merge);
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: policy(),
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// One session, then no more.
struct OneSession(Option<Ticks>);

impl Feed for OneSession {
    fn next_input(&mut self, _cancel: &CancelToken) -> Option<Box<dyn Input + Send>> {
        Some(Box::new(self.0.take()?))
    }
}

/// Opens the same live track whatever the overlay.
struct Live(Arc<LiveTrack>);

impl TrackSource for Live {
    fn open(
        &self,
        _overlay: &OverlayRef,
        _admission: Admission,
        _cancel: &CancelToken,
    ) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>> {
        Ok(self.0.clone())
    }
}

#[tokio::test]
async fn lanes_of_a_live_track_fill_without_aligning_or_restarting_their_source() {
    let (ticks, receiver) = channel();
    let feed = OneSession(Some(Ticks {
        ticks: receiver,
        text: String::new(),
    }));
    let values = PACED_TIMED
        .options
        .validate(&Settings::new())
        .expect("no options");
    let track = LiveTrack::start(Arc::new(Stamps), values, Box::new(feed), 100).expect("starts");
    let overlay = overlay_at("live", "LIVE", PathBuf::from("live"));
    let mut viewer = OverlayService::from_sources(Arc::new(Live(track))).open_viewer(
        vec![Some(overlay.clone()), Some(overlay)],
        timeline(10),
        SAMPLE_BUDGET,
        Admission::Compute,
    );
    viewer.want(want(0, 0, 9));
    for lane in 0..2 {
        viewer.set_policy(lane, 1, Some(MatchPolicy::default()), true);
    }
    let rate = FrameRate::new(30, 1).expect("rate");
    let at = |frame| rate.time_of(frame).expect("time").0;
    let mut sent = Sent::default();

    // Samples at frames 0 and 3 decide frames 0 to 2. The first announcement of a growing source comes at most every
    // 500 ms, so the third waits for it.
    ticks.send(at(0)).expect("sent");
    ticks.send(at(3)).expect("sent");
    tokio::time::sleep(Duration::from_millis(520)).await;
    ticks.send(at(6)).expect("sent");
    record_until(&mut viewer, &mut sent, |sent| {
        (0..2).all(|lane| cells(sent, lane).len() >= 6)
            && sent
                .readies
                .last()
                .is_some_and(|ready| ready.mean_sample_bytes > 0)
    })
    .await;

    // Each sample is a new publication of the same numbering: one revision, no alignment, a source still reading.
    assert_eq!(cells(&sent, 0), cells(&sent, 1));
    assert!(sent.readies.iter().all(|ready| {
        ready.data_revision == 1 && ready.alignment.is_none() && ready.ingest == Ingest::Reading
    }));
    assert!(matches!(
        viewer.file_scope(0),
        Err(FileScopeBlocked::Reading)
    ));
}
