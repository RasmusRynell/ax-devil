//! A revoked or narrowed stream stops at once: while the video is being indexed, with a message queued for the socket,
//! and for the frames, Sample Items and Frame Maps its viewer still has coming without asking again.

use super::*;
use crate::video_sources::VideoSources;
use api_types::playback::{Direction, LanePolicyWire, StickyWire, Want as WireWant};
use foundation::{CancelToken, FrameSize, HostPath, Interrupt, MediaTime, Timeline};
use futures_util::{sink, stream as streams};
use open::open_video;
use overlays::{MapRun, OverlayEvent, OverlayService};
use playback::{Authority, CachePool, ClockIntent, Registry};
use std::{
    collections::BTreeSet,
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicI64, Ordering},
    },
};
use tokio::sync::{Semaphore, oneshot};
use workspace::{ContentId, Entry, OverlayRef, VideoRef};

const WAIT: Duration = Duration::from_secs(20);
/// A tenth of the viewer's default 512 MiB frame budget.
const SAMPLE_BUDGET: u64 = 53_687_091;

#[tokio::test]
async fn revoking_while_the_index_is_built_cancels_it_and_ends_the_session() {
    let (holder, authority) = watch::channel(Authority::default());
    let mut gate = Gate::new(authority, || 0);
    let (_stop, mut closing) = watch::channel(false);
    let (started, running) = oneshot::channel();
    let (stopped_building, cancelled) = oneshot::channel();
    // The build runs until it is cancelled, as a slow index does.
    let building = move |cancel: &CancelToken, _: &mut dyn FnMut(f32)| {
        let _ = started.send(());
        while !cancel.is_cancelled() {
            std::thread::sleep(Duration::from_millis(2));
        }
        let _ = stopped_building.send(());
        Err::<(), _>(Interrupt::Cancelled)
    };
    let (outbox, _queue) = mpsc::channel(1);
    let mut viewer = streams::pending();
    let location = HostPath::new("/clips/a.mp4");

    let (ended, ()) = tokio::join!(
        tokio::time::timeout(
            WAIT,
            open_video(
                building,
                &location,
                &mut viewer,
                &outbox,
                &mut closing,
                &mut gate,
            )
        ),
        async {
            tokio::time::timeout(WAIT, running).await.unwrap().unwrap();
            holder.send_replace(Authority::Revoked);
        }
    );

    assert!(matches!(ended, Ok(Err(End::Revoked))));
    tokio::time::timeout(WAIT, cancelled)
        .await
        .expect("the index build is cancelled")
        .unwrap();
}

#[tokio::test]
async fn a_message_queued_when_the_authority_is_revoked_is_dropped_and_the_socket_closes_revoked() {
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    let delivered = Arc::new(Mutex::new(Vec::new()));
    let permits = Arc::new(Semaphore::new(0));
    // A socket that takes each message only when it is let through, as one whose peer reads slowly does.
    let socket = Box::pin(sink::unfold((), {
        let (delivered, permits) = (delivered.clone(), permits.clone());
        move |(), message: Message| {
            let (delivered, permits) = (delivered.clone(), permits.clone());
            async move {
                permits.acquire().await.unwrap().forget();
                delivered.lock().unwrap().push(message);
                Ok::<(), ()>(())
            }
        }
    }));
    let (outbox, writer) = spawn_writer(socket, gate);

    let frame = |n: u8| Message::Binary(vec![n].into());
    outbox.send(frame(1).into()).await.unwrap();
    // The first message is with the socket, the second waits behind it.
    outbox.send(frame(2).into()).await.unwrap();
    holder.send_replace(Authority::Revoked);
    permits.add_permits(8);
    drop(outbox);
    tokio::time::timeout(WAIT, writer).await.unwrap().unwrap();

    let delivered = delivered.lock().unwrap();
    assert_eq!(*delivered, [frame(1), gate::revoked_close()]);
}

/// A paused presenter on `frame`: a follower may have the four frames either side of it.
fn paused_on(frame: u64) -> Authority {
    Authority::Follow {
        intent: ClockIntent {
            frame,
            playing: false,
            speed_centi: 100,
            at_us: 0,
        },
    }
}

