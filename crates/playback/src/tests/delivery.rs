use crate::Direction::{Backward, Forward};
use crate::testing::{Fixture, deliveries, poll_now, want};
use crate::{Delivery, rank};
use foundation::{ErrorCode, UserError};

fn frames(ids: &[u64]) -> Vec<(u64, bool)> {
    ids.iter().map(|&id| (id, false)).collect()
}

#[test]
fn delivery_is_playhead_first_then_nearest_in_the_direction_of_travel() {
    let mut fx = Fixture::new(60, 10, 200);
    let mut session = fx.session();
    session.want(want(10, 3, 2, Forward));
    fx.drain();
    assert_eq!(deliveries(&mut session), frames(&[10, 11, 12, 13, 9, 8]));
    assert!(
        deliveries(&mut session).is_empty(),
        "everything in the window was sent"
    );

    session.want(want(30, 1, 3, Backward));
    fx.drain();
    assert_eq!(deliveries(&mut session), frames(&[30, 29, 28, 27, 31]));
}

#[test]
fn next_rank_is_the_rank_of_the_frame_the_next_delivery_returns_and_sends_nothing() {
    let mut fx = Fixture::new(60, 10, 200);
    let mut session = fx.session();
    assert_eq!(session.next_rank(), None, "no want yet");
    session.want(want(10, 3, 2, Forward));
    fx.drain();
    for _ in 0..6 {
        let ranked = session.next_rank();
        assert_eq!(ranked, session.next_rank(), "peeking does not send");
        let Some(Some(Delivery::Frame(frame))) = poll_now(session.next_delivery()) else {
            panic!("a frame is waiting");
        };
        assert_eq!(
            ranked,
            Some(rank(Forward, 10, frame.id().sequence_id)),
            "frame {}",
            frame.id().sequence_id
        );
    }
    assert_eq!(session.next_rank(), None, "the window was sent");
}

#[test]
fn a_frame_is_sent_once_per_window_residency_and_again_after_it_re_enters() {
    let mut fx = Fixture::new(60, 10, 200);
    let mut session = fx.session();
    session.want(want(10, 3, 2, Forward));
    fx.drain();
    assert_eq!(deliveries(&mut session).len(), 6);

    session.want(want(11, 3, 2, Forward));
    fx.drain();
    assert_eq!(
        deliveries(&mut session),
        frames(&[14]),
        "only the frame that entered"
    );

    session.want(want(40, 3, 2, Forward));
    fx.drain();
    assert_eq!(deliveries(&mut session), frames(&[40, 41, 42, 43, 39, 38]));

    session.want(want(10, 3, 2, Forward));
    assert_eq!(
        deliveries(&mut session),
        frames(&[10, 11, 12, 13, 9, 8]),
        "resent from the cache, with nothing to decode"
    );
}

#[test]
fn a_failed_frame_is_reported_alone_and_never_decoded_again() {
    let mut fx = Fixture::new(60, 10, 200);
    crate::lock(&fx.log.failing)
        .insert(12, UserError::new(ErrorCode::DecodeFailed, "corrupt data"));
    let mut session = fx.session();
    session.want(want(10, 5, 0, Forward));
    fx.drain();

    assert_eq!(
        deliveries(&mut session),
        [
            (10, false),
            (11, false),
            (12, true),
            (13, false),
            (14, false),
            (15, false)
        ]
    );
    session.want(want(10, 5, 0, Forward));
    fx.drain();
    assert_eq!(fx.log.decoded().iter().filter(|&&id| id == 12).count(), 1);
}

#[test]
fn a_frame_larger_than_the_share_is_delivered_without_being_cached() {
    let mut fx = Fixture::with_allowance(60, 10, 50);
    let mut session = fx.session();
    session.want(want(3, 2, 0, Forward));
    fx.drain();
    assert_eq!(deliveries(&mut session), frames(&[3]));
    session.want(want(4, 2, 0, Forward));
    fx.drain();
    assert_eq!(deliveries(&mut session), frames(&[4]));

    assert_eq!(fx.source.cache.used_bytes(), 0);
    assert_eq!(
        fx.log.decoded(),
        [0, 1, 2, 3, 4],
        "each frame decoded once, none re-planned"
    );
}

#[test]
fn two_sessions_share_one_cache_share_and_each_get_their_own_frames() {
    let mut fx = Fixture::new(120, 10, 24);
    let mut first = fx.session();
    let mut second = fx.session();
    first.want(want(10, 20, 3, Forward));
    second.want(want(80, 20, 3, Forward));
    fx.drain();

    assert_eq!(
        fx.cached().ranges(),
        [9..=20, 79..=90],
        "one share, split between the two"
    );
    assert!(fx.source.cache.used_bytes() <= fx.source.cache.budget_bytes());
    let got_first = deliveries(&mut first);
    let got_second = deliveries(&mut second);
    assert_eq!(got_first[0], (10, false));
    assert_eq!(got_second[0], (80, false));
    assert!(got_first.iter().all(|(id, _)| (9..=20).contains(id)));
    assert!(got_second.iter().all(|(id, _)| (79..=90).contains(id)));

    drop(first);
    fx.drain();
    assert!(
        fx.cached().gaps_in(80..=99).is_empty(),
        "the remaining viewer gets the whole share"
    );
}

