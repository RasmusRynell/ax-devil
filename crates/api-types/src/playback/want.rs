//! Wire types of what a viewer asks of the video: its direction, the frames it holds and how frame times follow
//! from the timestamps.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// How a viewer steps through the video.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    /// Playing or stepping forward.
    Forward,
    /// Stepping or scrubbing backward.
    Backward,
}

/// The frames a viewer holds or wants: its window around the playhead. Each want replaces the viewer's previous one;
/// the backend works on what is nearest the newest playhead first.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct Want {
    /// The sequence id the viewer shows or is about to.
    #[ts(type = "number")]
    pub playhead: u64,
    /// How many frames after the playhead the viewer keeps.
    pub ahead: u32,
    /// How many frames before the playhead the viewer keeps.
    pub behind: u32,
    /// Which way the viewer is travelling, which decides what to prefetch first.
    pub direction: Direction,
    /// How many frames after the playhead the viewer keeps Frame Maps and Sample Items for.
    pub data_ahead: u32,
    /// How many frames before the playhead the viewer keeps Frame Maps and Sample Items for.
    pub data_behind: u32,
}

/// How the video's frame times come from its timestamps, when its container has them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct VideoPtsWire {
    /// The stream's time base as numerator and denominator, seconds per tick.
    pub time_base: [i32; 2],
    /// The first frame's timestamp in ticks.
    #[ts(type = "number")]
    pub first_pts: i64,
}
