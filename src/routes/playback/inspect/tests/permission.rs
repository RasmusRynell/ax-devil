//! Admission and delivery after a following seat loses object-detail permission.
use super::*;
use foundation::UserError;
use handler_api::Form;
use overlay_sync::MatchBasis;
use scene::{Coverage, Fields, FilterPolicy};
use scene_store::{History, HistoryUnavailable};
use std::sync::{
    Weak,
    atomic::{AtomicBool, Ordering},
};

#[tokio::test]
async fn withdrawn_details_deny_every_inspection_request_even_with_a_valid_capability() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("fields.xml");
    let extra = (0..5000)
        .map(|i| format!("<field{i}>{i}</field{i}>"))
        .collect::<String>();
    let boxes = (0..90).map(|frame| format!("<box frame=\"{frame}\" outside=\"0\" xtl=\"10\" ytl=\"10\" xbr=\"30\" ybr=\"30\">{}</box>", if frame == 10 { format!("<extra><value>{}é😀</value>{extra}</extra>", "x".repeat(128 * 1024)) } else { String::new() })).collect::<String>();
    std::fs::write(&path, format!("<annotations><meta><task><size>90</size><mode>interpolation</mode><original_size><width>64</width><height>48</height></original_size></task></meta><track id=\"7\" label=\"Car\">{boxes}</track></annotations>")).unwrap();
    let overlay = OverlayRef {
        label: "Fields".into(),
        slot: "Fields".into(),
        path,
        handler: "CVAT".into(),
        options: handler_api::Settings::new(),
        metadata: Default::default(),
    };
    let (mut viewer, revision) = open_overlay(overlay).await;
    hold(&mut viewer, 10).await;
    let allowed = Arc::new(AtomicBool::new(true));
    let mut inspections = Inspections::new({
        let allowed = allowed.clone();
        Arc::new(move || allowed.load(Ordering::SeqCst))
    });
    inspections.ask(&viewer, 0, revision, 10, "cvat_track_7".into());
    let InspectionWire::Found {
        continuation: Some(cursor),
        fields,
        ..
    } = verdict(answer(&mut inspections).await.0)
    else {
        panic!("capability");
    };
    let ordinal = fields
        .iter()
        .find_map(|field| field.cut)
        .expect("a copyable oversized value")[0] as u32;
    let next_page = cursor.next.expect("more than one field page");
    inspections.continue_target(
        InspectionRead {
            pins: None,
            request: 0,
            token: cursor.token.clone(),
            field: Some(ordinal),
            raw: None,
            path: false,
            offset: 0,
        },
        viewer.inspect_target(0, revision, 10),
    );
    let ServerMessage::InspectContinued {
        result:
            InspectionReadResult::Chunk {
                next: Some(next_chunk),
                ..
            },
        ..
    } = answer(&mut inspections).await.0
    else {
        panic!("first value chunk");
    };
    let wanted = vec![
        api_types::inspect::InspectionPin {
            group: scene::inspection::FieldGroup::Debug,
            identity: None,
            path: ["cvat", "shape", "extra", "field4999"]
                .map(String::from)
                .into(),
        },
        api_types::inspect::InspectionPin {
            group: scene::inspection::FieldGroup::Debug,
            identity: None,
            path: ["cvat", "shape", "extra", "value"].map(String::from).into(),
        },
    ];
    let mut offset = 0;
    let mut pinned = Vec::new();
    loop {
        inspections.continue_target(
            InspectionRead {
                pins: Some(wanted.clone()),
                request: 2,
                token: cursor.token.clone(),
                field: None,
                raw: None,
                path: false,
                offset,
            },
            viewer.inspect_target(0, revision, 10),
        );
        let ServerMessage::InspectContinued {
            result: InspectionReadResult::Pins { fields, next },
            ..
        } = answer(&mut inspections).await.0
        else {
            panic!("pins");
        };
        pinned.extend(fields);
        if let Some(next) = next {
            offset = next;
        } else {
            break;
        }
    }
    assert_eq!(
        pinned
            .iter()
            .find(|(index, _)| *index == 0)
            .unwrap()
            .1
            .value,
        scene::inspection::Leaf::Text("4999".into())
    );
    assert_eq!(
        pinned
            .iter()
            .find(|(index, _)| *index == 1)
            .unwrap()
            .1
            .cut
            .unwrap()[0],
        u64::from(ordinal)
    );
    inspections.continue_target(
        InspectionRead {
            pins: None,
            request: 3,
            token: cursor.token.clone(),
            field: None,
            raw: None,
            path: false,
            offset: next_page,
        },
        viewer.inspect_target(0, revision, 10),
    );
    assert!(matches!(
        answer(&mut inspections).await.0,
        ServerMessage::InspectContinued {
            result: InspectionReadResult::Page { .. },
            ..
        }
    ));
    inspections.continue_target(
        InspectionRead {
            pins: None,
            request: 4,
            token: cursor.token.clone(),
            field: Some(ordinal),
            raw: None,
            path: false,
            offset: next_chunk,
        },
        viewer.inspect_target(0, revision, 10),
    );
    assert!(
        matches!(
            answer(&mut inspections).await.0,
            ServerMessage::InspectContinued {
                result: InspectionReadResult::Chunk { .. },
                ..
            }
        ),
        "a pinned value remains copyable outside the visible page"
    );
    allowed.store(false, Ordering::SeqCst);
    inspections.ask(&viewer, 0, revision, 10, "cvat_track_7".into());
    assert!(
        inspections.objects.running.is_none(),
        "denied initial inspection starts no work"
    );
    inspections.ask_fields(&viewer, 0, revision, 10, vec!["raw.frame_number".into()]);
    assert!(
        inspections.searches.running.is_none(),
        "denied query starts no work"
    );
    for (field, raw, pins, offset) in [
        (None, None, None, next_page),
        (Some(ordinal), None, None, next_chunk),
        (None, Some(true), None, 0),
        (None, None, Some(wanted), 0),
    ] {
        inspections.continue_target(
            InspectionRead {
                pins,
                request: 1,
                token: cursor.token.clone(),
                field,
                raw,
                path: false,
                offset,
            },
            viewer.inspect_target(0, revision, 10),
        );
        assert!(
            inspections.objects.running.is_none(),
            "denied pages, value chunks and Raw chunks start no work"
        );
    }
}

