//! A playback socket's lanes against the committed fixture video and overlay files written by each test, through
//! the real routes, decoders and catalogs: what a lane reports, the Frame Maps and Sample Items it sends for the
//! policy they were selected under, what a policy that cannot be applied does, the catalogs a viewer can fetch, and
//! what a watcher may learn from them.

mod common;

use api_types::playback::{
    BasisWire, ClientMessage, Direction, FRAME_MAP_EMPTY, FRAME_MAP_NONE, IngestWire,
    LaneDefaultsWire, LaneInfo, LanePolicyWire, LaneStatusWire, MATCH_EXACT, ServerMessage,
    StickyWire,
};
use common::{Backend, Event, Viewer, fixture};
use overlay_sync::{MatchBasis, MatchPolicy, Sticky, Tolerance};
use sessions::{
    CursorOptions,
    session::{Access, Command, Control, Reply},
};
use std::collections::BTreeMap;
use workspace_store::DocCommand;

fn policy(tolerance_ms: Option<u32>, timeout_ms: Option<u32>) -> LanePolicyWire {
    LanePolicyWire {
        basis: None,
        tolerance_ms,
        sticky: StickyWire {
            enabled: false,
            timeout_ms,
        },
    }
}

fn lane_policy(
    lane: u16,
    revision: u32,
    policy: LanePolicyWire,
    subscribed: bool,
) -> ClientMessage {
    ClientMessage::LanePolicy {
        lane,
        revision,
        policy,
        subscribed,
    }
}

/// Everything lane 0 has sent so far.
#[derive(Default)]
struct Seen {
    ready: Option<LaneStatusWire>,
    /// How many times each sample arrived.
    samples: BTreeMap<u64, u32>,
    /// By policy revision and frame: the sample cell, the kind, and the data revision the run was made under.
    maps: BTreeMap<(u32, u64), (u32, u8, u32)>,
    problems: Vec<String>,
    /// The data windows told, in order.
    windows: Vec<(u64, u64)>,
    /// The samples told to drop, in order.
    evicted: Vec<u32>,
}

impl Seen {
    /// Reads until `done` holds, which bounds the wait through the viewer's own timeout.
    async fn read_until(&mut self, viewer: &mut Viewer, done: impl Fn(&Self) -> bool) {
        while !done(self) {
            match viewer.next().await {
                Event::Message(ServerMessage::LaneStatus {
                    lane: 0,
                    status: status @ LaneStatusWire::Ready { .. },
                }) => self.ready = Some(status),
                Event::Message(ServerMessage::LaneStatus { .. }) | Event::Frame(_) => {}
                Event::Message(ServerMessage::LaneProblem { lane: 0, reason }) => {
                    self.problems.push(reason);
                }
                Event::Message(ServerMessage::DataWindow { first, last }) => {
                    self.windows.push((first, last));
                }
                Event::Message(ServerMessage::Evict {
                    source: 0,
                    data_revision: 1,
                    samples,
                }) => self.evicted.extend(samples),
                Event::Sample(sample) => {
                    assert_eq!(sample.source, 0);
                    *self.samples.entry(sample.sample).or_default() += 1;
                }
                Event::Map(map) => {
                    assert_eq!(map.lane, 0);
                    for (k, (cell, kind)) in map.samples.iter().zip(&map.kinds).enumerate() {
                        self.maps
                            .insert((map.policy, map.first + k as u64), (*cell, *kind, map.data));
                    }
                }
                _ => panic!("expected the lane's status, data and frames"),
            }
        }
    }

    /// Whether the maps made under `policy` cover frames 0 to 5.
    fn mapped(&self, policy: u32) -> bool {
        (0..=5).all(|frame| self.maps.contains_key(&(policy, frame)))
    }
}

/// A video with one lane whose file has a box on frames 0 to 2 and is dense, so the others are covered with nothing.
async fn viewer_with_a_lane(backend: &Backend) -> (Viewer, common::Described) {
    let gt =
        backend.mot_file("1,1,10,10,20,20,1,1,1\n2,1,12,10,20,20,1,1,1\n3,1,14,10,20,20,1,1,1\n");
    let id = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[gt])
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    (viewer, video)
}

