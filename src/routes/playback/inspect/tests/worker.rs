//! Held worker and socket delivery regressions for a capability's lifetime.
use super::*;
use foundation::UserError;
use handler_api::Form;
use overlay_sync::MatchBasis;
use scene::{Coverage, Fields, FilterPolicy};
use scene_store::{History, HistoryUnavailable};
use std::sync::{Weak, atomic::Ordering};

pub(super) struct HeldRead {
    inner: Arc<dyn TrackRead>,
    pub(super) started: tokio::sync::Notify,
    release: Mutex<Option<std::sync::mpsc::Receiver<()>>>,
    reads: std::sync::atomic::AtomicUsize,
}
impl HeldRead {
    pub(super) fn new(
        inner: Arc<dyn TrackRead>,
        release: Option<std::sync::mpsc::Receiver<()>>,
    ) -> Self {
        Self {
            inner,
            started: Default::default(),
            release: Mutex::new(release),
            reads: Default::default(),
        }
    }
}
impl TrackRead for HeldRead {
    fn bases(&self) -> &[MatchBasis] {
        self.inner.bases()
    }
    fn default_basis(&self) -> MatchBasis {
        self.inner.default_basis()
    }
    fn options(&self) -> &Form {
        self.inner.options()
    }
    fn snapshot(&self) -> Arc<TrackSnapshot> {
        self.inner.snapshot()
    }
    fn on_change(&self, listener: Weak<dyn Fn() + Send + Sync>) {
        self.inner.on_change(listener);
    }
    fn history(&self) -> Result<Arc<History>, HistoryUnavailable> {
        self.inner.history()
    }
    fn coverage(&self) -> Coverage {
        self.inner.coverage()
    }
    fn filter(&self) -> &FilterPolicy {
        self.inner.filter()
    }
    fn metadata(&self) -> &Fields {
        self.inner.metadata()
    }
    fn scene_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<Scene>, UserError> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        if let Some(release) = self.release.lock().unwrap().take() {
            self.started.notify_one();
            release
                .recv_timeout(Duration::from_secs(20))
                .expect("held Scene read released in time");
        }
        self.inner.scene_in(snapshot, index)
    }
    fn item_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<[u8]>, UserError> {
        self.inner.item_in(snapshot, index)
    }
}

