//! Export on the playback socket through the real routes and a fake renderer: only the owner may export, a request
//! that differs from what the viewer holds is refused before anything starts, and a viewer that leaves cancels its
//! export and takes its temporary file with it.

mod common;

use api_types::playback::{
    CatalogRefWire, ClientMessage, ExportFormatWire, ExportLaneWire, ExportQualityWire,
    ExportStateWire, IngestWire, LaneStatusWire, ServerMessage,
};
use ax_devil::exports::ExportService;
use common::{
    Backend, Viewer, WAIT,
    export_kit::{files, finished, serve_lanes, state_of, until},
    fixture,
};
use export::{LaneStyle, Output, Pixels, RenderJob, RenderSetup, Renderer};
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handlers::Handlers;
use media::{FrameIndex, VideoInput};
use overlay_sync::MatchBasis;
use scene::{
    BoundingBox, Coverage, Entity, EntityId, FilterPolicy, Geometry, Observation, Scene, SceneTime,
    TimeSlice,
};
use scene_decoders::{
    DecodeContext, DecodeError, DecodeSummary, DecoderSpec, FileSceneDecoder, Header, OnEqualKey,
    SceneSink, SourceFile, Storage,
};
use sessions::{
    CursorOptions,
    session::{Access, Command, Control, Reply},
};
use std::{
    collections::VecDeque,
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    },
    time::Duration,
};

static GATE: DecoderSpec = DecoderSpec {
    plugin_id: "test",
    handler: "GATE",
    display_name: "Gate",
    extensions: &[".gate"],
    artifact_version: 1,
    storage: Storage::DerivedCache,
    bases: &[MatchBasis::Frame],
    default_basis: MatchBasis::Frame,
};

/// Reads a `.gate` file as two samples, then keeps reading until the test drops its sender.
struct Gate(Mutex<mpsc::Receiver<()>>);

impl FileSceneDecoder for Gate {
    fn spec(&self) -> &'static DecoderSpec {
        &GATE
    }

    fn decode_file(
        &self,
        _source: &dyn SourceFile,
        _context: &DecodeContext<'_>,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let policy = || FilterPolicy {
            name: "gate",
            options: Vec::new(),
        };
        sink.header(Header {
            filter: policy(),
            coverage: Coverage::Sparse,
            metadata: Vec::new(),
        });
        // The store publishes at most every 250 ms, so the first samples take that long to become readable.
        std::thread::sleep(Duration::from_millis(260));
        for key in 0..2 {
            let time = SceneTime::Frame(key);
            let mut entity = Entity::new(EntityId::from(key));
            let shape = BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box");
            entity.push_observation(Observation::new(time, Geometry::Box(shape)));
            let scene = Scene::new(TimeSlice::at(time))
                .with_entities(vec![entity])
                .expect("one clock");
            let _ = sink.sample(scene, OnEqualKey::Merge);
        }
        let release = self.0.lock().expect("release lock");
        while !matches!(
            release.recv_timeout(Duration::from_millis(20)),
            Err(mpsc::RecvTimeoutError::Disconnected)
        ) {
            cancel.check()?;
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: policy(),
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

const CATALOG: &str = "built-in:standard";
const MP4: ExportFormatWire = ExportFormatWire::Mp4 {
    quality: ExportQualityWire::Small,
};

/// What the test sees of the renderers the service made.
#[derive(Default)]
struct Renders {
    made: AtomicUsize,
    /// The first picture waits until the test lets it go.
    hold: AtomicBool,
    waiting: AtomicBool,
    setups: Mutex<Vec<RenderSetup>>,
}

/// Draws flat grey at the size of the video.
struct Grey {
    renders: Arc<Renders>,
    output: Output,
    submitted: VecDeque<u64>,
}

impl Renderer for Grey {
    fn setup(&mut self, setup: &RenderSetup) -> Result<(u32, u32), UserError> {
        self.output = setup.output;
        self.renders.setups.lock().unwrap().push(setup.clone());
        Ok((setup.picture.width, setup.picture.height))
    }

    fn submit(&mut self, job: RenderJob) -> Result<(), UserError> {
        self.submitted.push_back(job.seq);
        Ok(())
    }

    fn receive(&mut self) -> Result<Pixels, UserError> {
        self.renders.waiting.store(true, Ordering::SeqCst);
        while self.renders.hold.load(Ordering::SeqCst) {
            std::thread::sleep(Duration::from_millis(2));
        }
        let (w, h) = (64, 48);
        Ok(Pixels {
            seq: self.submitted.pop_front().unwrap(),
            output: self.output,
            width: w,
            height: h,
            stride0: w,
            stride1: w / 2,
            data: vec![128; (w * h * 3 / 2) as usize].into(),
        })
    }
}

async fn backend(renders: &Arc<Renders>) -> Backend {
    backend_reading(renders, Handlers::built_in()).await
}

async fn backend_reading(renders: &Arc<Renders>, handlers: Handlers) -> Backend {
    let renders = Arc::clone(renders);
    let exports = ExportService::with_renderer(Arc::new(move |_| {
        renders.made.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(Grey {
            renders: Arc::clone(&renders),
            output: Output::I420,
            submitted: VecDeque::new(),
        }))
    }));
    Backend::start_with(false, handlers, |state| state.exports = Arc::new(exports)).await
}

