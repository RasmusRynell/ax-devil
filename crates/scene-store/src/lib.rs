//! Overlay data on disk and in memory: decode an overlay file once, keep every Scene in a private artifact, and hand
//! out Scenes by sample index from a bounded cache.
//!
//! [`TrackRegistry::open`] gives the [`Track`] of one overlay file for one video frame size and shares it while
//! anyone holds it. The artifact format and the memory bound are in the crate README.

mod format;
mod io;
mod load;
mod lru;
mod registry;
mod stat;
mod summary;
mod track;
mod writer;

#[cfg(test)]
mod tests;

pub use format::STORE_FORMAT_VERSION;
pub use registry::{SCENE_CACHE_BYTES, TrackKey, TrackRegistry};
pub use track::Track;
