//! Joining, publishing while reading, out-of-order keys and failures, through a decoder that follows a script.

use std::{
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

use handler_api::Header;
use scene::Geometry;

use super::*;
use crate::{Decided, Ingest, ingest::Tuning};

/// One Scene to give: entities as `(id, x)`, each with one observation at `time`.
#[derive(Clone)]
struct Step {
    time: SceneTime,
    entities: Vec<(u64, u32)>,
    on_equal_key: OnEqualKey,
}

fn step(time: u64, entities: &[(u64, u32)], on_equal_key: OnEqualKey) -> Step {
    Step {
        time: SceneTime::Frame(time),
        entities: entities.to_vec(),
        on_equal_key,
    }
}

type Gate = Box<dyn Fn(usize, &CancelToken) + Send + Sync>;

/// A decoder that gives the scripted Scenes, optionally a header first and a failure last.
struct Script {
    steps: Vec<Step>,
    header: bool,
    fail: Option<&'static str>,
    code: Option<[u8; 32]>,
    spec: Spec,
    gate: Mutex<Option<Gate>>,
    runs: AtomicUsize,
}

impl Script {
    fn new(steps: Vec<Step>) -> Self {
        Self {
            steps,
            header: false,
            fail: None,
            code: None,
            spec: spec(1),
            gate: Mutex::new(None),
            runs: AtomicUsize::new(0),
        }
    }

    fn runs(&self) -> usize {
        self.runs.load(Ordering::SeqCst)
    }

    /// Runs `gate(step index, cancel)` before each Scene is given.
    fn before_step(&self, gate: impl Fn(usize, &CancelToken) + Send + Sync + 'static) {
        *self.gate.lock().expect("gate lock") = Some(Box::new(gate));
    }
}

fn policy() -> FilterPolicy {
    FilterPolicy {
        name: "script",
        options: Vec::new(),
    }
}

fn scene_of(step: &Step) -> Scene {
    let entities = step
        .entities
        .iter()
        .map(|&(id, x)| {
            let left = f64::from(x) / 1000.0;
            let mut entity = Entity::new(EntityId::from(id));
            entity.push_observation(Observation::new(
                step.time,
                Geometry::Box(BoundingBox::new(left, 0.25, left + 0.1, 0.5).expect("a box")),
            ));
            entity
        })
        .collect();
    Scene::new(TimeSlice::at(step.time))
        .with_entities(entities)
        .expect("one clock")
}

impl Decoder for Script {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn code_hash(&self) -> Option<[u8; 32]> {
        self.code
    }

    fn decode(
        &self,
        _input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        self.runs.fetch_add(1, Ordering::SeqCst);
        if self.header {
            sink.header(Header {
                filter: policy(),
                coverage: Coverage::Sparse,
                metadata: Vec::new(),
            });
        }
        for (n, step) in self.steps.iter().enumerate() {
            if let Some(gate) = &*self.gate.lock().expect("gate lock") {
                gate(n, cancel);
            }
            cancel.check()?;
            // A plugin that is told to stop answers as if it had been cancelled.
            if sink.sample(scene_of(step), step.on_equal_key).is_break() {
                return Err(Interrupt::Cancelled);
            }
        }
        if let Some(reason) = self.fail {
            return Err(Interrupt::Failed(DecodeError(reason.into())));
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: policy(),
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

struct Rig {
    dir: TempDir,
    decoder: Arc<Script>,
    registry: TrackRegistry,
}

impl Rig {
    fn new(decoder: Script, tuning: Tuning) -> Self {
        let dir = tempfile::tempdir().expect("temp dir");
        fs::write(dir.path().join("overlay.txt"), b"rows\n").expect("source");
        let decoder = Arc::new(decoder);
        let registry = Self::registry(&decoder, &dir, tuning);
        Self {
            dir,
            decoder,
            registry,
        }
    }

    fn registry(decoder: &Arc<Script>, dir: &TempDir, tuning: Tuning) -> TrackRegistry {
        let decoder = decoder.clone();
        TrackRegistry::new(
            move |handler| (handler == "TEST").then(|| decoder.clone() as Arc<dyn Decoder>),
            Some(&dir.path().join("cache")),
        )
        .with_tuning(tuning)
    }

    fn restart(&mut self, tuning: Tuning) {
        self.registry = Self::registry(&self.decoder, &self.dir, tuning);
    }

    fn overlay(&self) -> OverlayRef {
        OverlayRef {
            label: "lane".into(),
            slot: "lane".into(),
            path: self.dir.path().join("overlay.txt"),
            handler: "TEST".into(),
            options: Settings::new(),
        }
    }

    fn open(&self) -> Result<Arc<Track>, Interrupt<foundation::UserError>> {
        self.registry.open(&self.overlay(), &CancelToken::new())
    }

    /// The stored artifacts; a file still being written is not one.
    fn artifacts(&self) -> usize {
        let Ok(entries) = fs::read_dir(self.dir.path().join("cache").join("scenes")) else {
            return 0;
        };
        entries
            .filter(|entry| {
                let name = entry.as_ref().expect("entry").file_name();
                !name.to_string_lossy().ends_with(".tmp")
            })
            .count()
    }
}

/// Publishes after every group, so a test sees each step of the watermark.
fn eager() -> Tuning {
    Tuning {
        publish_every: Duration::ZERO,
        ..Tuning::default()
    }
}

/// Waits until `track` reports an ingest that is not reading.
fn finished(track: &Track) -> Arc<crate::TrackSnapshot> {
    let (changed, signal) = mpsc::channel();
    let listener: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
        let _ = changed.send(());
    });
    track.on_change(Arc::downgrade(&listener));
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let snapshot = track.snapshot();
        if snapshot.ingest != Ingest::Reading {
            return snapshot;
        }
        assert!(Instant::now() < deadline, "the ingest never ended");
        let _ = signal.recv_timeout(Duration::from_millis(50));
    }
}

/// `(entity id, x of each observation)` of every entity of sample `index`.
fn contents(track: &Track, index: usize) -> Vec<(String, Vec<u32>)> {
    track
        .scene(index)
        .expect("scene")
        .entities()
        .iter()
        .map(|entity| {
            let xs = entity
                .observations()
                .iter()
                .map(|o| match &o.geometry {
                    Geometry::Box(b) => (b.left() * 1000.0).round() as u32,
                    _ => panic!("a box"),
                })
                .collect();
            (entity.id().as_str().to_string(), xs)
        })
        .collect()
}

fn pair(id: &str, xs: &[u32]) -> (String, Vec<u32>) {
    (id.to_string(), xs.to_vec())
}

#[test]
fn fragments_of_one_key_join_and_a_replace_drops_what_the_key_held() {
    use OnEqualKey::{Merge, Replace};
    let rig = Rig::new(
        Script::new(vec![
            step(0, &[(1, 10), (2, 20)], Merge),
            step(0, &[(1, 11), (3, 30)], Merge),
            step(1, &[(1, 12)], Merge),
            step(1, &[(9, 90)], Replace),
            step(2, &[(4, 40)], Replace),
        ]),
        Tuning::default(),
    );

    let track = rig.open().expect("opens");

    assert_eq!(track.snapshot().keys.len(), 3);
    assert_eq!(
        contents(&track, 0),
        [pair("1", &[10, 11]), pair("2", &[20]), pair("3", &[30])],
        "entities in order of first appearance, an id seen again joins its observations"
    );
    assert_eq!(contents(&track, 1), [pair("9", &[90])]);
    let warnings = track.warnings();
    assert_eq!(warnings.len(), 1, "one replace dropped data: {warnings:?}");
    assert!(warnings[0].problem.starts_with("1 samples were replaced"));
}

#[test]
fn a_header_lets_the_track_be_read_while_the_decode_runs() {
    let (reached, signal) = mpsc::channel();
    let (release, released) = mpsc::channel::<()>();
    let (reached, released) = (Mutex::new(reached), Mutex::new(released));
    let mut script = Script::new(
        (0..6)
            .map(|n| step(n, &[(1, n as u32)], OnEqualKey::Merge))
            .collect(),
    );
    script.header = true;
    let mut rig = Rig::new(script, eager());
    rig.decoder.before_step(move |n, _| {
        if n == 3 {
            reached.lock().expect("lock").send(()).expect("sent");
            released.lock().expect("lock").recv().expect("released");
        }
    });

    let track = rig.open().expect("opens once a prefix is published");
    signal
        .recv_timeout(Duration::from_secs(10))
        .expect("the decode reached step 3");

    let early = track.snapshot();
    assert_eq!(early.keys, frame_keys(&[0, 1]));
    assert_eq!(
        early.decided,
        Decided::Below {
            frame: Some(2),
            time: None
        }
    );
    assert_eq!(early.ingest, Ingest::Reading);
    let frame = |sequence_id| foundation::FrameId {
        sequence_id,
        timestamp: MediaTime(0),
    };
    assert!(early.decides(frame(1), MatchBasis::Frame));
    assert!(!early.decides(frame(2), MatchBasis::Frame));
    let first = track.scene(0).expect("a published sample is readable");
    assert!(track.item(1).is_ok());
    assert!(track.scene(2).is_err(), "an unpublished sample is not");
    assert_eq!(rig.artifacts(), 0, "nothing is stored before the end");

    release.send(()).expect("released");
    let done = finished(&track);

    assert_eq!(done.ingest, Ingest::Complete);
    assert_eq!(done.decided, Decided::All);
    assert_eq!(
        done.generation, 0,
        "ascending keys never renumber the samples"
    );
    assert_eq!(done.keys, frame_keys(&(0..6).collect::<Vec<u64>>()));
    assert_eq!(*track.scene(0).expect("scene"), *first);
    assert_eq!(rig.artifacts(), 1);
    drop(track);
    rig.restart(eager());
    drop(rig.open().expect("reopens"));
    assert_eq!(rig.decoder.runs(), 1, "the stored artifact is reused");
}

/// The expected content of each key after applying `steps` in order: `(entity id, xs)` in first-appearance order.
fn reference(steps: &[Step]) -> std::collections::BTreeMap<u64, Vec<(String, Vec<u32>)>> {
    let mut keys: std::collections::BTreeMap<u64, Vec<(String, Vec<u32>)>> = Default::default();
    for step in steps {
        let SceneTime::Frame(key) = step.time else {
            panic!("frames")
        };
        let held = keys.entry(key).or_default();
        if step.on_equal_key == OnEqualKey::Replace {
            held.clear();
        }
        for &(id, x) in &step.entities {
            match held
                .iter_mut()
                .find(|(held_id, _)| *held_id == id.to_string())
            {
                Some((_, xs)) => xs.push(x),
                None => held.push((id.to_string(), vec![x])),
            }
        }
    }
    keys
}

#[test]
fn keys_out_of_order_renumber_the_samples_once_whatever_the_sort_budget() {
    // After a prefix was published, a fragment of an old key arrives.
    let mut script = Script::new(vec![
        step(0, &[(1, 1)], OnEqualKey::Merge),
        step(1, &[(1, 2)], OnEqualKey::Merge),
        step(2, &[(1, 3)], OnEqualKey::Merge),
        step(3, &[(1, 4)], OnEqualKey::Merge),
        step(1, &[(2, 5)], OnEqualKey::Merge),
    ]);
    script.header = true;
    let (reached, signal) = mpsc::channel();
    let (release, released) = mpsc::channel::<()>();
    let (reached, released) = (Mutex::new(reached), Mutex::new(released));
    let rig = Rig::new(script, eager());
    rig.decoder.before_step(move |n, _| {
        if n == 4 {
            reached.lock().expect("lock").send(()).expect("sent");
            released.lock().expect("lock").recv().expect("released");
        }
    });
    let track = rig.open().expect("opens");
    signal
        .recv_timeout(Duration::from_secs(10))
        .expect("reached");
    let before = track.snapshot();
    let old = track.scene(1).expect("scene");

    release.send(()).expect("released");
    let after = finished(&track);

    assert_eq!(before.generation, 0);
    assert_eq!((after.generation, &after.ingest), (1, &Ingest::Complete));
    assert_eq!(after.keys, frame_keys(&[0, 1, 2, 3]));
    assert_eq!(contents(&track, 1), [pair("1", &[2]), pair("2", &[5])]);
    // A snapshot reads by the numbering it holds, however the track was renumbered since.
    let (was, is) = (
        track.item_in(&before, 1).expect("reads"),
        track.item_in(&after, 1).expect("reads"),
    );
    assert_ne!(was, is, "the old numbering's sample 1 is not the new one");
    assert_eq!(
        *track.scene_in(&before, 1).expect("scene"),
        *old,
        "the old snapshot still reads the sample it was taken with"
    );
    assert_eq!(
        track.scene_in(&after, 1).expect("scene").entities().len(),
        2
    );
    assert_eq!(
        old.entities().len(),
        1,
        "a Scene already handed out is unchanged"
    );

    // Random fragments: the result is what applying them in order would give, with the sorter in memory or on disk.
    let mut state = 12_345_u64;
    let mut random = |below: u64| {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        (state >> 33) % below
    };
    let steps: Vec<Step> = (0..400)
        .map(|n| {
            let entities: Vec<(u64, u32)> =
                (0..=random(2)).map(|_| (random(5), n as u32)).collect();
            let mut unique = entities;
            unique.sort_by_key(|&(id, _)| id);
            unique.dedup_by_key(|&mut (id, _)| id);
            let on_equal_key = if random(6) == 0 {
                OnEqualKey::Replace
            } else {
                OnEqualKey::Merge
            };
            step(random(40), &unique, on_equal_key)
        })
        .collect();
    let expected = reference(&steps);
    for budget in [crate::sort::SORT_BUDGET_BYTES, 1024] {
        let rig = Rig::new(
            Script::new(steps.clone()),
            Tuning {
                sort_budget: budget,
                ..Tuning::default()
            },
        );

        let track = rig.open().expect("opens");

        let keys = track.snapshot().keys.clone();
        assert_eq!(
            keys,
            frame_keys(&expected.keys().copied().collect::<Vec<_>>())
        );
        for (index, held) in expected.values().enumerate() {
            assert_eq!(
                &contents(&track, index),
                held,
                "sample {index}, budget {budget}"
            );
        }
    }
}

#[test]
fn a_decoder_that_fails_after_samples_keeps_them_and_stores_nothing() {
    let steps: Vec<Step> = (0..3)
        .map(|n| step(n, &[(1, n as u32)], OnEqualKey::Merge))
        .collect();
    let mut script = Script::new(steps);
    script.header = true;
    script.code = Some([7; 32]);
    script.fail = Some("The plugin stopped with exit code 3");
    let rig = Rig::new(script, eager());

    let track = rig.open().expect("the rows read are kept");
    let snapshot = finished(&track);

    assert_eq!(snapshot.keys.len(), 3);
    assert_eq!(snapshot.decided, Decided::All);
    let Ingest::Incomplete(error) = &snapshot.ingest else {
        panic!("{:?}", snapshot.ingest)
    };
    assert!(error.reason.contains("exit code 3"), "{error}");
    assert!(track.scene(2).is_ok());
    assert_eq!(rig.artifacts(), 0, "an incomplete artifact is never stored");
    let again = rig.open().expect("opens again");
    assert_eq!(
        rig.decoder.runs(),
        2,
        "the next open decodes again even while another viewer holds the first"
    );
    assert!(!Arc::ptr_eq(&track, &again) && track.scene(2).is_ok());

    // A built-in without a header that fails midway fails the open.
    let mut plain = Script::new(
        (0..3)
            .map(|n| step(n, &[(1, 1)], OnEqualKey::Merge))
            .collect(),
    );
    plain.fail = Some("line 3: not a row");
    let rig = Rig::new(plain, Tuning::default());
    assert!(matches!(rig.open(), Err(Interrupt::Failed(_))));
}

#[test]
fn a_refused_sample_after_some_leaves_the_rows_incomplete_not_reading() {
    let mut steps: Vec<Step> = (0..2)
        .map(|n| step(n, &[(1, 1)], OnEqualKey::Merge))
        .collect();
    steps.push(Step {
        time: SceneTime::Timestamp(MediaTime(80_000)),
        ..step(0, &[(1, 1)], OnEqualKey::Merge)
    });
    let mut script = Script::new(steps);
    script.header = true;
    let rig = Rig::new(script, eager());

    let track = rig.open().expect("the rows read are kept");
    let snapshot = finished(&track);

    assert_eq!(snapshot.keys.len(), 2);
    let Ingest::Incomplete(error) = &snapshot.ingest else {
        panic!("{:?}", snapshot.ingest)
    };
    assert!(error.reason.contains("mixes frame numbers"), "{error}");
}

#[test]
fn the_code_hash_and_the_declared_options_are_part_of_the_cache_identity() {
    let steps = vec![step(0, &[(1, 1)], OnEqualKey::Merge)];
    let mut script = Script::new(steps);
    script.code = Some([1; 32]);
    script.spec.options.fields[0].default = Some("default".into());
    let mut rig = Rig::new(script, Tuning::default());
    drop(rig.open().expect("opens"));
    assert_eq!(rig.decoder.runs(), 1);

    let mut explicit = rig.overlay();
    explicit.options.insert("profile".into(), "default".into());
    drop(
        rig.registry
            .open(&explicit, &CancelToken::new())
            .expect("opens"),
    );
    assert_eq!(
        rig.decoder.runs(),
        1,
        "an explicit default and an omitted option share an artifact"
    );

    // The same decoder with other code is another decode of the same cache file.
    let mut changed = Script::new(vec![step(0, &[(1, 1)], OnEqualKey::Merge)]);
    changed.code = Some([2; 32]);
    changed.spec.options.fields[0].default = Some("default".into());
    rig.decoder = Arc::new(changed);
    rig.restart(Tuning::default());
    drop(rig.open().expect("opens"));
    assert_eq!(rig.decoder.runs(), 1, "edited code rebuilds");
    rig.restart(Tuning::default());
    drop(rig.open().expect("opens"));
    assert_eq!(rig.decoder.runs(), 1, "and the rebuilt artifact is reused");
}

#[test]
fn dropping_every_handle_while_reading_stops_the_decode_and_stores_nothing() {
    let steps: Vec<Step> = (0..4)
        .map(|n| step(n, &[(1, 1)], OnEqualKey::Merge))
        .collect();
    let mut script = Script::new(steps);
    script.header = true;
    let rig = Rig::new(script, eager());
    // Without a cancel the decode would wait here for good.
    rig.decoder.before_step(|n, cancel| {
        while n == 3 && !cancel.is_cancelled() {
            thread::sleep(Duration::from_millis(1));
        }
    });

    drop(rig.open().expect("opens"));

    // The thread ends and removes its temporary file; nothing is stored.
    let scenes = rig.dir.path().join("cache").join("scenes");
    let deadline = Instant::now() + Duration::from_secs(10);
    while fs::read_dir(&scenes).is_ok_and(|mut entries| entries.next().is_some()) {
        assert!(Instant::now() < deadline, "the decode kept running");
        thread::sleep(Duration::from_millis(5));
    }
}
