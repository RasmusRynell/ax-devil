//! Frame windows around a playhead. The send window is the viewer's own; the prefetch window is the backend's.

use crate::{Direction, SourceSpec, Want};
use std::ops::RangeInclusive;

fn to_u64(n: usize) -> u64 {
    u64::try_from(n).unwrap_or(u64::MAX)
}

/// The frames a viewer keeps: exactly `behind` before and `ahead` after the playhead, clamped to the video.
pub(crate) fn send_window(want: &Want, last: u64) -> RangeInclusive<u64> {
    let playhead = want.playhead.min(last);
    playhead.saturating_sub(u64::from(want.behind))
        ..=playhead.saturating_add(u64::from(want.ahead)).min(last)
}

/// The frames to decode and hold for each want. The send window is scaled to the cache budget, keeping its
/// ahead/behind split; the budget is shared equally by the wants, so the windows together always fit.
pub(crate) fn prefetch_windows(
    spec: &SourceSpec,
    wants: &[Want],
    budget_frames: usize,
) -> Vec<RangeInclusive<u64>> {
    let each = to_u64((budget_frames / wants.len().max(1)).max(1));
    wants
        .iter()
        .map(|want| prefetch_window(spec, want, each))
        .collect()
}

fn prefetch_window(spec: &SourceSpec, want: &Want, frames: u64) -> RangeInclusive<u64> {
    let last = spec.timeline.last();
    let playhead = want.playhead.min(last);
    let sides = u64::from(want.ahead) + u64::from(want.behind);
    if sides == 0 {
        return playhead..=playhead;
    }
    let total = frames.min(last + 1);
    let spare = total - 1;
    let ahead = (u128::from(spare) * u128::from(want.ahead) * 2 + u128::from(sides))
        / (2 * u128::from(sides));
    let mut ahead = u64::try_from(ahead).unwrap_or(spare).min(spare);
    let mut behind = spare - ahead;
    // A side that runs into the end of the video gives its frames to the other side.
    if ahead > last - playhead {
        behind += ahead - (last - playhead);
        ahead = last - playhead;
    }
    if behind > playhead {
        ahead += behind - playhead;
        behind = playhead;
    }
    let (low, high) = (playhead - behind, playhead + ahead);
    if want.direction == Direction::Backward && total >= 2 {
        align_backward(spec, playhead, low, high, total)
    } else {
        low..=high
    }
}

/// Backward stepping needs whole keyframe groups: a group decodes from its keyframe up, so a window that starts
/// mid-group would decode that group again for every step. The window therefore starts at the first keyframe at or
/// after its nominal start, within the same frame total. When the playhead's group alone is longer than the budget,
/// the window starts on a grid of `total / 2` frames from the keyframe: one partial-group decode per grid step.
fn align_backward(
    spec: &SourceSpec,
    playhead: u64,
    low: u64,
    high: u64,
    total: u64,
) -> RangeInclusive<u64> {
    let keys = &spec.keyframes;
    let start = if keys.at_or_before(low) == low {
        low
    } else {
        match keys.after(low) {
            Some(key) if key <= playhead => key,
            _ => {
                let key = keys.at_or_before(playhead);
                if playhead - key < total {
                    key
                } else {
                    let step = (total / 2).max(1);
                    key + (playhead - step - key) / step * step
                }
            }
        }
    };
    start..=high.min(start + total - 1)
}
