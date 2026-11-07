use crate::Direction::Forward;
use crate::testing::{Fixture, want};
use crate::{CachePool, FrameSet};
use crate::{SharedFrame, testing};
use std::ops::RangeInclusive;
use std::sync::Barrier;

fn video_frame(id: u64, bytes: usize) -> SharedFrame {
    testing::video_frame(id, bytes).into()
}

fn one(range: RangeInclusive<u64>) -> [RangeInclusive<u64>; 1] {
    [range]
}

#[test]
fn byte_accounting_follows_mixed_sizes_replacements_and_oversized_frames() {
    let pool = CachePool::new(400);
    let cache = pool.add_source();
    assert!(cache.insert(video_frame(0, 100)));
    assert!(cache.insert(video_frame(1, 100)));
    assert!(cache.insert(video_frame(2, 200)));
    assert_eq!(cache.used_bytes(), 400);

    assert!(
        !cache.insert(video_frame(3, 100)),
        "farther than everything held, so refused rather than evicting nearer frames"
    );
    cache.protect(3, &[]);
    assert!(cache.insert(video_frame(3, 100)));
    assert!(!cache.contains(0), "the farthest frame made room");
    assert_eq!(cache.used_bytes(), 400);

    assert!(cache.insert(video_frame(2, 100)));
    assert_eq!(cache.get(2).unwrap().reserved_bytes(), 100);
    assert_eq!(
        cache.used_bytes(),
        300,
        "a replacement releases the old frame's bytes"
    );

    assert!(!cache.insert(video_frame(9, 500)), "larger than the share");
    assert!(!cache.contains(9));
    assert_eq!(cache.used_bytes(), 300);
}

#[test]
fn protected_frames_are_never_evicted_and_the_farthest_goes_first() {
    let pool = CachePool::new(500);
    let cache = pool.add_source();
    for id in 0..5 {
        assert!(cache.insert(video_frame(id, 100)));
    }
    cache.protect(4, &one(0..=1));

    assert!(cache.insert(video_frame(5, 100)));
    assert!(!cache.contains(2), "farthest unprotected frame evicted");
    assert!(
        cache.contains(0) && cache.contains(1),
        "protected frames stay although farther"
    );
    assert!(
        !cache.insert(video_frame(6, 100)),
        "as far as the farthest evictable frame"
    );

    cache.protect(4, &one(0..=4));
    let before = cache.ranges();
    assert!(
        !cache.insert(video_frame(8, 200)),
        "cannot fit beside the protected frames"
    );
    assert_eq!(cache.ranges(), before, "a refused frame changes nothing");
    assert_eq!(before, FrameSet::from_ranges([0..=1, 3..=5]));
}

#[test]
fn shares_split_equally_and_every_change_evicts_at_once() {
    let pool = CachePool::new(600);
    let first = pool.add_source();
    for id in 0..6 {
        assert!(first.insert(video_frame(id, 100)));
    }
    let second = pool.add_source();
    assert_eq!(first.budget_bytes(), 300);
    assert_eq!(second.budget_bytes(), 300);
    assert_eq!(first.used_bytes(), 300, "joining evicts at once");
    for id in 0..3 {
        assert!(second.insert(video_frame(id, 100)));
    }
    assert!(first.used_bytes() + second.used_bytes() <= pool.allowance());

    first.protect(0, &one(0..=9));
    pool.set_allowance(200);
    assert_eq!((first.budget_bytes(), second.budget_bytes()), (100, 100));
    assert!(
        first.used_bytes() <= 100 && second.used_bytes() <= 100,
        "a shrink beats protection"
    );

    drop(second);
    assert_eq!(first.budget_bytes(), 200, "leaving returns the share");
    assert_eq!(pool.source_count(), 1);

    pool.set_allowance(0);
    assert_eq!((first.budget_bytes(), first.used_bytes()), (0, 0));
    assert!(
        !first.insert(video_frame(0, 100)),
        "a zero share holds nothing"
    );

    pool.set_allowance(600);
    first.close();
    first.close();
    assert!(!first.insert(video_frame(0, 100)));
    assert_eq!(pool.source_count(), 0);
}

#[test]
fn concurrent_inserts_resizes_joins_and_leaves_never_deadlock() {
    let pool = CachePool::new(800);
    let start = Barrier::new(4);
    std::thread::scope(|scope| {
        for _ in 0..3 {
            scope.spawn(|| {
                let cache = pool.add_source();
                start.wait();
                for round in 0..300u64 {
                    let id = round % 50;
                    cache.protect(id, &one(id..=id + 3));
                    cache.insert(video_frame(id, 100));
                    cache.get(id);
                    cache.ranges();
                }
            });
        }
        scope.spawn(|| {
            start.wait();
            for round in 0..300usize {
                pool.set_allowance((round % 9) * 100);
                drop(pool.add_source());
            }
        });
    });
    assert_eq!(pool.source_count(), 0);
}

#[test]
fn a_session_reports_the_frames_its_video_has_cached() {
    let mut fx = Fixture::new(60, 10, 200);
    let session = fx.session();
    assert_eq!(session.cached(), FrameSet::new());
    session.want(want(10, 3, 2, Forward));
    fx.drain();
    assert!(
        session.cached().contains(10),
        "the playhead was decoded into the cache"
    );
    assert_eq!(session.cached(), fx.cached());
}
