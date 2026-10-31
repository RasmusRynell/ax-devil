//! The pure planner: which keyframe-group spans to decode, in which order.

use crate::window::prefetch_windows;
use crate::{FrameSet, SourceSpec};
use foundation::Keyframes;
use std::collections::BTreeMap;
use std::ops::RangeInclusive;

/// Which way the viewer is travelling; decides what is nearest and what is fetched first.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    /// Towards later frames.
    Forward,
    /// Towards earlier frames.
    Backward,
}

/// What one viewer needs around its playhead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Want {
    /// The frame the viewer is on.
    pub playhead: u64,
    /// Frames its window keeps after the playhead.
    pub ahead: u32,
    /// Frames its window keeps before the playhead.
    pub behind: u32,
    /// The direction of travel.
    pub direction: Direction,
}

/// One decode job: start at `keyframe` (or read on from the decoder's position), cache the frames of `keep`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Span {
    /// Where decoding can start.
    pub keyframe: u64,
    /// The frames to cache, all inside the keyframe's group.
    pub keep: RangeInclusive<u64>,
}

/// Lower is more urgent: the playhead, then the direction of travel nearest first, then the other side.
pub(crate) type Rank = (u8, u64);

pub(crate) fn rank(direction: Direction, playhead: u64, frame: u64) -> Rank {
    let distance = frame.abs_diff(playhead);
    if distance == 0 {
        (0, 0)
    } else if (frame > playhead) == (direction == Direction::Forward) {
        (1, distance)
    } else {
        (2, distance)
    }
}

/// The frame of `range` nearest `playhead`.
pub(crate) fn nearest(range: &RangeInclusive<u64>, playhead: u64) -> u64 {
    playhead.clamp(*range.start(), *range.end())
}

/// Whether a decoder at `position` should read on to `first` instead of seeking to its keyframe: it must not be
/// past it, and reading on must not decode a whole extra keyframe group.
pub(crate) fn reads_on(keyframes: &Keyframes, position: Option<u64>, first: u64) -> bool {
    let Some(position) = position else {
        return false;
    };
    if position > first {
        return false;
    }
    let target_key = keyframes.at_or_before(first);
    if position >= target_key {
        return true;
    }
    let position_key = keyframes.at_or_before(position);
    position > position_key && keyframes.after(position_key) == Some(target_key)
}

struct Group {
    first: u64,
    last: u64,
    rank: Rank,
}

/// The spans that should exist now, most urgent first. For every want, the uncached frames of its prefetch window
/// (the send window scaled to `budget_frames`, shared equally among the wants) are grouped by keyframe group; each
/// group with missing frames gives one span from its first to its last missing frame, so a group is never decoded
/// twice for one pass. `held` is every frame that needs no decoding (cached, failed or already delivered).
/// `in_flight` spans win ties, so equal work is never swapped between workers.
pub(crate) fn plan(
    spec: &SourceSpec,
    wants: &[Want],
    budget_frames: usize,
    held: &FrameSet,
    in_flight: &[Span],
) -> Vec<Span> {
    let last = spec.timeline.last();
    let mut groups: BTreeMap<u64, Group> = BTreeMap::new();
    for (want, window) in wants
        .iter()
        .zip(prefetch_windows(spec, wants, budget_frames))
    {
        let playhead = want.playhead.min(last);
        for gap in held.gaps_in(window) {
            let mut start = *gap.start();
            while start <= *gap.end() {
                let keyframe = spec.keyframes.at_or_before(start);
                let end = (*gap.end()).min(spec.keyframes.group_end(keyframe));
                let rank = rank(want.direction, playhead, nearest(&(start..=end), playhead));
                groups
                    .entry(keyframe)
                    .and_modify(|group| {
                        group.first = group.first.min(start);
                        group.last = group.last.max(end);
                        group.rank = group.rank.min(rank);
                    })
                    .or_insert(Group {
                        first: start,
                        last: end,
                        rank,
                    });
                start = end + 1;
            }
        }
    }
    let mut ranked: Vec<_> = groups
        .into_iter()
        .map(|(keyframe, group)| {
            let flying = in_flight.iter().any(|span| span.keyframe == keyframe);
            let key = (group.rank, !flying, keyframe);
            let span = Span {
                keyframe,
                keep: group.first..=group.last,
            };
            (key, span)
        })
        .collect();
    ranked.sort_by_key(|(key, _)| *key);
    ranked.into_iter().map(|(_, span)| span).collect()
}