#[tokio::test]
async fn a_catalog_is_fetched_by_id_and_one_that_is_not_served_answers_with_none() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    for id in ["built-in:standard", "user:night.json", "nope"] {
        viewer.send(&ClientMessage::Catalog { id: id.into() }).await;
    }

    let mut answers = BTreeMap::new();
    while answers.len() < 3 {
        match viewer.next().await {
            Event::Message(ServerMessage::Catalog {
                id,
                revision,
                document,
                ..
            }) => {
                answers.insert(id, (revision, document));
            }
            Event::Frame(_) => {}
            _ => panic!("expected the answers to the catalog requests"),
        }
    }
    let (revision, document) = &answers["built-in:standard"];
    assert_eq!(*revision, 1);
    let document: serde_json::Value = serde_json::from_str(document.as_ref().unwrap()).unwrap();
    assert_eq!(
        (document["schema"].as_u64(), document["name"].as_str()),
        (Some(4), Some("Standard"))
    );
    for unserved in ["user:night.json", "nope"] {
        assert_eq!(answers[unserved], (0, None), "{unserved}");
    }
}

#[tokio::test]
async fn opened_lists_the_served_catalogs_and_what_every_lane_starts_with() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;

    let (lane_defaults, catalogs) = loop {
        match viewer.next().await {
            Event::Message(ServerMessage::Indexing { .. }) => {}
            Event::Message(ServerMessage::Opened {
                lane_defaults,
                catalogs,
                ..
            }) => break (lane_defaults, catalogs),
            _ => panic!("expected Indexing or Opened"),
        }
    };

    let ids: Vec<_> = catalogs.iter().map(|catalog| catalog.id.as_str()).collect();
    assert_eq!(
        ids,
        [
            "built-in:standard",
            "built-in:tracking",
            "built-in:classic",
            "built-in:chunky",
            "built-in:glass",
            "built-in:minimal"
        ]
    );
    assert_eq!(
        lane_defaults,
        LaneDefaultsWire {
            catalog: "built-in:standard".into(),
            policy: LanePolicyWire {
                basis: None,
                tolerance_ms: Some(50),
                sticky: StickyWire {
                    enabled: true,
                    timeout_ms: Some(2050),
                },
            },
        }
    );
}

#[tokio::test]
async fn a_policy_for_a_lane_without_an_overlay_is_ignored_and_the_session_goes_on() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    assert_eq!(
        video.lanes,
        [LaneInfo {
            label: "cfr_h264_gop10_bf2.mp4".into(),
            source: None,
            policy: None,
        }]
    );

    viewer
        .send(&lane_policy(0, 1, policy(None, Some(100_001)), true))
        .await;

    viewer.want(40, 3, 0, Direction::Forward).await;
    viewer.frames(40..=43).await;
}

