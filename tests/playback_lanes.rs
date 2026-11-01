//! A playback socket's lanes against the committed fixture video and overlay files written by each test, through
//! the real routes, decoders and catalogs: what a lane reports, the overlays it sends for the view they were made for,
//! what a view that cannot be applied does, and what a watcher may learn from them.

mod common;

use api_types::playback::{
    ClientMessage, Direction, LaneInfo, LaneStatusWire, LaneViewWire, ServerMessage, StickyWire,
    TargetWire,
};
use common::{Backend, Event, Overlay, Viewer, fixture};
use sessions::{
    CursorOptions,
    session::{Access, Command, Control, Reply},
};
use std::collections::BTreeMap;

fn lane_view(catalog: &str, revision: u32, width: u32) -> ClientMessage {
    ClientMessage::LaneView {
        lane: 0,
        revision,
        view: LaneViewWire {
            catalog: catalog.into(),
            filter: BTreeMap::new(),
            tolerance_ms: None,
            sticky: StickyWire {
                enabled: false,
                timeout_ms: None,
                opacity: 1.0,
            },
            target: TargetWire {
                width,
                height: width * 3 / 4,
            },
            visible: true,
        },
    }
}

/// Reads until the lane complains, skipping frames and overlays.
async fn lane_problem(viewer: &mut Viewer) -> String {
    loop {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneProblem { lane: 0, reason }) => return reason,
            Event::Frame(_) | Event::Overlay(_) => {}
            Event::Message(ServerMessage::LaneStatus { .. }) => {}
            _ => panic!("expected a LaneProblem"),
        }
    }
}

/// Reads until the lane is ready and every frame of 0..=5 has an overlay made for `revision`.
async fn collect(
    viewer: &mut Viewer,
    revision: u32,
    ready: &mut Option<LaneStatusWire>,
) -> BTreeMap<u64, Overlay> {
    let mut overlays = BTreeMap::new();
    while ready.is_none() || overlays.len() < 6 {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                lane: 0,
                status: status @ LaneStatusWire::Ready { .. },
            }) => *ready = Some(status),
            Event::Message(ServerMessage::LaneStatus { .. }) | Event::Frame(_) => {}
            Event::Overlay(overlay) if overlay.revision == revision => {
                overlays.insert(overlay.sequence_id, overlay);
            }
            Event::Overlay(_) => {}
            _ => panic!("expected the lane's status, overlays and frames"),
        }
    }
    overlays
}

#[tokio::test]
async fn a_lane_view_naming_an_unknown_catalog_is_reported_and_the_session_goes_on() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    assert_eq!(video.lanes.len(), 1);

    viewer.send(&lane_view("built-in:nope", 1, 640)).await;
    let reason = lane_problem(&mut viewer).await;
    assert!(reason.contains("built-in:nope"), "{reason}");

    viewer.want(40, 3, 0, Direction::Forward).await;
    viewer.frames(40..=43).await;
}

#[tokio::test]
async fn a_lane_reports_its_load_then_sends_an_overlay_per_frame_for_the_view_it_was_made_for() {
    let backend = Backend::start().await;
    let gt = backend.folder.path().join("gt.txt");
    // Video frames 0 to 2 have a box; the file is dense, so the others are covered with nothing.
    std::fs::write(
        &gt,
        "1,1,10,10,20,20,1,1,1\n2,1,12,10,20,20,1,1,1\n3,1,14,10,20,20,1,1,1\n",
    )
    .unwrap();
    let id = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[gt])
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    let lane = LaneInfo {
        label: "gt.txt".into(),
        has_overlay: true,
    };
    assert_eq!(video.lanes, [lane]);

    let mut ready = None;
    viewer.want(0, 5, 0, Direction::Forward).await;
    let overlays = collect(&mut viewer, 0, &mut ready).await;

    let Some(LaneStatusWire::Ready {
        samples, filter, ..
    }) = ready.clone()
    else {
        panic!("the lane is ready");
    };
    assert_eq!(samples, 3);
    assert!(filter.iter().any(|option| option.id == "show_person"));
    for (frame, overlay) in &overlays {
        assert_eq!((overlay.lane, overlay.target), (0, (1280, 720)));
        assert_eq!(overlay.timestamp_us as f64, video.times[*frame as usize]);
        assert_eq!(
            overlay.matched,
            if *frame < 3 { 1 } else { 4 },
            "frame {frame}"
        );
        assert_eq!(overlay.opacity, 1.0);
    }
    assert!(
        overlays[&4].sections.is_empty(),
        "a covered frame with nothing draws nothing"
    );

    // A new view resends the window made for it.
    viewer.send(&lane_view("built-in:tracking", 3, 800)).await;
    let resent = collect(&mut viewer, 3, &mut ready).await;
    assert!(resent.values().all(|overlay| overlay.target == (800, 600)));

    // A catalog the library lacks is reported by the lane's overlays; the lane stays ready.
    viewer.send(&lane_view("user:missing.json", 4, 800)).await;
    assert!(lane_problem(&mut viewer).await.contains("missing.json"));
}