struct HeldRead {
    inner: Arc<dyn TrackRead>,
    started: tokio::sync::Notify,
    release: Mutex<std::sync::mpsc::Receiver<()>>,
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
        self.started.notify_one();
        self.release.lock().unwrap().recv().unwrap();
        self.inner.scene_in(snapshot, index)
    }
    fn item_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<[u8]>, UserError> {
        self.inner.item_in(snapshot, index)
    }
}

#[tokio::test]
async fn a_blocking_inspection_finishing_after_permission_withdrawal_is_not_delivered() {
    use futures_util::sink;
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    hold(&mut viewer, 10).await;
    let (track, snapshot, frame) = viewer.inspect_target(0, revision, 10).unwrap();
    let (release, held) = std::sync::mpsc::channel();
    let track = Arc::new(HeldRead {
        inner: track,
        started: tokio::sync::Notify::new(),
        release: Mutex::new(held),
    });
    let allowed = Arc::new(AtomicBool::new(true));
    let (_holder, authority) = watch::channel(Authority::Follow {
        intent: ClockIntent {
            frame: 10,
            playing: false,
            speed_centi: 100,
            at_us: 0,
        },
    });
    let gate = Gate::new(authority, || 0).with_inspection({
        let allowed = allowed.clone();
        Arc::new(move || allowed.load(Ordering::SeqCst))
    });
    gate.open(timeline());
    assert!(gate.admits(&(frame..=frame)));
    let mut inspections = Inspections::new(gate.inspection_permission());
    inspections.ask_target(
        0,
        revision,
        10,
        "2".into(),
        Some((track.clone(), snapshot, frame)),
    );
    track.started.notified().await;
    allowed.store(false, Ordering::SeqCst);
    release.send(()).unwrap();
    let outgoing = inspections.next().await;
    let delivered = Arc::new(Mutex::new(Vec::new()));
    let socket = Box::pin(sink::unfold(
        delivered.clone(),
        |delivered, message| async move {
            delivered.lock().unwrap().push(message);
            Ok::<_, ()>(delivered)
        },
    ));
    let (outbox, writer) = crate::routes::playback::spawn_writer(socket, gate);
    outbox.send(outgoing).await.unwrap();
    drop(outbox);
    writer.await.unwrap();
    assert!(delivered.lock().unwrap().is_empty());
}
