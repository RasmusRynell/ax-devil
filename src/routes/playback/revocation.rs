//! A revoked or narrowed stream stops at once: while the video is being indexed, with a message queued for the socket,
//! and for the frames and overlays its viewer still has coming without asking again.

use super::*;
use crate::video_sources::VideoSources;
use api_types::playback::{Direction, Want as WireWant};
use foundation::{CancelToken, FrameSize, HostPath, Interrupt, MediaTime, Timeline};
use futures_util::{sink, stream as streams};
use open::open_video;
use overlays::OverlayService;
use playback::{Authority, CachePool, ClockIntent, Registry};
use std::{
    collections::BTreeSet,
    path::Path,
    sync::{Arc, Mutex},
};
use tokio::sync::{Semaphore, oneshot};
use workspace::{ContentId, Entry, OverlayRef, VideoRef};

const WAIT: Duration = Duration::from_secs(20);

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
    let frame = |id: u8| Outgoing {
        message: Message::Binary(vec![id].into()),
        frame: Some(u64::from(id)),
    };

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
async fn a_viewer_made_a_follower_gets_no_frame_or_overlay_outside_the_presenters_window_and_ends_once_revoked()
 {
    let folder = tempfile::tempdir().unwrap();
    let videos = VideoSources::new(
        Registry::new(CachePool::new(256 << 20)),
        &folder.path().join("cache"),
    );
    let entry = entry_with_a_lane(folder.path());
    let opened = videos
        .open(&entry.video, &CancelToken::new(), &mut |_| {})
        .unwrap_or_else(|_| panic!("the video opens"));
    let mut session = opened.session;
    let handlers = handlers::Handlers::built_in();
    let service = OverlayService::new(
        scene_store::TrackRegistry::new(handlers, None),
        catalogs::Library::built_in(),
    );
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    let (_stop, mut closing) = watch::channel(false);
    let (outbox, mut queue) = mpsc::channel::<Outgoing>(1);
    let (asks, asked) = mpsc::channel::<Message>(1);
    let mut viewer = Box::pin(streams::unfold(asked, |mut asked| async {
        asked.recv().await.map(|message| (Ok(message), asked))
    }));
    let setup = stream::Setup {
        entry: &entry,
        start_frame: Some(60),
        audience: Audience::HOST,
        size: FrameSize {
            width: 64,
            height: 48,
        },
        warnings: Vec::new(),
        catalogs: Vec::new(),
        overlays: &service,
    };
    // An exploring viewer asks for the whole video around frame 60, once.
    let everything = serde_json::to_string(&ClientMessage::Want(WireWant {
        playhead: 60,
        ahead: u32::MAX,
        behind: u32::MAX,
        direction: Direction::Forward,
    }))
    .unwrap();
    let is_overlay =
        |sent: &Outgoing| matches!(&sent.message, Message::Binary(bytes) if bytes[5] == 2);

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
        // While the viewer explores, the lane's overlays come for frames far from where it will follow.
        loop {
            let sent = queue.recv().await.unwrap();
            if is_overlay(&sent) && sent.frame.is_some_and(|frame| frame > 20) {
                break;
            }
        }
        // Something more waits in the outbox because nobody reads it.
        while queue.is_empty() {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        // The owner turns exploring off: the viewer follows a presenter paused on frame 10 and says nothing more.
        holder.send_replace(paused_on(10));
        // What was already queued was handed over before the change. Nothing after it may be outside frames 6 to 14,
        // neither a frame nor an overlay, and the lane's overlays come for every frame in it. (Its frames may have
        // gone already, while the lane loaded.)
        queue.recv().await.unwrap();
        let mut overlays = BTreeSet::from_iter(6..=14);
        while !overlays.is_empty() {
            let sent = queue.recv().await.unwrap();
            let Some(frame) = sent.frame else {
                continue;
            };
            assert!(
                (6..=14).contains(&frame),
                "frame {frame} is outside the presenter's window"
            );
            if is_overlay(&sent) {
                overlays.remove(&frame);
            }
        }
        holder.send_replace(Authority::Revoked);
    };
    let (ended, ()) = tokio::time::timeout(WAIT, async { tokio::join!(streaming, watching) })
        .await
        .expect("the window arrives and the stream ends");
    assert!(matches!(ended, Err(End::Revoked)), "a revoked stream ends");
    // The writer drops whatever was still queued and closes the socket revoked (see the test above).
}