#[tokio::test]
async fn a_frame_queued_outside_a_narrowed_authority_is_dropped_and_the_rest_still_go() {
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    gate.open(Timeline::new((0..90).map(|n| MediaTime::from_us(n * 33_333)).collect()).unwrap());
    let delivered = Arc::new(Mutex::new(Vec::new()));
    let permits = Arc::new(Semaphore::new(0));
    let socket = Box::pin(sink::unfold((), {
        let (delivered, permits) = (delivered.clone(), permits.clone());
        move |(), message: Message| {
            let (delivered, permits) = (delivered.clone(), permits.clone());
            async move {
                permits.acquire().await.unwrap().forget();
                delivered.lock().unwrap().push(message);
                Ok::<(), ()>(())
            }
        }
    }));
    let (outbox, writer) = spawn_writer(socket, gate);
    let frame = |id: u8| Outgoing::frame(Message::Binary(vec![id].into()), u64::from(id));

    outbox.send(frame(60)).await.unwrap();
    // Frame 60 is with the socket, frame 61 waits behind it, and then the viewer is made a follower of frame 10.
    outbox.send(frame(61)).await.unwrap();
    holder.send_replace(paused_on(10));
    permits.add_permits(8);
    outbox.send(frame(12)).await.unwrap();
    outbox.send(Message::text("progress").into()).await.unwrap();
    drop(outbox);
    tokio::time::timeout(WAIT, writer).await.unwrap().unwrap();

    let sent = |id: u8| Message::Binary(vec![id].into());
    assert_eq!(
        *delivered.lock().unwrap(),
        [sent(60), sent(12), Message::text("progress")],
        "frame 60 was already with the socket; frame 61 was not and is not within four frames of 10"
    );
}

#[tokio::test]
async fn a_map_run_queued_with_a_frame_outside_a_narrowed_authority_is_dropped_whole_and_handed_back()
 {
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    gate.open(Timeline::new((0..90).map(|n| MediaTime::from_us(n * 33_333)).collect()).unwrap());
    let delivered = Arc::new(Mutex::new(Vec::new()));
    let socket = Box::pin(sink::unfold((), {
        let delivered = delivered.clone();
        move |(), message: Message| {
            let delivered = delivered.clone();
            async move {
                delivered.lock().unwrap().push(message);
                Ok::<(), ()>(())
            }
        }
    }));
    let (outbox, writer) = spawn_writer(socket, gate.clone());
    let run = |first: u64| {
        let run = MapRun {
            lane: 0,
            policy_revision: 1,
            data_revision: 1,
            first,
            samples: vec![0; 9],
            kinds: vec![0; 9],
            frame: 10,
        };
        Outgoing {
            message: Message::Binary(vec![first as u8].into()),
            frames: Some(run.frames()),
            data: Some(OverlayEvent::Map(run)),
        }
    };

    // A follower of frame 10 may have 6 to 14. The run of 6 to 14 is its own; the run of 8 to 16 is ranked by
    // frame 10, which it may have, but reaches frames it may not.
    holder.send_replace(paused_on(10));
    outbox.send(run(6)).await.unwrap();
    outbox.send(run(8)).await.unwrap();
    drop(outbox);
    tokio::time::timeout(WAIT, writer).await.unwrap().unwrap();

    assert_eq!(
        *delivered.lock().unwrap(),
        [Message::Binary(vec![6].into())]
    );
    let [OverlayEvent::Map(handed_back)] = &gate.take_rejected()[..] else {
        panic!("the dropped run is handed back");
    };
    assert_eq!(handed_back.frames(), 8..=16);
}

#[test]
fn a_run_is_admitted_against_one_reading_of_the_clock_and_the_authority() {
    // A playing follower of frame 0: 0 to 200 at t = 0, 200 to 500 at t = 3 s. Every read of the clock moves it on.
    let (_holder, authority) = watch::channel(Authority::Follow {
        intent: ClockIntent {
            frame: 0,
            playing: true,
            speed_centi: 100,
            at_us: 0,
        },
    });
    let reads = Arc::new(AtomicI64::new(0));
    let gate = Gate::new(authority, {
        let reads = reads.clone();
        move || reads.fetch_add(1, Ordering::SeqCst) * 3_000_000
    });
    gate.open(Timeline::new((0..1000).map(|n| MediaTime::from_us(n * 10_000)).collect()).unwrap());

    // Frame 100 is allowed only at t = 0 and frame 400 only at t = 3 s: no one reading allows both.
    assert!(!gate.admits(&(100..=400)));
    assert_eq!(reads.load(Ordering::SeqCst), 1);
    // A sample is one frame, judged by one read as well: at t = 3 s frame 100 is behind, at t = 6 s (500 to 800) 600
    // is inside.
    assert!(!gate.admits(&(100..=100)));
    assert!(gate.admits(&(600..=600)));
    assert_eq!(reads.load(Ordering::SeqCst), 3);
}

