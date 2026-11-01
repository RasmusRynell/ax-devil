//! The playback WebSocket against the committed fixture videos, through the real routes and real decoders. Frame N
//! of every luma-coded fixture is flat with luma (7 * N) mod 256 (`fixtures/video/generate.sh`), so a received frame
//! says which frame of the video it is.

mod common;

use api_types::playback::{ClientMessage, Direction, ServerMessage};
use common::{Backend, Event, MIB, fixture, luma};
use foundation::{CancelToken, ErrorCode, HostPath, Interrupt};
use playback::DECODE_WORKERS;
use playback::{Delivery, Want};
use sessions::{
    CursorOptions,
    session::{Access, Command, Control, Reply},
};
use std::{collections::BTreeSet, time::Duration};
use workspace::VideoRef;

#[tokio::test]
async fn opening_a_video_describes_it_then_sends_its_timeline_and_the_first_frame() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;

    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;

    assert_eq!(video.frame_count, 90);
    assert_eq!(video.frame_bytes, 64 * 48 + 2 * 32 * 24);
    let formula: Vec<f64> = (0..90u64)
        .map(|n| (n as f64 * 1e6 / 30.0).round())
        .collect();
    assert_eq!(video.times, formula);
    // The video ends one frame period after its last frame starts.
    let period = formula[89] - formula[88];
    assert_eq!(video.duration_us as f64, formula[89] + period);
    // Nothing asked for a window yet: the viewer still gets the frame it opened on, first.
    let Event::Frame(first) = viewer.next().await else {
        panic!("expected the first frame");
    };
    assert_eq!((first.sequence_id, first.timestamp_us), (0, 0));
    assert_eq!((first.width, first.height, first.layout), (64, 48, 1));
    assert_eq!(first.plane_lengths, [3072, 768, 768]);
    assert_eq!(first.flags & 1, 1, "frame 0 is a keyframe");
    assert_eq!(first.flat_luma(), 0);
}

#[tokio::test]
async fn a_window_fills_with_the_right_frames_starting_at_the_playhead() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("open_gop_h264.mkv")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    viewer.want(0, 25, 0, Direction::Forward).await;
    let arrived = viewer.frames(0..=25).await;

    assert_eq!(arrived[0], 0, "the playhead first");
    let unique: BTreeSet<u64> = arrived.iter().copied().collect();
    assert_eq!(unique.len(), arrived.len(), "each frame is sent once");
}

#[tokio::test]
async fn a_jump_far_away_and_back_delivers_each_new_window_with_the_right_frames() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("long_gop_h264.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    viewer.want(170, 3, 3, Direction::Forward).await;
    viewer.frames(167..=173).await;
    // Backward into the middle of an earlier group of 60 frames: decoding starts at frame 60.
    viewer.want(70, 2, 8, Direction::Backward).await;
    viewer.frames(62..=72).await;
}

#[tokio::test]
async fn a_video_opens_on_the_requested_frame_and_the_last_frame_says_so() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;

    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), Some(45)).await;
    viewer.opened().await;
    // Frames of its group that decode before it may come first; nothing far from it does.
    let arrived = viewer.frames(45..=45).await;
    assert!(
        arrived.iter().all(|id| id.abs_diff(45) <= 10),
        "{arrived:?}"
    );

    let mut past_the_end = backend.viewer().await;
    past_the_end.open(&id.ticket(), Some(1000)).await;
    past_the_end.opened().await;
    let last = loop {
        let Event::Frame(frame) = past_the_end.next().await else {
            panic!("expected frames around the last one");
        };
        assert!(frame.sequence_id >= 80, "clamped to the last frame");
        if frame.sequence_id == 89 {
            break frame;
        }
        assert_eq!(frame.flags & 2, 0, "only the last frame is flagged");
    };
    assert_eq!(last.flags & 2, 2, "the last frame is flagged");
}

#[tokio::test]
async fn a_failed_frame_is_reported_by_itself_and_its_neighbours_still_arrive() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("truncated.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    let video = viewer.opened().await;
    let last = video.frame_count - 1;

    // The cut tail fails; the window's first frames are whole.
    viewer.want(last - 9, 9, 0, Direction::Forward).await;
    let mut failed = BTreeSet::new();
    let mut shown = BTreeSet::new();
    let window = last - 9..=last;
    while !window
        .clone()
        .all(|id| failed.contains(&id) || shown.contains(&id))
    {
        match viewer.next().await {
            Event::Frame(frame) => {
                assert_eq!(frame.flat_luma(), luma(frame.sequence_id));
                shown.insert(frame.sequence_id);
            }
            Event::Message(ServerMessage::FrameError { sequence_id, error }) => {
                assert!(!error.reason.unwrap().is_empty());
                failed.insert(sequence_id);
            }
            _ => panic!("expected frames or frame errors"),
        }
    }
    assert!(failed.contains(&last), "the cut frame fails: {failed:?}");
    assert!(shown.contains(&(last - 9)), "the others arrive: {shown:?}");
}