#[test]
fn released_work_neither_starts_nor_delivers_and_a_fresh_inspection_works() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(bounded("whole released-inspection sequence", async {
        let (_folder, viewer, revision) = super::pins::pin_viewer().await;
        let (input, snapshot, frame) = viewer.inspect_target(0, revision, 10).unwrap();
        let (_authority, receiver) = watch::channel(Authority::default());
        let gate = Gate::new(receiver, || 0);
        gate.open(timeline());
        let mut owner = Inspections::default();
        // The first read is already decoding when its capability is replaced. Its echoed generation must not
        // deliver an Unavailable reply either: the same validity handle owns initial and continuation results.
        let (release, waiting) = std::sync::mpsc::channel();
        let release = ReleaseOnDrop(release);
        let track = Arc::new(HeldRead::new(input.clone(), Some(waiting)));
        owner.ask_target(
            101,
            0,
            revision,
            10,
            "cvat_track_7".into(),
            Some((track.clone(), snapshot.clone(), frame)),
        );
        bounded("initial read started", track.started.notified()).await;
        let first = owner.cursor.as_ref().unwrap().0.clone();
        owner.ask_target(
            101,
            0,
            revision,
            10,
            "cvat_track_7".into(),
            Some((track.clone(), snapshot.clone(), frame)),
        );
        assert_eq!(
            owner.cursor.as_ref().unwrap().0,
            first,
            "a retransmission keeps the admitted capability"
        );
        assert!(
            owner.objects.queued.is_none(),
            "a retransmission does not queue another decode"
        );
        owner.ask(&viewer, 102, 0, revision, 10, "cvat_track_7".into());
        drop(release);
        let old = bounded("superseded initial result", owner.next()).await;
        let fresh = bounded("replacement initial result", owner.next()).await;
        let delivered = deliver(gate.clone(), vec![old, fresh]).await;
        assert_eq!(
            delivered.len(),
            1,
            "superseded initial replies are dropped by capability validity"
        );
        assert!(matches!(
            delivered[0],
            ServerMessage::Inspected {
                request: 102,
                inspection: InspectionWire::Found { .. },
                ..
            }
        ));

        for field in [None, Some(0)] {
            // Make a real page/chunk on the worker, then hold its completed result before it can be handed to
            // the socket. No production scheduling hook is needed: this test owns its module's worker queue.
            let (_, source, revision, sample, entity, position, _) =
                owner.cursor.as_ref().unwrap().clone();
            let prepared = position.lock().unwrap().traversal.clone().unwrap();
            let scene = position.lock().unwrap().scene.clone().unwrap();
            let page = prepared.page(scene.entity(&entity).unwrap(), 0, &[]);
            let read = InspectionRead {
                request: 201,
                token: owner.cursor.as_ref().unwrap().0.clone(),
                field: field.map(|_| {
                    page.fields
                        .iter()
                        .enumerate()
                        .find(|(_, f)| f.cut.is_some())
                        .unwrap()
                        .0 as u32
                }),
                raw: None,
                pins: None,
                path: false,
                offset: 0,
            };
            drop(scene);
            drop(prepared);
            let request = Request {
                validity: position::validity(&position),
                generation: read.request,
                source,
                data_revision: revision,
                sample,
                ask: Ask::Continue(entity, read.clone(), position),
            };
            let (started, ready) = tokio::sync::oneshot::channel();
            let (release, waiting) = std::sync::mpsc::channel();
            let release = ReleaseOnDrop(release);
            let job = Job {
                request: request.clone(),
                target: viewer.inspect_target(source, revision, u64::from(sample)),
            };
            let is_chunk = read.field.is_some();
            let running = tokio::task::spawn_blocking(move || {
                let outgoing = super::super::answer(job);
                let Message::Text(body) = &outgoing.message else {
                    panic!("text result");
                };
                let message: ServerMessage = serde_json::from_str(body).unwrap();
                assert!(matches!(
                    message,
                    ServerMessage::InspectContinued {
                        result: InspectionReadResult::Page { .. }
                            | InspectionReadResult::Chunk { .. },
                        ..
                    }
                ));
                assert_eq!(
                    matches!(
                        message,
                        ServerMessage::InspectContinued {
                            result: InspectionReadResult::Chunk { .. },
                            ..
                        }
                    ),
                    is_chunk
                );
                started.send(()).unwrap();
                waiting
                    .recv_timeout(Duration::from_secs(20))
                    .expect("held continuation released");
                outgoing
            });
            owner.objects.running = Some((request, running));
            bounded("page/chunk produced on worker", ready)
                .await
                .unwrap();
            let queued = Arc::new(HeldRead::new(input.clone(), None));
            owner.continue_target(
                InspectionRead {
                    request: 202,
                    ..read
                },
                Some((queued.clone(), snapshot.clone(), frame)),
            );
            assert!(owner.objects.queued.is_some());
            owner.release();
            assert!(
                owner.objects.queued.is_none(),
                "release immediately drops its queued continuation"
            );
            drop(release);
            let outgoing = bounded("released continuation finishes", owner.next()).await;
            assert!(
                owner.objects.running.is_none(),
                "the queued continuation never starts"
            );
            assert_eq!(
                queued.reads.load(Ordering::SeqCst),
                0,
                "queued continuation must not decode"
            );
            owner.ask(&viewer, 203, 0, revision, 10, "cvat_track_7".into());
            let fresh = bounded("fresh inspection after release", owner.next()).await;
            let delivered = deliver(gate.clone(), vec![outgoing, fresh]).await;
            assert_eq!(
                delivered.len(),
                1,
                "released page/chunk results never reach the socket"
            );
            assert!(matches!(
                delivered[0],
                ServerMessage::Inspected {
                    request: 203,
                    inspection: InspectionWire::Found { .. },
                    ..
                }
            ));
        }
        // Admission can precede actual blocking-pool execution. Cancel while the sole worker is occupied:
        // the already-submitted first job must check validity before any Scene decode when it finally starts.
        let (started, ready) = tokio::sync::oneshot::channel();
        let (release, waiting) = std::sync::mpsc::channel();
        let release = ReleaseOnDrop(release);
        let occupied = tokio::task::spawn_blocking(move || {
            started.send(()).unwrap();
            waiting
                .recv_timeout(Duration::from_secs(20))
                .expect("occupied worker released");
        });
        bounded("blocking pool occupied", ready).await.unwrap();
        let track = Arc::new(HeldRead::new(input, None));
        owner.ask_target(
            301,
            0,
            revision,
            10,
            "cvat_track_7".into(),
            Some((track.clone(), snapshot, frame)),
        );
        owner.release();
        drop(release);
        bounded("occupied worker finished", occupied).await.unwrap();
        let outgoing = bounded("cancelled-before-start job finishes", owner.next()).await;
        assert_eq!(
            track.reads.load(Ordering::SeqCst),
            0,
            "cancelled work must not start a Scene decode"
        );
        assert!(deliver(gate, vec![outgoing]).await.is_empty());
    }));
}

pub(super) async fn deliver(gate: Gate, outgoing: Vec<Outgoing>) -> Vec<ServerMessage> {
    use futures_util::sink;
    let delivered = Arc::new(Mutex::new(Vec::new()));
    let socket = Box::pin(sink::unfold(
        delivered.clone(),
        |delivered, message| async move {
            delivered.lock().unwrap().push(message);
            Ok::<_, ()>(delivered)
        },
    ));
    let (outbox, writer) = crate::routes::playback::spawn_writer(socket, gate);
    for message in outgoing {
        bounded("inspection delivery queued", outbox.send(message))
            .await
            .unwrap();
    }
    drop(outbox);
    bounded("inspection writer finished", writer).await.unwrap();
    delivered
        .lock()
        .unwrap()
        .iter()
        .map(|message| {
            let Message::Text(body) = message else {
                panic!("inspection text");
            };
            serde_json::from_str(body).unwrap()
        })
        .collect()
}
