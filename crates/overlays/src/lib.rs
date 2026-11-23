//! The overlay data feed of a viewer: it loads each overlay file once and sends what the frontend needs to draw
//! it, as Sample Items and Frame Maps. It never evaluates a catalog, filters for display or draws; presentation is
//! `frontend/src/present/`.
//!
//! [`OverlayService::open_viewer`] gives a [`ViewerOverlays`]. Lanes on the same file share a source: its track is
//! loaded once and each of its samples is encoded once (`scene-store` caches the encoding across viewers) and sent
//! once. A lane adds only its Frame Map, which `scene::matching` selects under the lane's [`LanePolicy`]. The viewer
//! follows the data window of the video's [`Want`]: maps and samples go only inside it, the samples as
//! [`sample_window`] defines, nearest the playhead first. The viewer keeps what it was sent until told otherwise: the
//! backend sends its data window and an eviction for every sample it stops counting as held, and keeps the held
//! samples within the byte budget the viewer opened with, evicting the least urgent to make room for a more urgent
//! one.
//!
//! One thread per source loads the track and then encodes; the state they share is bounded by construction (see
//! `state`): a want updates the windows in place, the encoders derive their next sample from them, and at most four
//! encoded items per source wait for the consumer. A sample that cannot be read goes as an empty item with a
//! problem, so no lane waits for it forever; a failed source sends nothing.
//!
//! [`Want`]: playback::Want

mod data;
mod ingest;
mod lane;
mod live;
mod map;
mod pull;
mod service;
mod sources;
mod state;
mod viewer;
mod window;
mod worker;

#[cfg(test)]
mod tests;

pub use lane::{
    LanePolicy, LaneReady, LaneSnapshot, LaneStatus, MapRun, OverlayEvent, SampleItem,
    SourceSnapshot,
};
pub use live::{LIVE_KEEP_FRAMES, LiveEvents, LiveLane, LiveOut, SampleEvents};
pub use map::cell;
pub use service::OverlayService;
pub use viewer::{FileScopeBlocked, FileScopeInput, ViewerOverlays};
pub use window::{EMPTY_CELL, FIRST_NON_SAMPLE, NONE_CELL, RUN_FRAMES, sample_window};
