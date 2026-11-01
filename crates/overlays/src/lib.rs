//! The overlay pipeline of a viewer: one lane per overlay file, each turning the Scene of a video frame into Drawing
//! Instructions for the user's catalog and filters.
//!
//! [`OverlayService::open_viewer`] gives a [`ViewerOverlays`]. The viewer follows the video's window ([`Want`]): for
//! every shown lane and every frame in the window it looks the sample up ([`overlay_sync::select`]), filters its
//! Scene and draws it with the lane's catalog, once per residency of the frame in the window and once per view
//! revision. [`prepare_lane_frame`] is that step on its own, which export reuses.
//!
//! One worker thread prepares frames and compiles catalogs; one thread per overlay file loads its track. The state
//! they share is bounded by construction (see `state`): a want updates the window in place and the worker derives
//! its next job from it, so there is no queue of wants or frames to grow. A lane whose catalog cannot be drawn still
//! sends empty drawings for its frames, and a failed lane sends nothing, so neither holds the video back.
//!
//! [`Want`]: playback::Want

mod lane;
mod prepare;
mod service;
mod sources;
mod state;
mod viewer;
mod worker;

#[cfg(test)]
mod tests;

pub use lane::{LaneOverlay, LaneReady, LaneStatus, LaneView, OverlayEvent, TargetSize};
pub use prepare::prepare_lane_frame;
pub use service::OverlayService;
pub use viewer::ViewerOverlays;
