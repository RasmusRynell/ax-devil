//! Tests of the store through a decoder that counts its runs.

mod behaviour;
mod framing;
mod history;
mod ingest;
mod live;
mod query;
mod reuse;

use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, LazyLock, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

use foundation::{CancelToken, Interrupt, MediaTime, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Field, FieldKind, Form, Framing, Input, OnEqualKey,
    OverlayRef, SceneSink, Settings, Spec, Values,
};
use overlay_sync::{FrameKey, KeyChunks, SampleKeys};
use scene::{
    BoundingBox, ClassType, Classification, Coverage, Entity, EntityId, FilterPolicy, Geometry,
    MatchBasis, Observation, Scene, SceneTime, TimeSlice, Value,
};
use tempfile::TempDir;

use crate::{Track, TrackRead, TrackRegistry};

/// The spec of a decoder that is never changed.
pub(super) static SPEC: LazyLock<Spec> = LazyLock::new(|| spec(1));

/// The test decoder's spec at `version`; it declares one text option, `profile`.
pub(super) fn spec(version: u32) -> Spec {
    Spec {
        id: "TEST".into(),
        plugin: "test".into(),
        name: "Test".into(),
        extensions: vec![".txt".into()],
        framing: Framing::Whole,
        bases: vec![MatchBasis::Frame, MatchBasis::Time],
        default_basis: MatchBasis::Frame,
        options: Form {
            fields: vec![Field::new("profile", "Profile", FieldKind::Text)],
        },
        version,
    }
}

/// Frame keys whose samples are numbered by them.
pub(super) fn in_order(frames: &[u64]) -> KeyChunks<FrameKey> {
    frames
        .iter()
        .enumerate()
        .map(|(sample, &frame)| FrameKey {
            frame,
            sample: sample as u32,
        })
        .collect()
}

/// The keys of a frame-keyed file.
pub(super) fn frame_keys(keys: &[u64]) -> SampleKeys {
    SampleKeys {
        frames: Some(in_order(keys)),
        times: None,
    }
}

/// What a [`Counting`] decoder emits.
#[derive(Clone, Copy)]
pub(super) enum Shape {
    /// `frames` frame-keyed Scenes of `boxes` entities each.
    Frames { frames: u64, boxes: usize },
    /// One frame Scene and then a timestamp Scene.
    Mixed,
    /// Timestamp Scenes out of order, one key twice.
    Shuffled,
}

type Hook = Box<dyn Fn(u64, &CancelToken) + Send + Sync>;

/// A file decoder that counts its runs.
pub(super) struct Counting {
    pub spec: Spec,
    pub shape: Shape,
    pub runs: AtomicUsize,
    pub hook: Mutex<Option<Hook>>,
}

impl Counting {
    pub(super) fn new(shape: Shape) -> Arc<Self> {
        Arc::new(Self {
            spec: spec(1),
            shape,
            runs: AtomicUsize::new(0),
            hook: Mutex::new(None),
        })
    }

    pub(super) fn runs(&self) -> usize {
        self.runs.load(Ordering::SeqCst)
    }

    /// Runs `hook(frame, cancel)` before each Scene is emitted.
    pub(super) fn on_scene(&self, hook: impl Fn(u64, &CancelToken) + Send + Sync + 'static) {
        *self.hook.lock().expect("hook lock") = Some(Box::new(hook));
    }
}

fn entities(time: SceneTime, boxes: usize) -> Scene {
    let list = (0..boxes)
        .map(|n| {
            let left = n as f64 / 1000.0;
            let mut entity = Entity::new(EntityId::from(n as u64));
            let mut observation = Observation::new(
                time,
                Geometry::Box(BoundingBox::new(left, 0.25, left + 0.125, 0.5).expect("a box")),
            );
            observation.classifications = vec![Classification {
                attributes: vec![
                    ("class_id".into(), Value::Int(1)),
                    ("source".into(), Value::Str("test".into())),
                ],
                ..Classification::new(ClassType::new("person").expect("a class"))
            }];
            entity.push_observation(observation);
            entity
        })
        .collect();
    Scene::new(TimeSlice::at(time))
        .with_entities(list)
        .expect("one clock")
}