/// The fixture video with one lane whose ground truth covers every frame, so each frame in a window has an overlay.
fn entry_with_a_lane(folder: &Path) -> Entry {
    let gt = folder.join("gt.txt");
    let rows: String = (1..=90)
        .map(|frame| format!("{frame},1,10,10,20,20,1,1,1\n"))
        .collect();
    std::fs::write(&gt, rows).unwrap();
    let item = foundation::ItemId::generate();
    Entry {
        id: ContentId::of_item(&item),
        label: "clip".into(),
        video: VideoRef::File(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/cfr_h264_gop10_bf2.mp4"),
        ),
        overlays: vec![OverlayRef {
            label: "GT".into(),
            path: gt,
            handler: "MOT_FILE".into(),
            options: Default::default(),
        }],
    }
}

#[tokio::test]
async fn a_viewer_made_a_follower_gets_no_frame_or_data_outside_the_presenters_window_and_ends_once_revoked()
 {
    let folder = tempfile::tempdir().unwrap();
    let videos = Arc::new(VideoSources::new(
        Registry::new(CachePool::new(256 << 20)),
        &folder.path().join("cache"),
    ));
    let entry = entry_with_a_lane(folder.path());
    let opened = videos
        .open(&entry.video, &CancelToken::new(), &mut |_| {})
        .unwrap_or_else(|_| panic!("the video opens"));
    let mut session = opened.session;
    let handlers = handlers::Handlers::built_in();
    let service = OverlayService::new(scene_store::TrackRegistry::new(handlers, None));
    let library = ::catalogs::Library::built_in();
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    let (_stop, mut closing) = watch::channel(false);
    // A socket that takes each message only when it is let through, as one whose peer reads slowly does.
    let delivered = Arc::new(Mutex::new(Vec::<Message>::new()));
    let permits = Arc::new(Semaphore::new(0));
    let socket = Box::pin(sink::unfold((), {
        let (delivered, permits) = (delivered.clone(), permits.clone());
        move |(), message: Message| {
            let (delivered, permits) = (delivered.clone(), permits.clone());
            async move {
                permits.acquire().await.unwrap().forget();
                delivered.lock().unwrap().push(message);
                Ok::<(), ()>(())
            }
        }
    }));
    let (outbox, writer) = spawn_writer(socket, gate.clone());
    let (asks, asked) = mpsc::channel::<Message>(1);
    let mut viewer = Box::pin(streams::unfold(asked, |mut asked| async {
        asked.recv().await.map(|message| (Ok(message), asked))
    }));
    let setup = stream::Setup {
        entry: &entry,
        start_frame: Some(60),
        data_bytes: SAMPLE_BUDGET,
        audience: Audience::HOST,
        size: FrameSize {
            width: 64,
            height: 48,
        },
        warnings: Vec::new(),
        library: &library,
        overlays: &service,
        exports: &Arc::new(crate::exports::ExportService::electron()),
        videos: &videos,
    };
    // An exploring viewer asks for the whole video around frame 60, once.
    let everything = serde_json::to_string(&ClientMessage::Want(WireWant {
        playhead: 60,
        ahead: u32::MAX,
        behind: u32::MAX,
        direction: Direction::Forward,
        data_ahead: 10,
        data_behind: 10,
    }))
    .unwrap();
    // The lane draws, so its maps and samples come.
    let subscribe = serde_json::to_string(&ClientMessage::LanePolicy {
        lane: 0,
        revision: 1,
        policy: LanePolicyWire {
            tolerance_ms: None,
            sticky: StickyWire {
                enabled: false,
                timeout_ms: None,
            },
        },
        subscribed: true,
    })
    .unwrap();
    // What an envelope covers (see `api_types::playback`): the frame of a video frame, the frames a Frame Map covers,
    // or the one sample a Sample Item is. The file has one sample per frame, so a sample's index is its frame.
    let covers = |bytes: &[u8]| {
        let first = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
        match bytes[5] {
            5 => {
                let count = u64::from(u32::from_le_bytes(bytes[40..44].try_into().unwrap()));
                first..first + count
            }
            _ => first..first + 1,
        }
    };
    let seen = || delivered.lock().unwrap().len();

    let streaming = stream::stream(
        &mut session,
        setup,
        &mut viewer,
        &outbox,
        &mut closing,
        gate,
    );
    let watching = async {
        // Twice: the second goes in only once the first was taken, which is when it applies.
        asks.send(Message::text(&everything)).await.unwrap();
        asks.send(Message::text(&everything)).await.unwrap();
        asks.send(Message::text(&subscribe)).await.unwrap();
        // While the viewer explores, the lane's data comes for frames far from where it will follow. Let the writer
        // send one message at a time until a far sample has gone.
        loop {
            let before = seen();
            permits.add_permits(1);
            while seen() == before {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
            let last = delivered.lock().unwrap().last().cloned().unwrap();
            if matches!(&last, Message::Binary(bytes) if bytes[5] == 4 && covers(bytes).start > 20)
            {
                break;
            }
        }
        // Something more waits in the outbox because the socket takes nothing.
        while outbox.capacity() > 0 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        // The owner turns exploring off: the viewer follows a presenter paused on frame 10 and says nothing more.
        let cut = seen();
        holder.send_replace(paused_on(10));
        permits.add_permits(1 << 20);
        // The lane's maps and samples cover every frame of its window.
        let mut unmapped = BTreeSet::from_iter(6..=14);
        let mut unsent = BTreeSet::from_iter(6..=14);
        while !(unmapped.is_empty() && unsent.is_empty()) {
            tokio::time::sleep(Duration::from_millis(5)).await;
            for message in &delivered.lock().unwrap()[cut..] {
                if let Message::Binary(bytes) = message {
                    let left = match bytes[5] {
                        5 => &mut unmapped,
                        4 => &mut unsent,
                        _ => continue,
                    };
                    for covered in covers(bytes) {
                        left.remove(&covered);
                    }
                }
            }
        }
        holder.send_replace(Authority::Revoked);
        cut
    };
    let (ended, cut) = tokio::time::timeout(WAIT, async { tokio::join!(streaming, watching) })
        .await
        .expect("the window arrives and the stream ends");
    assert!(matches!(ended, Err(End::Revoked)), "a revoked stream ends");
    drop(outbox);
    tokio::time::timeout(WAIT, writer).await.unwrap().unwrap();

    // The order and the contents of what the socket took. The message after the cut is the one the writer already
    // held when the authority narrowed, which may be from before; everything after it is the follower's.
    let delivered = delivered.lock().unwrap();
    assert_eq!(delivered.last(), Some(&gate::revoked_close()));
    let (mut window, mut told, mut held) =
        (None::<RangeInclusive<u64>>, Vec::new(), BTreeSet::new());
    for (at, message) in delivered.iter().enumerate() {
        match message {
            Message::Text(body) => match serde_json::from_str(body) {
                Ok(ServerMessage::DataWindow { first, last }) => {
                    // Controls are not gated: the window is the authority's at the time it was applied.
                    assert!(
                        at <= cut || (6..=14).contains(&first) && (6..=14).contains(&last),
                        "the data window {first}..={last} is outside the presenter's window"
                    );
                    window = Some(first..=last);
                    told.push(first..=last);
                }
                Ok(ServerMessage::Evict {
                    source, samples, ..
                }) => {
                    assert_eq!(source, 0);
                    for sample in samples.into_iter().map(u64::from) {
                        assert!(
                            told.iter().any(|window| window.contains(&sample)),
                            "evicting {sample}, which no window told to the viewer had"
                        );
                        assert!(
                            !(6..=14).contains(&sample),
                            "evicting {sample}, which the follower still needs"
                        );
                        held.remove(&sample);
                    }
                }
                _ => {}
            },
            Message::Binary(bytes) if matches!(bytes[5], 1 | 4 | 5) => {
                let covered = covers(bytes);
                assert!(
                    at <= cut
                        || (6..=14).contains(&covered.start)
                            && (6..=14).contains(&(covered.end - 1)),
                    "{covered:?} is outside the presenter's window"
                );
                match bytes[5] {
                    5 => {
                        let window = window
                            .as_ref()
                            .expect("the window is told before the first map");
                        assert!(
                            window.contains(&covered.start) && window.contains(&(covered.end - 1)),
                            "the map of {covered:?} is outside the window {window:?} told before it"
                        );
                    }
                    4 => {
                        held.insert(covered.start);
                    }
                    _ => {}
                }
            }
            _ => {}
        }
        if at + 1 == cut {
            assert!(
                !held.is_empty(),
                "the viewer held far samples before it was narrowed"
            );
        }
    }
    // Every control survived the narrowing: what the viewer held outside its window was evicted.
    assert!(
        held.iter().all(|sample| (6..=14).contains(sample)),
        "the viewer still holds {held:?}"
    );
}