/// An owner's viewer on the fixture with one subscribed lane and the Standard catalog served: the lane's policy
/// revision, its data revision and the catalog's revision.
async fn holding_a_lane(backend: &Backend) -> (Viewer, ExportLaneWire, common::Added) {
    let gt = backend.mot_file("1,1,10,10,20,20,1,1,1\n2,1,12,10,20,20,1,1,1\n");
    holding_a_lane_over(backend, gt).await
}

async fn holding_a_lane_over(
    backend: &Backend,
    overlay: PathBuf,
) -> (Viewer, ExportLaneWire, common::Added) {
    let added = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[overlay])
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&added.ticket(), None).await;
    viewer.opened().await;
    let [served] = &serve_lanes(&mut viewer, false, &[(0, CATALOG)]).await[..] else {
        unreachable!("one lane")
    };
    let lane = ExportLaneWire {
        lane: 0,
        catalog: CatalogRefWire {
            id: CATALOG.into(),
            revision: served.catalog,
        },
        style: LaneStyle {
            label: "GT".into(),
            enabled_classes: Vec::new(),
            class_options: 0,
            hidden: 0,
            theme: None,
            retained_opacity: 0.5,
            filter: None,
        },
        policy_revision: 1,
        data_revision: Some(served.data),
    };
    (viewer, lane, added)
}

fn export(job: u32, lane: ExportLaneWire, last: u64, path: &Path) -> ClientMessage {
    ClientMessage::Export {
        job,
        lanes: vec![lane],
        first: 0,
        last,
        format: MP4,
        path: path.display().to_string(),
    }
}

#[tokio::test]
async fn an_owner_exports_what_it_holds_and_a_request_that_differs_from_it_is_refused_before_anything_starts()
 {
    let renders = Arc::new(Renders::default());
    let backend = backend(&renders).await;
    let (mut viewer, lane, _added) = holding_a_lane(&backend).await;
    let out = tempfile::tempdir().unwrap();
    let path = out.path().join("clip.mp4");

    let mut stale_policy = lane.clone();
    stale_policy.policy_revision = 2;
    let mut stale_data = lane.clone();
    stale_data.data_revision = stale_data.data_revision.map(|revision| revision + 1);
    let mut other_catalog = lane.clone();
    other_catalog.catalog.revision += 1;
    let mut unserved = lane.clone();
    unserved.catalog.id = "built-in:tracking".into();
    for (job, refused) in [stale_policy, stale_data, other_catalog, unserved]
        .into_iter()
        .enumerate()
    {
        let job = job as u32 + 1;
        viewer.send(&export(job, refused, 2, &path)).await;
        let ExportStateWire::Failed(error) = state_of(&mut viewer, job).await else {
            panic!("job {job} was not refused");
        };
        assert_eq!(error.code, ErrorCode::ProtocolViolation, "job {job}");
    }
    assert_eq!(renders.made.load(Ordering::SeqCst), 0, "nothing started");

    viewer.send(&export(5, lane, 2, &path)).await;
    let (progress, ended) = finished(&mut viewer, 5).await;
    assert_eq!(ended, ExportStateWire::Done);
    assert!(progress.windows(2).all(|pair| pair[0] <= pair[1]));
    assert_eq!(files(out.path()), ["clip.mp4"]);
    let index = FrameIndex::load_or_build(
        &VideoInput::File(path),
        None,
        &CancelToken::new(),
        &mut |_| {},
    )
    .unwrap();
    assert_eq!(index.timeline().len(), 3, "frames 0 to 2");
    let setups = renders.setups.lock().unwrap();
    let [setup] = &setups[..] else {
        panic!("one export, one setup");
    };
    assert_eq!(setup.lanes[0].source, Some(0), "the lane draws its file");
    assert!(
        setup.lanes[0].catalog.contains("\"layers\""),
        "the served text"
    );
}

