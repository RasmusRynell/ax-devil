//! The decoder that follows a script and the rig that opens it, for the ingest tests.

use handler_api::Header;
use scene::Geometry;

use super::*;
use crate::ingest::Tuning;

/// One Scene to give: entities as `(id, x)`, each with one observation at `time`.
#[derive(Clone)]
pub(super) struct Step {
    pub(super) time: SceneTime,
    pub(super) entities: Vec<(u64, u32)>,
    pub(super) on_equal_key: OnEqualKey,
}

pub(super) fn step(time: u64, entities: &[(u64, u32)], on_equal_key: OnEqualKey) -> Step {
    Step {
        time: SceneTime::Frame(time as i64),
        entities: entities.to_vec(),
        on_equal_key,
    }
}

type Gate = Box<dyn Fn(usize, &CancelToken) + Send + Sync>;

/// A decoder that gives the scripted Scenes, optionally a header first and a failure last.
pub(super) struct Script {
    steps: Vec<Step>,
    pub(super) header: bool,
    pub(super) fail: Option<&'static str>,
    pub(super) code: Option<[u8; 32]>,
    pub(super) spec: Spec,
    gate: Mutex<Option<Gate>>,
    runs: AtomicUsize,
}

impl Script {
    pub(super) fn new(steps: Vec<Step>) -> Self {
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

    pub(super) fn runs(&self) -> usize {
        self.runs.load(Ordering::SeqCst)
    }

    /// Runs `gate(step index, cancel)` before each Scene is given.
    pub(super) fn before_step(&self, gate: impl Fn(usize, &CancelToken) + Send + Sync + 'static) {
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

pub(super) struct Rig {
    pub(super) dir: TempDir,
    pub(super) decoder: Arc<Script>,
    /// What the registry finds, so a test can edit the decoder under a registry that holds tracks.
    pub(super) live: Arc<Mutex<Arc<Script>>>,
    pub(super) registry: TrackRegistry,
}

impl Rig {
    pub(super) fn new(decoder: Script, tuning: Tuning) -> Self {
        let dir = tempfile::tempdir().expect("temp dir");
        fs::write(dir.path().join("overlay.txt"), b"rows\n").expect("source");
        let decoder = Arc::new(decoder);
        let live = Arc::new(Mutex::new(decoder.clone()));
        let registry = Self::registry(&live, &dir, tuning);
        Self {
            dir,
            decoder,
            live,
            registry,
        }
    }

    fn registry(live: &Arc<Mutex<Arc<Script>>>, dir: &TempDir, tuning: Tuning) -> TrackRegistry {
        let live = live.clone();
        TrackRegistry::new(
            move |handler| (handler == "TEST").then(|| live.lock().expect("live").clone() as _),
            Some(&dir.path().join("cache")),
        )
        .with_tuning(tuning)
    }

    pub(super) fn restart(&mut self, tuning: Tuning) {
        self.registry = Self::registry(&self.live, &self.dir, tuning);
    }

    pub(super) fn overlay(&self) -> OverlayRef {
        OverlayRef {
            label: "lane".into(),
            slot: "lane".into(),
            path: self.dir.path().join("overlay.txt"),
            handler: "TEST".into(),
            options: Settings::new(),
            metadata: Default::default(),
        }
    }

    pub(super) fn open(&self) -> Result<Arc<Track>, Interrupt<foundation::UserError>> {
        self.registry.open(&self.overlay(), &CancelToken::new())
    }

    /// The stored artifacts; a file still being written is not one.
    pub(super) fn artifacts(&self) -> usize {
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
