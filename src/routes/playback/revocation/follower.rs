//! A viewer made a follower of a presenter: what it is sent is judged against the presenter's window, and it ends once
//! revoked.

use super::*;
use crate::video_sources::VideoSources;
use api_types::playback::{Direction, LanePolicyWire, StickyWire, Want as WireWant};
use foundation::FrameSize;
use overlays::OverlayService;
use playback::{CachePool, Registry};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};
use workspace::{ContentId, Entry, OverlayRef, VideoRef};

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
            slot: "GT".into(),
            source: handler_api::LaneSource::File(gt),
            handler: "MOT_FILE".into(),
            options: handler_api::Settings::from_iter([
                ("width".to_owned(), 64.into()),
                ("height".to_owned(), 48.into()),
            ]),
            metadata: Default::default(),
        }],
        starts_hidden: false,
        lanes_starting_hidden: Vec::new(),
        metadata: Default::default(),
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
    let service = OverlayService::new(scene_store::TrackRegistry::new(
        handlers.decoder_lookup(),
        None,
    ));
    let library = ::catalogs::Library::built_in();
    let content = workspace::Content::SeekableVideo(workspace::SeekableVideoContent {
        item_id: foundation::ItemId::generate(),
        entry: entry.clone(),
    });
    let settings = ::settings::Settings::load(&folder.path().join("settings.json")).unwrap();
    let (holder, authority) = watch::channel(Authority::default());
    let gate = Gate::new(authority, || 0);
    let (_stop, mut closing) = watch::channel(false);
    let overlay_service = Arc::new(service.clone());
    let preparer = crate::prepare::Preparer::new(videos.clone(), overlay_service, closing.clone());
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
        matching: &BTreeMap::new(),
        start_frame: Some(60),
        data_bytes: SAMPLE_BUDGET,
        audience: Audience::HOST,
        session: sessions::SessionId::generate(),
        size: FrameSize {
            width: 64,
            height: 48,
        },
        wire: super::wire::Wire::Raw,
        warnings: Vec::new(),
        pts: None,
        cadence: None,
        frame_rate: None,
        library: &library,
        overlays: &service,
        exports: &Arc::new(crate::exports::ExportService::electron()),
        videos: &videos,
        content: &content,
        preparer: &preparer,
        settings: &settings,
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
            basis: None,
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
