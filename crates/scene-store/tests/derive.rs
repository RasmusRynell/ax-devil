//! Admission, and the identity of derived lanes.

use std::{
    fs,
    ops::ControlFlow,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use foundation::{CancelToken, Interrupt};
use handler_api::{
    Bundle, DecodeError, DecodeSummary, Decoder, Form, Framing, Input, LaneSource, OnEqualKey,
    OverlayRef, SceneSink, Settings, Spec, Values,
};
use scene::{Coverage, FilterPolicy, MatchBasis, Scene, SceneTime, TimeSlice};
use scene_store::{Admission, TrackRead, TrackRegistry};

/// A decoder that counts its runs. It emits one Scene per line of its file, or per sample of the first lane of a
/// bundle when it is derived, and with `hold` set waits until it is cancelled.
struct Counting {
    spec: Spec,
    code: Option<[u8; 32]>,
    derived: bool,
    hold: AtomicBool,
    runs: AtomicUsize,
}

impl Counting {
    fn new(id: &str, code: Option<[u8; 32]>, derived: bool) -> Arc<Self> {
        Arc::new(Self {
            spec: Spec {
                id: id.into(),
                plugin: "test".into(),
                name: id.into(),
                description: None,
                extensions: Vec::new(),
                framing: Framing::Whole,
                bases: vec![MatchBasis::Frame],
                default_basis: MatchBasis::Frame,
                options: Form { fields: Vec::new() },
                version: 1,
            },
            code,
            derived,
            hold: AtomicBool::new(false),
            runs: AtomicUsize::new(0),
        })
    }

    fn runs(&self) -> usize {
        self.runs.load(Ordering::SeqCst)
    }
}

impl Decoder for Counting {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn code_hash(&self) -> Option<[u8; 32]> {
        self.code
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        self.runs.fetch_add(1, Ordering::SeqCst);
        while self.hold.load(Ordering::SeqCst) {
            cancel.check()?;
            thread::sleep(Duration::from_millis(5));
        }
        let lines = if self.derived {
            let path = input.path().expect("a bundle file");
            let bundle = Bundle::read(path).expect("a bundle");
            let lane = bundle.lanes.values().next().expect("a lane");
            let text = fs::read_to_string(&lane.path).expect("a lane file");
            text.lines().count() - 1
        } else {
            let record = input.next(cancel)?.expect("the file");
            record.bytes.iter().filter(|byte| **byte == b'\n').count()
        };
        for frame in 0..lines {
            let scene = Scene::new(TimeSlice::at(SceneTime::Frame(frame as i64)));
            if let ControlFlow::Break(()) = sink.sample(scene, OnEqualKey::Replace) {
                break;
            }
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "test",
                options: Vec::new(),
            },
            metadata: Vec::new(),
            coverage: Coverage::Dense,
        })
    }
}

fn registry(decoders: &[Arc<Counting>], cache: &Path) -> TrackRegistry {
    let decoders = decoders.to_vec();
    TrackRegistry::new(
        move |id| {
            decoders
                .iter()
                .find(|decoder| decoder.spec.id == id)
                .map(|decoder| decoder.clone() as Arc<dyn Decoder>)
        },
        Some(cache),
    )
}

fn overlay(handler: &str, source: LaneSource) -> OverlayRef {
    OverlayRef {
        label: handler.into(),
        slot: handler.into(),
        source,
        handler: handler.into(),
        options: Settings::new(),
        metadata: Settings::new(),
    }
}

fn file(handler: &str, path: &Path) -> OverlayRef {
    overlay(handler, LaneSource::File(path.to_path_buf()))
}

/// The number of files in `folder` of the cache.
fn stored(cache: &Path, folder: &str) -> usize {
    fs::read_dir(cache.join(folder)).map_or(0, |entries| entries.filter_map(Result::ok).count())
}