#[test]
fn every_viewers_playhead_is_delivered_even_when_the_share_cannot_hold_them_all() {
    let mut fx = Fixture::new(120, 10, 2);
    let mut sessions: Vec<_> = (0..3).map(|_| fx.session()).collect();
    for (session, playhead) in sessions.iter().zip([15, 55, 95]) {
        session.want(want(playhead, 0, 0, Forward));
    }
    fx.drain();

    for (session, playhead) in sessions.iter_mut().zip([15, 55, 95]) {
        assert_eq!(
            deliveries(session),
            frames(&[playhead]),
            "viewer at {playhead}"
        );
    }
    assert!(fx.source.cache.used_bytes() <= fx.source.cache.budget_bytes());
}

#[test]
fn a_live_allowance_reduction_still_delivers_every_viewers_playhead() {
    let mut fx = Fixture::new(120, 10, 3);
    let mut sessions: Vec<_> = (0..3).map(|_| fx.session()).collect();
    for (session, playhead) in sessions.iter().zip([15, 55, 95]) {
        session.want(want(playhead, 0, 0, Forward));
    }
    fx.pool.set_allowance(200);
    fx.source.poke();
    fx.drain();

    for (session, playhead) in sessions.iter_mut().zip([15, 55, 95]) {
        assert_eq!(
            deliveries(session),
            frames(&[playhead]),
            "viewer at {playhead}"
        );
    }
    assert!(fx.source.cache.used_bytes() <= 200);
}

#[test]
fn a_viewer_waiting_on_an_uncached_frame_does_not_make_others_decode_it_again() {
    let mut fx = Fixture::new(120, 10, 2);
    let mut sessions: Vec<_> = (0..3).map(|_| fx.session()).collect();
    for (session, playhead) in sessions.iter().zip([15, 55, 95]) {
        session.want(want(playhead, 0, 0, Forward));
    }
    fx.drain();
    for session in &mut sessions {
        deliveries(session);
    }
    let decoded = fx.log.decoded().len();

    // A new want from another viewer replans; the paused viewer's delivered frame is not decoded for again.
    sessions[0].want(want(15, 0, 0, Forward));
    fx.drain();
    assert_eq!(fx.log.decoded().len(), decoded);
}

#[test]
fn viewers_that_stopped_reading_never_hold_back_the_others() {
    // One frame of cache, six viewers: five frames wait for their viewers in their own slots.
    let mut fx = Fixture::new(120, 10, 1);
    let mut sessions: Vec<_> = (0..6).map(|_| fx.session()).collect();
    let playheads = [15, 35, 55, 75, 95, 115];
    for (session, playhead) in sessions.iter().zip(playheads) {
        session.want(want(playhead, 0, 0, Forward));
    }
    fx.drain();

    let last = sessions.len() - 1;
    assert_eq!(
        deliveries(&mut sessions[last]),
        frames(&[115]),
        "the one reader gets its frame"
    );
    let decoded = fx.log.decoded().len();

    // The others had stopped reading; when they do read, nothing was lost, and nothing is decoded again.
    for (session, playhead) in sessions.iter_mut().zip(playheads).take(last) {
        assert_eq!(
            deliveries(session),
            frames(&[playhead]),
            "viewer at {playhead}"
        );
    }
    assert_eq!(fx.log.decoded().len(), decoded);
}

#[test]
fn a_viewer_with_every_slot_full_does_not_stop_decoding_for_another() {
    // One frame of cache. Viewer one wants 12..=17 but its window is 12 alone; viewer two is at 18 in the same
    // group, so the frames between pass through the decoder and viewer one's slots fill up.
    let mut fx = Fixture::new(120, 10, 1);
    let mut stalled = fx.session();
    let mut reader = fx.session();
    stalled.want(want(12, 5, 0, Forward));
    reader.want(want(18, 0, 0, Forward));
    fx.drain();

    assert_eq!(
        crate::lock(&fx.source.state).sessions[&0].parked.len(),
        crate::source::PARKED_SLOTS,
        "the stalled viewer's slots are full"
    );
    assert_eq!(deliveries(&mut reader), frames(&[18]));
    let decoded = fx.log.decoded().len();
    reader.want(want(18, 0, 0, Forward));
    fx.drain();
    assert_eq!(
        fx.log.decoded().len(),
        decoded,
        "the stalled viewer's frames are not decoded again"
    );

    let got = deliveries(&mut stalled);
    assert_eq!(got[0], (12, false));
    assert!(got.len() <= 1 + crate::source::PARKED_SLOTS + 1, "{got:?}");
}
