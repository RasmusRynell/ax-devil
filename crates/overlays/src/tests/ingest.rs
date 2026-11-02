//! A track still being read: the lane sends the frames its samples have decided, once, and no others.

use std::{
    sync::{
        Arc, Mutex,
        mpsc::{Receiver, RecvTimeoutError, Sender, channel},
    },
    thread,
    time::Duration,
};

use foundation::{CancelToken, Interrupt};
use overlay_sync::{MatchPolicy, Sticky};
use playlist_resolvers::OverlayRef;
use scene::{
    BoundingBox, Coverage, Entity, EntityId, FilterPolicy, Geometry, Observation, Scene, SceneTime,
    TimeSlice,
};
use scene_decoders::{
    DecodeContext, DecodeError, DecodeSummary, DecoderSpec, FileSceneDecoder, Header, OnEqualKey,
    SceneSink, SourceFile, Storage,
};
use scene_store::Ingest;

use super::{Env, Sent, cells, next, sample_ids, settle_into, want};
use crate::{FileScopeBlocked, LaneStatus, OverlayEvent, ViewerOverlays};

static PACED: DecoderSpec = DecoderSpec {
    plugin_id: "test",
    handler: "PACED",
    display_name: "Paced",
    extensions: &[".paced"],
    artifact_version: 1,
    storage: Storage::DerivedCache,
};

/// The store publishes at most every 250 ms, so the decoder gives each Scene that long after the last.
const PUBLISH_GAP: Duration = Duration::from_millis(260);

/// A decoder that gives one frame-keyed Scene for each key the test sends, and ends when the sender is dropped.
struct Paced(Mutex<Receiver<u64>>);

fn scene_at(key: u64) -> Scene {
    let time = SceneTime::Frame(key);
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

impl FileSceneDecoder for Paced {
    fn spec(&self) -> &'static DecoderSpec {
        &PACED
    }

    fn decode_file(
        &self,
        _source: &dyn SourceFile,
        _context: &DecodeContext<'_>,
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
                    if sink.sample(scene_at(key), OnEqualKey::Merge).is_break() {
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
    let (keys, receiver) = channel();
    let env = Env::with_decoder(Arc::new(Paced(Mutex::new(receiver))));
    let overlay: OverlayRef = env.overlay("a.paced", "PACED", "rows\n");
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
    let reading = sent.readies.len() - 1;
    assert!(
        sent.readies[..reading]
            .iter()
            .all(|ready| ready.data_revision == 1 && ready.ingest == Ingest::Reading)
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