#[test]
fn a_guest_never_runs_plugin_code_to_build_a_lane() {
    let dir = tempfile::tempdir().expect("temp dir");
    let (source, cache) = (dir.path().join("lane.txt"), dir.path().join("cache"));
    fs::write(&source, "a\nb\n").expect("source");
    let plugin = Counting::new("PLUGIN", Some([7; 32]), false);
    let registry = registry(std::slice::from_ref(&plugin), &cache);
    let lane = file("PLUGIN", &source);
    let cancel = CancelToken::new();

    let unbuilt = registry.open_as(&lane, Admission::Reuse, &cancel);
    assert!(matches!(&unbuilt, Err(Interrupt::Failed(error)) if error.reason == "Not built yet"));
    assert_eq!(plugin.runs(), 0, "nothing was decoded");

    drop(
        registry
            .open_as(&lane, Admission::Compute, &cancel)
            .expect("computes"),
    );
    assert_eq!(plugin.runs(), 1);
    let reused = registry
        .open_as(&lane, Admission::Reuse, &cancel)
        .expect("reuses");
    assert_eq!((reused.snapshot().keys.len(), plugin.runs()), (2, 1));
    drop(reused);

    // A Reuse open that meets a Compute open abandoned before it finished does not start the decode itself.
    let other = dir.path().join("other.txt");
    fs::write(&other, "a\n").expect("source");
    let held = file("PLUGIN", &other);
    plugin.hold.store(true, Ordering::SeqCst);
    let registry = Arc::new(registry);
    let abandoned = CancelToken::new();
    let computing = {
        let (registry, held, abandoned) = (registry.clone(), held.clone(), abandoned.clone());
        thread::spawn(move || {
            registry
                .open_as(&held, Admission::Compute, &abandoned)
                .map(drop)
        })
    };
    let started = Instant::now();
    while plugin.runs() < 2 {
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "the decode started"
        );
        thread::sleep(Duration::from_millis(5));
    }
    let waiting = {
        let (registry, held) = (registry.clone(), held.clone());
        thread::spawn(move || {
            registry
                .open_as(&held, Admission::Reuse, &CancelToken::new())
                .map(drop)
        })
    };
    thread::sleep(Duration::from_millis(100));
    abandoned.cancel();
    assert!(matches!(
        computing.join().expect("joined"),
        Err(Interrupt::Cancelled)
    ));
    let waited = waiting.join().expect("joined");
    assert!(matches!(waited, Err(Interrupt::Failed(error)) if error.reason == "Not built yet"));
    assert_eq!(plugin.runs(), 2, "only the abandoned open decoded");
}

#[test]
fn a_derived_lane_rebuilds_when_an_input_changes_and_is_reused_when_it_does_not() {
    let dir = tempfile::tempdir().expect("temp dir");
    let (input, cache) = (dir.path().join("input.txt"), dir.path().join("cache"));
    fs::write(&input, "a\nb\n").expect("input");
    let base = Counting::new("BASE", None, false);
    let derived = Counting::new("DERIVED", None, true);
    let registry = registry(&[base.clone(), derived.clone()], &cache);
    let lane = overlay(
        "DERIVED",
        LaneSource::Derived {
            lanes: vec![("input".into(), file("BASE", &input))],
            video: None,
        },
    );
    let open = || {
        let track = registry
            .open_as(&lane, Admission::Compute, &CancelToken::new())
            .expect("opens");
        while matches!(track.snapshot().ingest, scene_store::Ingest::Reading) {
            thread::sleep(Duration::from_millis(5));
        }
        track.snapshot().keys.len()
    };

    assert_eq!((open(), derived.runs()), (2, 1));
    assert_eq!((open(), derived.runs()), (2, 1), "the stored lane is used");
    assert_eq!((stored(&cache, "lanes"), stored(&cache, "derived")), (1, 1));

    fs::write(&input, "a\nb\nc\n").expect("input");
    assert_eq!(
        (open(), derived.runs()),
        (3, 2),
        "the changed input rebuilt it"
    );
    assert_eq!((stored(&cache, "lanes"), stored(&cache, "derived")), (2, 2));
}