#[tokio::test]
async fn closing_or_dropping_the_viewer_releases_the_source() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;

    let mut closing = backend.viewer().await;
    closing.open(&id.ticket(), None).await;
    closing.opened().await;
    closing.frames(0..=0).await;
    assert_eq!(backend.videos.registry().running_workers(), DECODE_WORKERS);
    closing.send(&ClientMessage::Close).await;
    backend.wait_for_workers(0).await;

    let mut leaving = backend.viewer().await;
    leaving.open(&id.ticket(), None).await;
    leaving.opened().await;
    leaving.frames(0..=0).await;
    assert_eq!(backend.videos.registry().running_workers(), DECODE_WORKERS);
    drop(leaving);
    backend.wait_for_workers(0).await;
}

#[tokio::test]
async fn two_viewers_of_one_file_share_one_source_until_the_last_leaves() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut first = backend.viewer().await;
    first.open(&id.ticket(), None).await;
    first.opened().await;
    first.frames(0..=0).await;

    let mut second = backend.viewer().await;
    second.open(&id.ticket(), Some(30)).await;
    let video = second.opened().await;
    assert_eq!(video.frame_count, 90);
    second.frames(30..=30).await;
    assert_eq!(
        backend.videos.registry().running_workers(),
        DECODE_WORKERS,
        "one source, one set of decoders"
    );

    first.send(&ClientMessage::Close).await;
    second.want(60, 5, 0, Direction::Forward).await;
    second.frames(60..=65).await;
    assert_eq!(
        backend.videos.registry().running_workers(),
        DECODE_WORKERS,
        "the second viewer keeps the source"
    );
    second.send(&ClientMessage::Close).await;
    backend.wait_for_workers(0).await;
}

#[tokio::test]
async fn a_cache_that_shrinks_to_nothing_and_grows_back_never_changes_what_is_delivered() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;
    viewer.want(0, 20, 0, Direction::Forward).await;
    viewer.frames(0..=20).await;

    // With no room for any frame, the frame at the playhead still arrives, once per window.
    backend.videos.registry().set_allowance(0);
    viewer.want(70, 0, 0, Direction::Forward).await;
    viewer.frames(70..=70).await;
    viewer.want(30, 0, 0, Direction::Forward).await;
    viewer.frames(30..=30).await;

    // With room again, a whole window fills.
    backend.videos.registry().set_allowance(256 * MIB);
    viewer.want(60, 8, 0, Direction::Forward).await;
    viewer.frames(61..=68).await;
}

#[tokio::test]
async fn windows_far_larger_than_the_video_are_served_whole() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    viewer.want(40, 500_000, 500_000, Direction::Forward).await;
    viewer.frames(0..=89).await;
}

#[tokio::test]
async fn what_cannot_be_opened_ends_the_session_with_a_reason_and_where() {
    let backend = Backend::start().await;

    let not_video = fixture("not_video.mp4");
    let mut text = backend.viewer().await;
    text.open(&backend.add(&not_video).await.ticket(), None)
        .await;
    let error = text.error().await;
    assert_eq!(error.code, ErrorCode::NotAVideo);
    assert!(!error.reason.unwrap().is_empty());
    assert_eq!(error.location, Some(not_video.display().to_string()));

    let gone = backend.folder.path().join("gone.mp4");
    std::fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &gone).unwrap();
    let id = backend.add(&gone).await;
    std::fs::remove_file(&gone).unwrap();
    let mut deleted = backend.viewer().await;
    deleted.open(&id.ticket(), None).await;
    let error = deleted.error().await;
    assert!(!error.reason.unwrap().is_empty());
    assert_eq!(error.location, Some(gone.display().to_string()));

    let mut early = backend.viewer().await;
    early.want(0, 1, 1, Direction::Forward).await;
    assert_eq!(
        early.error().await.code,
        ErrorCode::ProtocolViolation,
        "a window before Open is a protocol error"
    );
    backend.wait_for_workers(0).await;
}

#[tokio::test]
async fn a_cancelled_index_build_opens_nothing_and_saves_nothing() {
    let backend = Backend::start().await;
    let cancel = CancelToken::new();

    // The build reports 0.0 before it reads anything, so this cancels it before its first packet.
    let result = backend.videos.open(
        &VideoRef::File(fixture("cfr_h264_gop10_bf2.mp4")),
        &cancel,
        &mut |_| cancel.cancel(),
    );

    assert!(matches!(result, Err(Interrupt::Cancelled)));
    assert_eq!(backend.videos.registry().running_workers(), 0);
    let saved = std::fs::read_dir(backend.folder.path().join("cache/frame-index"))
        .map_or(0, Iterator::count);
    assert_eq!(saved, 0, "an unfinished index is not cached");
}