/// Every lane status `viewer` is sent until each of `lanes` lanes has one that is not `Loading`, as JSON text.
async fn settled_lanes(viewer: &mut Viewer, lanes: u16) -> BTreeMap<u16, String> {
    let mut settled = BTreeMap::new();
    while settled.len() < usize::from(lanes) {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                status: LaneStatusWire::Loading,
                ..
            })
            | Event::Frame(_)
            | Event::Overlay(_) => {}
            Event::Message(message @ ServerMessage::LaneStatus { lane, .. }) => {
                settled.insert(lane, serde_json::to_string(&message).unwrap());
            }
            Event::Message(other) => panic!("unexpected {other:?}"),
            _ => panic!("expected lane statuses"),
        }
    }
    settled
}

#[tokio::test]
async fn a_watcher_learns_that_a_lane_failed_or_warned_but_not_the_hosts_paths_or_reasons() {
    let backend = Backend::start().await;
    let secret = backend.folder.path().join("SECRET-DIR");
    std::fs::create_dir(&secret).unwrap();
    // One lane loads with a warning about its malformed row, the other's file is gone by the time it loads.
    let (warned, gone) = (secret.join("warned.txt"), secret.join("gone.txt"));
    std::fs::write(&warned, "1,1,10,10,20,20,1,1,1\nnot a row\n").unwrap();
    std::fs::write(&gone, "1,1,10,10,20,20,1,1,1\n").unwrap();
    let video = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[warned, gone.clone()])
        .await;
    std::fs::remove_file(&gone).unwrap();

    let mut owner = backend.viewer().await;
    owner.open(&video.ticket(), None).await;
    owner.opened().await;
    let told = settled_lanes(&mut owner, 2).await;
    assert!(
        told[&0].contains("line 2"),
        "the owner hears the warning: {told:?}"
    );
    assert!(
        told[&1].contains("SECRET-DIR"),
        "and where it failed: {told:?}"
    );

    let share = Command::Control(Control::Share {
        access: Access::Watch,
    });
    video.owner.command(share).unwrap();
    let link = video.owner.state().sharing.as_ref().unwrap().link.clone();
    let seat = backend
        .state
        .sessions
        .join(&link, CursorOptions::default())
        .unwrap();
    let Ok(Reply::Ticket(ticket)) = seat.command(Command::RequestTicket(video.key.clone())) else {
        panic!("a watcher may stream what the owner has open");
    };
    let mut watcher = backend.viewer().await;
    watcher.open(&ticket.to_string(), None).await;
    watcher.opened().await;
    let told = settled_lanes(&mut watcher, 2).await;
    assert!(told[&0].contains(r#""warnings":[]"#), "{told:?}");
    assert!(told[&1].contains("FileNotFound"), "{told:?}");
    for status in told.values() {
        for host_only in ["SECRET-DIR", "line 2", "reason", "location"] {
            assert!(
                !status.contains(host_only),
                "{host_only} reached the watcher: {status}"
            );
        }
    }
}