#[tokio::test]
async fn a_lane_reports_its_load_then_sends_its_maps_and_each_mapped_sample_once_under_the_policy_asked_for()
 {
    let backend = Backend::start().await;
    let (mut viewer, video) = viewer_with_a_lane(&backend).await;
    assert_eq!(
        video.lanes,
        [LaneInfo {
            label: "gt.txt".into(),
            source: Some(0),
            policy: None,
        }]
    );

    // A lane sends nothing until it is subscribed.
    let mut seen = Seen::default();
    viewer.want(0, 5, 0, Direction::Forward).await;
    seen.read_until(&mut viewer, |seen| seen.ready.is_some())
        .await;
    let Some(LaneStatusWire::Ready {
        samples,
        filter,
        data_revision,
        mean_sample_bytes,
        ingest,
        ..
    }) = seen.ready.clone()
    else {
        panic!("the lane is ready");
    };
    assert_eq!((samples, data_revision), (3, 1));
    assert_eq!(ingest, IngestWire::Complete);
    assert!(mean_sample_bytes > 0);
    assert!(filter.iter().any(|option| option.id == "show_person"));
    assert!(seen.maps.is_empty() && seen.samples.is_empty());

    viewer
        .send(&lane_policy(0, 1, policy(None, None), true))
        .await;
    seen.read_until(&mut viewer, |seen| {
        seen.mapped(1) && seen.samples.len() == 3
    })
    .await;
    for frame in 0..3 {
        assert_eq!(
            seen.maps[&(1, frame)],
            (frame as u32, MATCH_EXACT, data_revision)
        );
    }
    for frame in 3..=5 {
        assert_eq!(
            seen.maps[&(1, frame)],
            (FRAME_MAP_EMPTY, MATCH_EXACT, data_revision),
            "a covered frame with nothing is empty, not missing"
        );
    }
    assert!(seen.maps.values().all(|(cell, ..)| *cell != FRAME_MAP_NONE));
    assert_eq!(
        seen.samples.values().copied().collect::<Vec<_>>(),
        [1, 1, 1]
    );

    // The data window was told before the maps it holds frames for.
    assert_eq!(seen.windows.last(), Some(&(0, 5)));
    assert!(seen.evicted.is_empty(), "nothing left the window");

    // Another policy resends the maps under its revision. The viewer keeps its samples until told otherwise.
    viewer
        .send(&lane_policy(0, 2, policy(Some(50), None), true))
        .await;
    seen.read_until(&mut viewer, |seen| seen.mapped(2)).await;
    assert_eq!(seen.maps[&(2, 1)].0, 1);

    // A policy that cannot be applied is reported; the lane keeps its matching and the maps carry the new revision.
    viewer
        .send(&lane_policy(0, 3, policy(None, Some(100_001)), true))
        .await;
    seen.read_until(&mut viewer, |seen| seen.mapped(3)).await;
    assert!(
        seen.problems[0].contains("100 seconds"),
        "{:?}",
        seen.problems
    );
    assert_eq!(seen.maps[&(3, 1)].0, 1);
    assert_eq!(
        seen.samples.values().copied().collect::<Vec<_>>(),
        [1, 1, 1],
        "no sample was sent again"
    );

    // Unsubscribing stops the data and frees what the viewer holds; the window that follows is told, then only
    // frames arrive for it.
    viewer
        .send(&lane_policy(0, 4, policy(None, None), false))
        .await;
    viewer.want(40, 3, 0, Direction::Forward).await;
    seen.read_until(&mut viewer, |seen| {
        seen.evicted.len() == 3 && seen.windows.last() == Some(&(40, 43))
    })
    .await;
    assert_eq!(seen.evicted, [0, 1, 2]);
    viewer.frames(40..=43).await;
}

#[tokio::test]
async fn one_file_shown_twice_is_two_lanes_with_their_own_policies_on_one_source() {
    let backend = Backend::start().await;
    let gt =
        backend.mot_file("1,1,10,10,20,20,1,1,1\n2,1,12,10,20,20,1,1,1\n3,1,14,10,20,20,1,1,1\n");
    let id = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[gt.clone(), gt])
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    assert_eq!(
        video.lanes,
        [
            LaneInfo {
                label: "gt.txt".into(),
                source: Some(0),
                policy: None,
            },
            LaneInfo {
                label: "gt.txt (2)".into(),
                source: Some(0),
                policy: None,
            },
        ]
    );

    viewer.want(0, 5, 0, Direction::Forward).await;
    let ready = settled_lanes(&mut viewer, 2).await;
    assert!(
        ready.values().all(|status| status.contains("Ready")),
        "{ready:?}"
    );
    viewer
        .send(&lane_policy(0, 1, policy(None, None), true))
        .await;
    viewer
        .send(&lane_policy(1, 1, policy(Some(50), None), true))
        .await;

    let mut samples = BTreeMap::<(u16, u64), u32>::new();
    let mut maps = BTreeMap::<(u16, u64), u32>::new();
    while samples.len() < 3
        || !(0..2).all(|lane| (0..=5).all(|frame| maps.contains_key(&(lane, frame))))
    {
        match viewer.next().await {
            Event::Sample(sample) => {
                *samples.entry((sample.source, sample.sample)).or_default() += 1;
            }
            Event::Map(map) => {
                assert_eq!(map.policy, 1);
                for (k, cell) in map.samples.iter().enumerate() {
                    maps.insert((map.lane, map.first + k as u64), *cell);
                }
            }
            Event::Message(ServerMessage::DataWindow { .. }) | Event::Frame(_) => {}
            _ => panic!("expected the lanes' data and maps"),
        }
    }
    assert_eq!(
        samples
            .keys()
            .map(|(source, _)| *source)
            .collect::<Vec<_>>(),
        [0, 0, 0],
        "both lanes draw one source: its samples are sent once"
    );
    assert!(samples.values().all(|sent| *sent == 1));
    for lane in 0..2 {
        assert_eq!(
            (0..3).map(|frame| maps[&(lane, frame)]).collect::<Vec<_>>(),
            [0, 1, 2]
        );
    }
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
            | Event::Message(ServerMessage::DataWindow { .. } | ServerMessage::Evict { .. })
            | Event::Frame(_)
            | Event::Sample(_)
            | Event::Map(_) => {}
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
    let warned = backend.mot_file("1,1,10,10,20,20,1,1,1\nnot a row\n");
    let gone = secret.join("gone.txt");
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

