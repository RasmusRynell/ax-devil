//! The file history through the store: built while ingesting, stored in the artifact, loaded when asked.

use scene::{ClassRule, Event, FilterOption, Operation, OperationKind};

use super::*;
use crate::{
    History, HistoryUnavailable,
    history::{Built, HistoryBuilder},
    ingest::Tuning,
};

/// A Scene at `frame` whose entities are `(id, classes of its one observation)`.
pub(super) fn scene_of(frame: u64, entities: &[(&str, &[&str])]) -> Scene {
    let time = SceneTime::Frame(frame);
    let list = entities
        .iter()
        .map(|(id, classes)| {
            let mut entity = Entity::new(EntityId::new(*id).expect("an id"));
            let mut observation = Observation::new(
                time,
                Geometry::Box(BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box")),
            );
            observation.classifications = classes
                .iter()
                .map(|class| {
                    Classification::new(ClassType::new(class.to_string()).expect("a class"))
                })
                .collect();
            entity.push_observation(observation);
            entity
        })
        .collect();
    Scene::new(TimeSlice::at(time))
        .with_entities(list)
        .expect("one clock")
}

/// A policy with one option per `(id, class)`, all on.
pub(super) fn policy_of(options: &[(&'static str, &'static str)]) -> FilterPolicy {
    FilterPolicy {
        name: "test",
        options: options
            .iter()
            .map(|&(id, class)| FilterOption {
                id: id.into(),
                label: id.into(),
                default_enabled: true,
                rule: ClassRule {
                    types: vec![class.into()],
                    include_empty: false,
                    exclude: false,
                },
            })
            .collect(),
    }
}

/// The history of `scenes` taken as samples 0, 1, ... under `policy`.
pub(super) fn history_of(scenes: &[Scene], policy: &FilterPolicy) -> History {
    let mut builder = HistoryBuilder::new(usize::MAX);
    for scene in scenes {
        builder.add(scene);
    }
    match builder.finish(Some(policy)) {
        Built::Ready(history) => *history,
        Built::TooLarge => panic!("the history fits"),
    }
}

/// `(id, classes, runs)` of every entity that appears.
fn entities(history: &History) -> Vec<(String, Vec<String>, Vec<[u32; 2]>)> {
    (0..history.entities() as u32)
        .map(|e| {
            let classes = history.classes(e).map(str::to_string).collect();
            (history.id(e).to_string(), classes, history.runs(e).to_vec())
        })
        .collect()
}

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| item.to_string()).collect()
}

/// Gives the scripted Scenes, then the summary, or fails after them.
struct Emit {
    scenes: Vec<Scene>,
    policy: FilterPolicy,
    header: bool,
    fail: bool,
    runs: Arc<AtomicUsize>,
}

impl Decoder for Emit {
    fn spec(&self) -> &Spec {
        &SPEC
    }

