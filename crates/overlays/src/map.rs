//! The Frame Map cell: which sample a lane draws on a frame. The viewer's maps and an export both come from
//! [`cell`], so the two never disagree.

use foundation::Timeline;
use overlay_sync::{MatchPolicy, Selection, select};
use scene::Coverage;
use scene_store::{Track, TrackSnapshot};

use crate::window::{EMPTY_CELL, NONE_CELL};

/// The Frame Map cell of `frame` for a lane reading `snapshot` of `track` under `policy`, and the `MATCH_*` byte of how
/// it matched.
///
/// The cell is the sample's index, [`EMPTY_CELL`] when a dense file covers the frame with nothing, or
/// [`NONE_CELL`] when nothing belongs to it (also a frame past the end of the timeline, and a frame the track's
/// ingest has not decided yet).
pub fn cell(
    track: &Track,
    snapshot: &TrackSnapshot,
    timeline: &Timeline,
    frame: u64,
    policy: &MatchPolicy,
) -> (u32, u8) {
    cell_in(snapshot, track.coverage(), timeline, frame, policy).unwrap_or((NONE_CELL, 0))
}

/// [`cell`] under one `snapshot` of the track, or `None` while the snapshot does not decide the frame: a sample still
/// to come could change what it shows. Frames are decided in order, so the decided ones are a prefix of the video.
pub(crate) fn cell_in(
    snapshot: &TrackSnapshot,
    coverage: Coverage,
    timeline: &Timeline,
    frame: u64,
    policy: &MatchPolicy,
) -> Option<(u32, u8)> {
    let Some(id) = timeline.frame(frame) else {
        return Some((NONE_CELL, 0));
    };
    if !snapshot.decides(id) {
        return None;
    }
    Some(
        match select(&snapshot.keys, coverage, id, timeline, policy) {
            Selection::None => (NONE_CELL, 0),
            Selection::Empty => (EMPTY_CELL, 0),
            Selection::Sample { index, kind } => {
                (u32::try_from(index).unwrap_or(NONE_CELL), kind as u8)
            }
        },
    )
}