#[tokio::test]
async fn an_export_waits_for_a_lane_that_is_still_being_read_to_finish() {
    let (release, released) = mpsc::channel::<()>();
    let gate = Handlers::built_in().with_file_decoders([Arc::new(Gate(Mutex::new(released))) as _]);
    let renders = Arc::new(Renders::default());
    let backend = backend_reading(&renders, gate).await;
    let slow = backend.folder.path().join("slow.gate");
    fs::write(&slow, "rows\n").unwrap();
    let (mut viewer, lane, _added) = holding_a_lane_over(&backend, slow).await;
    let out = tempfile::tempdir().unwrap();
    let path = out.path().join("clip.mp4");

    viewer.send(&export(1, lane.clone(), 2, &path)).await;
    let ExportStateWire::Failed(error) = state_of(&mut viewer, 1).await else {
        panic!("a lane still being read must refuse the export");
    };
    assert!(error.reason.unwrap().contains("still being read"));
    assert_eq!(renders.made.load(Ordering::SeqCst), 0, "nothing started");

    drop(release);
    until(&mut viewer, |message| match message {
        ServerMessage::LaneStatus {
            status: LaneStatusWire::Ready { ingest, .. },
            ..
        } => (ingest == IngestWire::Complete).then_some(()),
        _ => None,
    })
    .await;
    viewer.send(&export(2, lane, 2, &path)).await;
    assert_eq!(finished(&mut viewer, 2).await.1, ExportStateWire::Done);
}

#[tokio::test]
async fn a_watcher_may_not_export() {
    let renders = Arc::new(Renders::default());
    let backend = backend(&renders).await;
    let (_owner, lane, added) = holding_a_lane(&backend).await;
    added
        .owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    let link = added.owner.state().sharing.as_ref().unwrap().link.clone();
    let seat = backend
        .state
        .sessions
        .join(&link, CursorOptions::default())
        .unwrap();
    let Ok(Reply::Ticket(ticket)) = seat.command(Command::RequestTicket(added.key.clone())) else {
        panic!("a watcher may stream what the owner has open");
    };
    let mut watcher = backend.viewer().await;
    watcher.open(&ticket.to_string(), None).await;
    watcher.opened().await;
    let out = tempfile::tempdir().unwrap();

    watcher
        .send(&export(1, lane, 2, &out.path().join("clip.mp4")))
        .await;

    let ExportStateWire::Failed(error) = state_of(&mut watcher, 1).await else {
        panic!("the watcher's export must be refused");
    };
    assert_eq!(error.code, ErrorCode::ProtocolViolation);
    assert_eq!(error.reason, None, "and not told the host's details");
    assert_eq!(renders.made.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn closing_the_socket_cancels_the_export_and_removes_its_temporary_file() {
    let renders = Arc::new(Renders::default());
    renders.hold.store(true, Ordering::SeqCst);
    let backend = backend(&renders).await;
    let (mut viewer, lane, _added) = holding_a_lane(&backend).await;
    let out = tempfile::tempdir().unwrap();
    viewer
        .send(&export(1, lane, 40, &out.path().join("clip.mp4")))
        .await;
    tokio::time::timeout(WAIT, async {
        while !renders.waiting.load(Ordering::SeqCst) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("the export reaches its first picture");
    assert_eq!(files(out.path()).len(), 1, "the temporary file exists");

    drop(viewer);
    // The viewer's decode threads end after its export was cancelled, so the picture may go now.
    backend.wait_for_workers(0).await;
    renders.hold.store(false, Ordering::SeqCst);

    tokio::time::timeout(WAIT, async {
        while !files(out.path()).is_empty() {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("the temporary file goes and no output appears");
}