    fn decode(
        &self,
        _input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        _cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        self.runs.fetch_add(1, Ordering::SeqCst);
        if self.header {
            sink.header(handler_api::Header {
                filter: self.policy.clone(),
                coverage: Coverage::Sparse,
                metadata: Vec::new(),
            });
        }
        for scene in &self.scenes {
            let _ = sink.sample(scene.clone(), OnEqualKey::Replace);
        }
        if self.fail {
            return Err(Interrupt::Failed(DecodeError("line 9: broken".into())));
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: self.policy.clone(),
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

fn registry(dir: &TempDir, emit: Emit, tuning: Tuning) -> TrackRegistry {
    let emit = Arc::new(emit);
    TrackRegistry::new(
        move |handler| (handler == "TEST").then(|| emit.clone() as Arc<dyn Decoder>),
        Some(&dir.path().join("cache")),
    )
    .with_tuning(tuning)
}

fn open(dir: &TempDir, registry: &TrackRegistry) -> Arc<Track> {
    let source = dir.path().join("overlay.txt");
    fs::write(&source, b"rows\n").expect("source");
    let overlay = OverlayRef {
        label: "lane".into(),
        slot: "lane".into(),
        path: source,
        handler: "TEST".into(),
        options: Settings::new(),
        metadata: Default::default(),
    };
    match registry.open(&overlay, &CancelToken::new()) {
        Ok(track) => track,
        Err(_) => panic!("open failed"),
    }
}

fn waited(track: &Track) -> Result<Arc<History>, HistoryUnavailable> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while matches!(track.history(), Err(HistoryUnavailable::Reading)) {
        assert!(
            std::time::Instant::now() < deadline,
            "the ingest never ended"
        );
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    track.history()
}

/// Frames 0 and 1 hold `a` (a car, then a bus); frame 3 holds `a` again and `b` with a rename of `a` to `z`.
pub(super) fn scenes() -> Vec<Scene> {
    let rename = Event::new(
        TimeSlice::at(SceneTime::Frame(3)),
        Operation::Rename {
            from: EntityId::new("a").expect("an id"),
            to: EntityId::new("z").expect("an id"),
        },
    );
    vec![
        scene_of(0, &[("a", &["car"])]),
        scene_of(1, &[("a", &["bus"]), ("b", &[])]),
        scene_of(3, &[("a", &["bus"]), ("b", &[])])
            .with_events(vec![rename])
            .expect("one clock"),
    ]
}

#[test]
fn the_history_records_classes_runs_and_events_per_entity() {
    let history = history_of(&scenes(), &policy_of(&[("show_car", "car")]));

    assert_eq!(
        entities(&history),
        [
            ("a".into(), strings(&["car", "bus"]), vec![[0, 2]]),
            ("b".into(), Vec::new(), vec![[1, 2]]),
            ("z".into(), Vec::new(), Vec::new()),
        ],
        "classes of every sample in order of first appearance; an event-only id has no runs"
    );
    let events: Vec<_> = history.events().collect();
    assert_eq!(events.len(), 1);
    assert_eq!(
        (events[0].sample, events[0].kind, events[0].label),
        (2, OperationKind::Rename, "Rename a → z")
    );
    assert_eq!(events[0].entities, ["a", "z"]);
    assert_eq!(
        history.event_kinds(0).collect::<Vec<_>>(),
        [OperationKind::Rename]
    );
}

#[test]
fn a_run_ends_where_an_entity_misses_a_sample() {
    let scenes = [
        scene_of(0, &[("a", &[])]),
        scene_of(1, &[("b", &[])]),
        scene_of(2, &[("a", &[])]),
        scene_of(3, &[("a", &[])]),
    ];

    let history = history_of(&scenes, &policy_of(&[]));

    assert_eq!(history.runs(0), [[0, 0], [2, 3]]);
    assert_eq!(history.runs(1), [[1, 1]]);
}

#[test]
fn a_finished_track_loads_its_history_from_the_artifact_and_a_reopen_finds_it_again() {
    let dir = tempfile::tempdir().expect("temp dir");
    let policy = policy_of(&[("show_car", "car")]);
    let runs = Arc::new(AtomicUsize::new(0));
    let emit = || Emit {
        scenes: scenes(),
        policy: policy.clone(),
        header: false,
        fail: false,
        runs: runs.clone(),
    };
    let track = open(&dir, &registry(&dir, emit(), Tuning::default()));
    let history = waited(&track).expect("the ingest ended");
    let expected = entities(&history);
    drop((history, track));

    let again = open(&dir, &registry(&dir, emit(), Tuning::default()));

    let loaded = again.history().expect("read from the stored artifact");
    assert_eq!(entities(&loaded), expected);
    assert_eq!(runs.load(Ordering::SeqCst), 1, "the reopen did not decode");
    assert!(
        Arc::ptr_eq(&loaded, &again.history().expect("shared")),
        "callers share one copy while any holds it"
    );
}

#[test]
fn a_history_over_the_cap_is_none_and_the_file_says_why() {
    let dir = tempfile::tempdir().expect("temp dir");
    let policy = policy_of(&[]);
    let emit = || Emit {
        scenes: scenes(),
        policy: policy.clone(),
        header: false,
        fail: false,
        runs: Arc::default(),
    };
    let small = Tuning {
        history_bytes: 64,
        ..Tuning::default()
    };

    let track = open(&dir, &registry(&dir, emit(), small));
    assert_eq!(waited(&track).err(), Some(HistoryUnavailable::TooLarge));
    drop(track);

    let again = open(&dir, &registry(&dir, emit(), Tuning::default()));
    assert_eq!(
        again.history().err(),
        Some(HistoryUnavailable::TooLarge),
        "a stored artifact keeps its verdict"
    );
}

#[test]
fn the_file_masks_count_against_the_cap_before_they_are_allocated() {
    // 40,000 declared options make a 5 kB mask per entity, far more than one tiny entity otherwise costs.
    let options: Vec<(&'static str, &'static str)> = vec![("x", "car"); 40_000];
    let mut builder = HistoryBuilder::new(2_000);
    builder.add(&scene_of(0, &[("a", &["car"])]));

    assert!(matches!(
        builder.finish(Some(&policy_of(&options))),
        Built::TooLarge
    ));
}

#[test]
fn fragments_that_arrived_out_of_order_are_indexed_by_the_final_numbering() {
    let dir = tempfile::tempdir().expect("temp dir");
    let emit = Emit {
        scenes: vec![
            scene_of(2, &[("a", &["car"])]),
            scene_of(0, &[("b", &["bus"])]),
            scene_of(1, &[("a", &["car"])]),
        ],
        policy: policy_of(&[]),
        header: false,
        fail: false,
        runs: Arc::default(),
    };

    let track = open(&dir, &registry(&dir, emit, Tuning::default()));

    let history = waited(&track).expect("the ingest ended");
    assert_eq!(
        entities(&history),
        [
            ("b".into(), strings(&["bus"]), vec![[0, 0]]),
            ("a".into(), strings(&["car"]), vec![[1, 2]]),
        ]
    );
}

#[test]
fn an_incomplete_track_keeps_the_history_of_the_samples_it_has() {
    let dir = tempfile::tempdir().expect("temp dir");
    let emit = Emit {
        scenes: scenes(),
        policy: policy_of(&[]),
        header: true,
        fail: true,
        runs: Arc::default(),
    };

    let track = open(&dir, &registry(&dir, emit, Tuning::default()));

    let history = waited(&track).expect("held in memory");
    assert_eq!(history.samples(), 3);
    assert!(matches!(
        track.snapshot().ingest,
        crate::Ingest::Incomplete(_)
    ));
}
