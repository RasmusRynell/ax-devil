use foundation::{FrameId, MediaTime, Timeline};
use scene::Coverage;

use crate::{
    FrameKey, KeyChunks, MatchBasis, MatchKind, MatchPolicy, SampleKeys, Selection, Sticky,
    Tolerance,
};

/// Selects the sample of an overlay file that belongs to `frame`, by the policy's [`MatchBasis`].
///
/// Under [`MatchBasis::Frame`] the file is matched by `keys.frames`, under [`MatchBasis::Time`] by `keys.times`; a
/// file without keys for the chosen basis selects [`Selection::None`].
///
/// A sample is never selected for a frame before its own key, whatever the tolerance: metadata does not appear
/// before the video reaches it. Nothing is interpolated or matched to the nearest neighbour. The function reads
/// only its arguments, allocates nothing and costs one binary search, so it is fit for the playback hot path.
///
/// # By time ([`MatchBasis::Time`])
///
/// 1. A sample at exactly `frame.timestamp` is [`MatchKind::Exact`].
/// 2. Otherwise the latest earlier sample is [`MatchKind::Tolerated`] when the policy is
///    [`Tolerance::PreviousWithin`] and it is at most that far back (inclusive).
/// 3. Otherwise the same latest earlier sample is [`MatchKind::Retained`] when sticky keeps it, see below.
/// 4. Otherwise [`Selection::None`], also when the frame is before the first sample.
///
/// A tolerated sample is drawn fully opaque, but an enabled sticky timeout still bounds it. `coverage` is ignored: a
/// file matched by time says nothing about frames it has no sample for.
///
/// # By frame ([`MatchBasis::Frame`])
///
/// 1. The key equal to `frame.sequence_id` is [`MatchKind::Exact`]. The tolerance does not apply; frame numbers are
///    not times.
/// 2. Otherwise, for [`Coverage::Dense`], [`Selection::Empty`]: the file covers every frame and has nothing here.
///    Sticky never retains a sample across such a frame, so an empty frame shows no stale boxes.
/// 3. Otherwise ([`Coverage::Sparse`]) the latest earlier key is [`MatchKind::Retained`] when sticky keeps it.
/// 4. Otherwise [`Selection::None`].
///
/// # Sticky
///
/// With [`Sticky::enabled`], the latest sample before the frame stays on screen while its age is within
/// [`Sticky::timeout`] (inclusive; no limit for `None`), kept as [`MatchKind::Retained`]. The age is
/// `frame.timestamp` minus the sample's time: the key itself for a time-keyed file, and the time the video's
/// `timeline` gives the key's frame for a match by frame, never a frame number read as a time. A key the timeline
/// does not reach is rejected. The age is measured from the sample, so the result does not depend on which frames
/// were selected before. Tolerance does not limit sticky, and the timeout also expires a tolerated sample.
pub fn select(
    keys: &SampleKeys,
    coverage: Coverage,
    frame: FrameId,
    timeline: &Timeline,
    policy: &MatchPolicy,
) -> Selection {
    let selected = match policy.basis.unwrap_or_default() {
        MatchBasis::Frame => keys
            .frames
            .as_ref()
            .map(|keys| by_frame(keys, coverage, frame, timeline, policy.sticky)),
        MatchBasis::Time => keys
            .times
            .as_ref()
            .map(|keys| by_time(keys, frame.timestamp, policy)),
    };
    selected.unwrap_or(Selection::None)
}

fn by_frame(
    keys: &KeyChunks<FrameKey>,
    coverage: Coverage,
    frame: FrameId,
    timeline: &Timeline,
    sticky: Sticky,
) -> Selection {
    // The latest key at or before the frame, if any.
    let latest = keys
        .partition_point(|key| key.frame <= frame.sequence_id)
        .checked_sub(1)
        .and_then(|rank| keys.get(rank));
    let Some(FrameKey {
        frame: key,
        sample: index,
    }) = latest
    else {
        return missing(coverage);
    };
    let index = index as usize;
    if key == frame.sequence_id {
        return sample(index, MatchKind::Exact);
    }
    if coverage == Coverage::Dense {
        return Selection::Empty;
    }
    match timeline.time(key) {
        Some(time) => retain(sticky, index, age(frame.timestamp, time)),
        None => Selection::None,
    }
}

fn by_time(keys: &KeyChunks<MediaTime>, time: MediaTime, policy: &MatchPolicy) -> Selection {
    let latest = keys
        .partition_point(|&key| key <= time)
        .checked_sub(1)
        .and_then(|index| Some((index, keys.get(index)?)));
    let Some((index, key)) = latest else {
        return Selection::None;
    };
    let age = age(time, key);
    if age == 0 {
        return sample(index, MatchKind::Exact);
    }
    if let Tolerance::PreviousWithin(tolerance) = policy.tolerance
        && age <= tolerance.0
        && (!policy.sticky.enabled || young(policy.sticky, age))
    {
        return sample(index, MatchKind::Tolerated);
    }
    retain(policy.sticky, index, age)
}

/// Whether a sample `age` microseconds old is within the sticky timeout (inclusive; no limit for `None`).
fn young(sticky: Sticky, age: i64) -> bool {
    age >= 0 && sticky.timeout.is_none_or(|timeout| age <= timeout.0)
}

/// What a frame with no sample of its own shows when the file does not cover it.
fn missing(coverage: Coverage) -> Selection {
    match coverage {
        Coverage::Dense => Selection::Empty,
        Coverage::Sparse => Selection::None,
    }
}

/// The sample at `index`, `age` microseconds old, kept by sticky if it is on and the sample is young enough.
fn retain(sticky: Sticky, index: usize, age: i64) -> Selection {
    if !sticky.enabled || !young(sticky, age) {
        return Selection::None;
    }
    sample(index, MatchKind::Retained)
}

fn sample(index: usize, kind: MatchKind) -> Selection {
    Selection::Sample { index, kind }
}

/// Microseconds from `then` to `now`, saturating.
fn age(now: MediaTime, then: MediaTime) -> i64 {
    now.0.saturating_sub(then.0)
}

#[cfg(test)]
mod tests;