impl Decoder for Counting {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        if input
            .next(cancel)?
            .is_some_and(|record| record.bytes.starts_with(b"broken"))
        {
            return Err(Interrupt::Failed(DecodeError(
                "line 1: not a test file".into(),
            )));
        }
        self.runs.fetch_add(1, Ordering::SeqCst);
        let step = |n: u64, scene: Scene, sink: &mut dyn SceneSink| {
            if let Some(hook) = &*self.hook.lock().expect("hook lock") {
                hook(n, cancel);
            }
            cancel.check()?;
            let _ = sink.sample(scene, OnEqualKey::Replace);
            Ok::<_, Interrupt<DecodeError>>(())
        };
        match self.shape {
            Shape::Frames { frames, boxes } => {
                for frame in 0..frames {
                    step(frame, entities(SceneTime::Frame(frame), boxes), sink)?;
                }
            }
            Shape::Mixed => {
                step(0, entities(SceneTime::Frame(0), 1), sink)?;
                step(
                    1,
                    entities(SceneTime::Timestamp(MediaTime(40_000)), 1),
                    sink,
                )?;
            }
            Shape::Shuffled => {
                for (n, us) in [3_000_u64, 1_000, 2_000, 1_000].into_iter().enumerate() {
                    let boxes = n + 1;
                    let time = SceneTime::Timestamp(MediaTime(us as i64));
                    step(n as u64, entities(time, boxes), sink)?;
                }
            }
        }
        Ok(DecodeSummary {
            warnings: vec![Warning {
                problem: "Skipped 1 of 3 rows".into(),
                reasons: vec!["line 2: bad".into()],
                consequence: "They are not shown.".into(),
                more_reasons: false,
            }],
            filter: FilterPolicy {
                name: "test",
                options: Vec::new(),
            },
            metadata: vec![("rows".into(), Value::Int(3))],
            coverage: Coverage::Dense,
        })
    }
}

/// A source file, a cache folder and a registry over one decoder.
pub(super) struct Fixture {
    pub dir: TempDir,
    pub source: PathBuf,
    pub decoder: Arc<Counting>,
    pub registry: TrackRegistry,
}

impl Fixture {
    pub(super) fn new(shape: Shape) -> Self {
        Self::with_decoder(Counting::new(shape))
    }

    pub(super) fn with_decoder(decoder: Arc<Counting>) -> Self {
        let dir = tempfile::tempdir().expect("temp dir");
        let source = dir.path().join("overlay.txt");
        fs::write(&source, b"rows rows rows\n").expect("source");
        let registry = Self::registry_over(&decoder, Some(&dir.path().join("cache")));
        Self {
            dir,
            source,
            decoder,
            registry,
        }
    }

    /// A registry like a restarted app's: same decoder, same cache folder, nothing open.
    pub(super) fn restart(&mut self) {
        self.registry = Self::registry_over(&self.decoder, Some(&self.cache()));
    }

    pub(super) fn registry_over(decoder: &Arc<Counting>, cache: Option<&Path>) -> TrackRegistry {
        let decoder = decoder.clone();
        TrackRegistry::new(
            move |handler| (handler == "TEST").then(|| decoder.clone() as Arc<dyn Decoder>),
            cache,
        )
    }

    pub(super) fn cache(&self) -> PathBuf {
        self.dir.path().join("cache")
    }

    pub(super) fn overlay(&self) -> OverlayRef {
        OverlayRef {
            label: "lane".into(),
            slot: "lane".into(),
            path: self.source.clone(),
            handler: "TEST".into(),
            options: Settings::new(),
        }
    }

    pub(super) fn open(&self) -> Arc<Track> {
        match self.registry.open(&self.overlay(), &CancelToken::new()) {
            Ok(track) => track,
            Err(Interrupt::Failed(error)) => panic!("open failed: {error}"),
            Err(Interrupt::Cancelled) => panic!("open was cancelled"),
        }
    }

    /// Whether `error` is about the fixture's overlay file.
    pub(super) fn is_about_source(&self, error: &foundation::UserError) -> bool {
        let name = self.source.file_name();
        matches!(&error.location, Some(foundation::Location::Host(path)) if path.as_path().file_name() == name)
    }

    /// The artifact files in the cache folder.
    pub(super) fn artifacts(&self) -> Vec<PathBuf> {
        let Ok(entries) = fs::read_dir(self.cache().join("scenes")) else {
            return Vec::new();
        };
        let mut files: Vec<_> = entries.map(|e| e.expect("entry").path()).collect();
        files.sort();
        files
    }
}
