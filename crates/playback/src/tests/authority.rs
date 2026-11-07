use crate::Direction::Forward;
use crate::testing::want;
use crate::{Authority, ClockIntent, Priority, clamp_demand};
use foundation::{MediaTime, Timeline};

/// 1000 frames at 100 fps: frame n is at n * 10 ms, so a second is 100 frames.
fn video() -> Timeline {
    Timeline::new((0..1000).map(|n| MediaTime::from_us(n * 10_000)).collect()).unwrap()
}

fn follow(frame: u64, playing: bool, speed_centi: u32) -> Authority {
    Authority::Follow {
        intent: ClockIntent {
            frame,
            playing,
            speed_centi,
            at_us: 5_000_000,
        },
    }
}

fn playhead(authority: &Authority, now_us: i64, ahead: u32, asked: u64) -> Option<u64> {
    clamp_demand(authority, &video(), now_us, want(asked, ahead, 0, Forward)).map(|w| w.playhead)
}

/// The first and last frame the holder would be sent when it asks for `behind` and `ahead` frames around `asked`.
fn delivered(
    authority: &Authority,
    now_us: i64,
    asked: u64,
    ahead: u32,
    behind: u32,
) -> (u64, u64) {
    let w = clamp_demand(
        authority,
        &video(),
        now_us,
        want(asked, ahead, behind, Forward),
    )
    .unwrap();
    (
        w.playhead.saturating_sub(u64::from(w.behind)),
        (w.playhead + u64::from(w.ahead)).min(video().last()),
    )
}

#[test]
fn an_owner_goes_anywhere_in_the_video_and_a_revoked_stream_nowhere() {
    let own = Authority::Own {
        priority: Priority::High,
    };
    assert_eq!(playhead(&own, 0, 4, 40_000), Some(999));
    assert_eq!(playhead(&own, 0, 4, 250), Some(250));
    let stamped = clamp_demand(&own, &video(), 0, want(7, 4, 0, Forward)).unwrap();
    assert_eq!(
        stamped.priority,
        Priority::High,
        "the holder cannot claim its own priority"
    );
    assert_eq!(playhead(&Authority::Revoked, 0, 4, 250), None);
    // A window is bounded whatever is asked, yet a video still fits in one.
    let huge = clamp_demand(&own, &video(), 0, want(7, u32::MAX, u32::MAX, Forward)).unwrap();
    assert!((1_000..=1_000_000).contains(&huge.ahead), "{}", huge.ahead);
    assert!(
        (1_000..=1_000_000).contains(&huge.behind),
        "{}",
        huge.behind
    );
}

#[test]
fn a_follower_buffers_around_the_presenter_but_cannot_scrub() {
    // Playing from frame 100 at real time: one second after the intent the presenter is on frame 200.
    let playing = follow(100, true, 100);
    let now = 6_000_000;
    assert_eq!(
        playhead(&playing, now, 4, 0),
        Some(100),
        "at most a second behind"
    );
    assert_eq!(
        playhead(&playing, now, 4, 500),
        Some(400),
        "at most two seconds ahead"
    );
    assert_eq!(
        playhead(&playing, now, 4, 250),
        Some(250),
        "inside the window it is left alone"
    );
    assert_eq!(
        playhead(&playing, now, u32::MAX, 999),
        Some(400),
        "what the follower asks to keep ahead does not move its window"
    );
    // At double speed the presenter is on frame 300 and two seconds of video last one second.
    assert_eq!(
        playhead(&follow(100, true, 200), now, 4, 999),
        Some(300 + 400)
    );

    // Paused, the presenter's frame is the requested one whatever the time: four frames either way.
    let paused = follow(100, false, 100);
    assert_eq!(playhead(&paused, now, 4, 0), Some(96));
    assert_eq!(playhead(&paused, now + 60_000_000, 4, 999), Some(104));
    assert_eq!(playhead(&paused, now, 4, 101), Some(101));
}

#[test]
fn nothing_a_follower_asks_delivers_frames_outside_the_presenters_window() {
    let now = 6_000_000;
    // (authority, the first and last frame it may be sent)
    let windows = [
        (follow(100, true, 100), (100, 400)),
        (follow(100, true, 200), (200, 700)),
        (follow(100, true, 800), (800, 999)),
        (follow(100, false, 100), (96, 104)),
        (follow(990, false, 100), (986, 999)),
    ];
    for (authority, (first, last)) in windows {
        for asked in [0, 100, 250, 999, u64::MAX] {
            for (ahead, behind) in [
                (0, 0),
                (4, 4),
                (u32::MAX, 0),
                (0, u32::MAX),
                (u32::MAX, u32::MAX),
            ] {
                let (from, to) = delivered(&authority, now, asked, ahead, behind);
                assert!(
                    first <= from && to <= last,
                    "{authority:?} asked {asked} +{ahead} -{behind} gets {from}..={to}, not inside {first}..={last}"
                );
            }
        }
    }
    // The whole window is reachable by asking for all of it.
    let (authority, (first, last)) = windows[0];
    assert_eq!(
        delivered(&authority, now, 250, u32::MAX, u32::MAX),
        (first, last)
    );
}
