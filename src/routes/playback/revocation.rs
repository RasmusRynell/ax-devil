//! A revoked or narrowed stream stops at once: while the video is being indexed, with a message queued for the socket,
//! and for the frames, Sample Items and Frame Maps its viewer still has coming without asking again.

use super::*;
use foundation::{CancelToken, MediaTime, Timeline};
use futures_util::{sink, stream as streams};
use overlays::{MapRun, OverlayEvent};
use playback::{Authority, ClockIntent};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicI64, Ordering},
};
use tokio::sync::Semaphore;

const WAIT: Duration = Duration::from_secs(20);
/// A tenth of the viewer's default 512 MiB frame budget.
const SAMPLE_BUDGET: u64 = 53_687_091;

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
            inspection: None,
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

mod follower;