#[tokio::test]
async fn an_onvif_lane_starts_by_frame_switches_to_time_and_the_workspace_keeps_it() {
    let backend = Backend::start().await;
    let xml: String = [0, 100, 200]
        .iter()
        .map(|ms| {
            format!(
                r#"<tt:Frame xmlns:tt="http://www.onvif.org/ver10/schema" UtcTime="1970-01-01T00:00:00.{ms:03}Z" />{}"#,
                "\n"
            )
        })
        .collect();
    let path = backend.folder.path().join("lane.xml");
    std::fs::write(&path, xml).unwrap();
    let id = backend
        .add_with_handlers(
            &fixture("cfr_h264_gop10_bf2.mp4"),
            &[(path, Some("ONVIF_XML"))],
        )
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    let mut seen = Seen::default();
    viewer.want(0, 5, 0, Direction::Forward).await;
    seen.read_until(&mut viewer, |seen| seen.ready.is_some())
        .await;
    let Some(LaneStatusWire::Ready {
        bases,
        default_basis,
        ..
    }) = seen.ready.clone()
    else {
        panic!("the lane is ready");
    };
    assert_eq!(bases, [BasisWire::Frame, BasisWire::Time]);
    assert_eq!(default_basis, BasisWire::Frame);

    // By frame, record k is frame k; by time, the records at 0, 100 and 200 ms are frames 0, 3 and 6.
    let frame = policy(None, None);
    viewer.send(&lane_policy(0, 1, frame, true)).await;
    seen.read_until(&mut viewer, |seen| seen.mapped(1)).await;
    assert_eq!(seen.maps[&(1, 1)].0, 1);
    let time = LanePolicyWire {
        basis: Some(BasisWire::Time),
        ..frame
    };
    viewer.send(&lane_policy(0, 2, time, true)).await;
    seen.read_until(&mut viewer, |seen| seen.mapped(2)).await;
    assert_eq!(seen.maps[&(2, 1)].0, FRAME_MAP_NONE);
    assert_eq!(
        (seen.maps[&(2, 3)].0, seen.maps[&(2, 3)].1),
        (1, MATCH_EXACT)
    );
    // The workspace keeps the choice: a viewer opened afterwards starts the lane from it.
    let workspace::Content::SeekableVideo(content) = &id.content else {
        panic!();
    };
    let matching = MatchPolicy {
        basis: Some(MatchBasis::Time),
        tolerance: Tolerance::Exact,
        sticky: Sticky {
            enabled: false,
            timeout: None,
        },
    };
    let kept = DocCommand::SetLaneMatching {
        item: content.item_id,
        lane: 0,
        matching,
    };
    id.owner.command(Command::Content(kept)).unwrap();
    let mut again = backend.viewer().await;
    again.open(&id.ticket(), None).await;
    assert_eq!(again.opened().await.lanes[0].policy, Some(time));

    // A frame-keyed file has no time basis: the lane says so and goes on by frame.
    let (mut viewer, _) = viewer_with_a_lane(&backend).await;
    let mut seen = Seen::default();
    viewer.want(0, 5, 0, Direction::Forward).await;
    viewer.send(&lane_policy(0, 1, time, true)).await;
    seen.read_until(&mut viewer, |seen| seen.mapped(1)).await;
    assert!(
        seen.problems[0].contains("cannot be matched by time"),
        "{:?}",
        seen.problems
    );
    assert_eq!(seen.maps[&(1, 1)].0, 1);
}