#[tokio::test]
async fn a_viewer_that_stops_reading_and_then_misbehaves_loses_its_socket_and_its_source() {
    let backend = Backend::start_squeezed().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    // The backend's tasks that serve a viewer are the only ones that come and go.
    let tasks = || {
        tokio::runtime::Handle::current()
            .metrics()
            .num_alive_tasks()
    };
    let idle = tasks();
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;
    // The window is far more than the two sockets hold. From here the viewer reads nothing, so the backend is
    // stuck writing a frame with the next one waiting behind it.
    viewer.want(0, 80, 0, Direction::Forward).await;
    viewer.wait_until_backed_up().await;
    assert_eq!(backend.videos.registry().running_workers(), DECODE_WORKERS);
    assert!(tasks() > idle, "the session's tasks are running");

    // The Error that would end the session has nowhere to go.
    viewer.send_garbage().await;

    // The session lets go of its source at once, and within its deadline of its socket and tasks.
    backend.wait_for_workers(0).await;
    tokio::time::timeout(Duration::from_secs(20), async {
        while tasks() > idle {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the session's tasks are reclaimed");
}

#[tokio::test]
async fn a_file_that_changed_while_it_was_indexed_fails_the_whole_source_naming_the_file() {
    let backend = Backend::start().await;
    let video = backend.folder.path().join("changing.mp4");
    std::fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &video).unwrap();
    let videos = backend.videos.clone();
    let path = video.clone();

    // The index build reports its start after it took the file's identity: touching the file then makes every
    // decoder the source opens afterwards refuse it.
    let mut session = tokio::task::spawn_blocking(move || {
        let video = VideoRef::File(path.clone());
        videos.open(&video, &CancelToken::new(), &mut |_| {
            let touched = std::time::SystemTime::now() + Duration::from_secs(60);
            let file = std::fs::File::options().write(true).open(&path).unwrap();
            file.set_modified(touched).unwrap();
        })
    })
    .await
    .unwrap()
    .unwrap_or_else(|_| panic!("the source opens"))
    .session;
    session.want(Want {
        playhead: 0,
        ahead: 4,
        behind: 0,
        direction: playback::Direction::Forward,
        priority: playback::Priority::default(),
    });

    let delivery = tokio::time::timeout(Duration::from_secs(20), session.next_delivery())
        .await
        .expect("the failure is delivered");
    let Some(Delivery::Failed(error)) = delivery else {
        panic!("expected the source to fail, got {delivery:?}");
    };
    assert!(!error.reason.is_empty());
    assert_eq!(error.location, Some(HostPath::new(&video).into()));
    assert!(
        session.next_delivery().await.is_none(),
        "nothing follows it"
    );
    drop(session);
    backend.wait_for_workers(0).await;
}

#[tokio::test]
async fn opening_again_after_the_recording_changed_describes_the_recording_now_at_the_path() {
    let backend = Backend::start().await;
    let video = backend.folder.path().join("clip.mp4");
    std::fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &video).unwrap();
    let id = backend.add(&video).await;
    let mut first = backend.viewer().await;
    first.open(&id.ticket(), None).await;
    assert_eq!(first.opened().await.frame_count, 90);
    first.send(&ClientMessage::Close).await;
    backend.wait_for_workers(0).await;

    std::fs::copy(fixture("long_gop_h264.mp4"), &video).unwrap();
    let mut second = backend.viewer().await;
    second.open(&id.ticket(), None).await;
    let described = second.opened().await;

    assert_eq!(described.frame_count, 180);
    assert_eq!(described.times.len(), 180);
    second.frames(0..=0).await;
}

#[tokio::test]
async fn a_ticket_opens_one_stream_and_a_watchers_stream_ends_when_sharing_stops() {
    let backend = Backend::start().await;
    let video = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let ticket = video.ticket();
    let mut owner = backend.viewer().await;
    owner.open(&ticket, None).await;
    owner.opened().await;
    owner.frames(0..=0).await;

    // A ticket works once, and one that was never issued works never; neither says why.
    for presented in [ticket.as_str(), "not-a-ticket"] {
        let mut refused = backend.viewer().await;
        refused.open(presented, None).await;
        assert!(matches!(refused.next().await, Event::Closed));
        assert_eq!(refused.close_code, Some(4401), "{presented}");
    }

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
    watcher.frames(0..=0).await;

    video
        .owner
        .command(Command::Control(Control::Stop))
        .unwrap();
    while !matches!(watcher.next().await, Event::Closed) {}
    assert_eq!(watcher.close_code, Some(4403));
    // The owner's own stream goes on.
    owner.want(30, 3, 0, Direction::Forward).await;
    owner.frames(30..=33).await;
}
