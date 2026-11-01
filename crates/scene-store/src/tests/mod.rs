//! Tests of the store through a decoder that counts its runs.

mod behaviour;
mod reuse;

use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

use foundation::{CancelToken, FrameSize, Interrupt, MediaTime};
use playlist_resolvers::{OverlayRef, Settings};
use scene::{
    BoundingBox, ClassType, Classification, Coverage, Entity, EntityId, FilterPolicy, Geometry,
    Observation, Scene, SceneTime, TimeSlice, Value,
};
use scene_decoders::{
    DecodeContext, DecodeError, DecodeSummary, DecoderSpec, FileSceneDecoder, LoadWarning, Storage,
};
use tempfile::TempDir;

use crate::{Track, TrackRegistry};

pub(super) static SPEC: DecoderSpec = DecoderSpec {
    plugin_id: "test",
    handler: "TEST",
    display_name: "Test",
    extensions: &[".txt"],
    artifact_version: 1,
    storage: Storage::DerivedCache,
};

pub(super) static SPEC_V2: DecoderSpec = DecoderSpec {
    artifact_version: 2,
    ..SPEC
};

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

/// A file decoder that counts its runs. Box positions depend on the frame size, so a rebuild is visible in the data.
pub(super) struct Counting {
    pub spec: &'static DecoderSpec,
    pub shape: Shape,
    pub runs: AtomicUsize,
    pub hook: Mutex<Option<Hook>>,
}

impl Counting {
    pub(super) fn new(shape: Shape) -> Arc<Self> {
        Arc::new(Self {
            spec: &SPEC,
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

fn entities(time: SceneTime, boxes: usize, width: f64) -> Scene {
    let list = (0..boxes)
        .map(|n| {
            let left = n as f64 / 1000.0 + width / 100_000.0;
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

impl FileSceneDecoder for Counting {
    fn spec(&self) -> &'static DecoderSpec {
        self.spec
    }

    fn decode_file(
        &self,
        input: &[u8],
        context: &DecodeContext<'_>,
        emit: &mut dyn FnMut(Scene),
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        if input.starts_with(b"broken") {
            return Err(Interrupt::Failed(DecodeError(
                "line 1: not a test file".into(),
            )));
        }
        self.runs.fetch_add(1, Ordering::SeqCst);
        let width = f64::from(context.frame_size.width);
        let step = |n: u64, scene: Scene, emit: &mut dyn FnMut(Scene)| {
            if let Some(hook) = &*self.hook.lock().expect("hook lock") {
                hook(n, cancel);
            }
            cancel.check()?;
            emit(scene);
            Ok::<_, Interrupt<DecodeError>>(())
        };
        match self.shape {
            Shape::Frames { frames, boxes } => {
                for frame in 0..frames {
                    step(frame, entities(SceneTime::Frame(frame), boxes, width), emit)?;
                }
            }
            Shape::Mixed => {
                step(0, entities(SceneTime::Frame(0), 1, width), emit)?;
                step(
                    1,
                    entities(SceneTime::Timestamp(MediaTime(40_000)), 1, width),
                    emit,
                )?;
            }
            Shape::Shuffled => {
                for (n, us) in [3_000_u64, 1_000, 2_000, 1_000].into_iter().enumerate() {
                    let boxes = n + 1;
                    let time = SceneTime::Timestamp(MediaTime(us as i64));
                    step(n as u64, entities(time, boxes, width), emit)?;
                }
            }
        }
        Ok(DecodeSummary {
            warnings: vec![LoadWarning {
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

pub(super) const SIZE: FrameSize = FrameSize {
    width: 640,
    height: 480,
};

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
        TrackRegistry::with_decoders(
            move |handler| {
                (handler == "TEST").then(|| decoder.clone() as Arc<dyn FileSceneDecoder>)
            },
            cache,
        )
    }

    pub(super) fn cache(&self) -> PathBuf {
        self.dir.path().join("cache")
    }

    pub(super) fn overlay(&self) -> OverlayRef {
        OverlayRef {
            label: "lane".into(),
            path: self.source.clone(),
            handler: "TEST".into(),
            options: Settings::new(),
        }
    }

    pub(super) fn open(&self) -> Arc<Track> {
        self.open_sized(SIZE)
    }

    pub(super) fn open_sized(&self, size: FrameSize) -> Arc<Track> {
        match self
            .registry
            .open(&self.overlay(), size, &CancelToken::new())
        {
            Ok(track) => track,
            Err(Interrupt::Failed(error)) => panic!("open failed: {error}"),
            Err(Interrupt::Cancelled) => panic!("open was cancelled"),
        }
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
