//! Overlay data on disk and in memory: decode an overlay file once, keep every Scene in a private artifact, and hand
//! out Scenes by sample index from a bounded cache.
//!
//! [`TrackRegistry::open`] gives the [`Track`] of one overlay file and shares it while
//! anyone holds it. A track can be read while its file is still being decoded; [`TrackRead::snapshot`] says how much is
//! final. The artifact format, the ingest rules and the memory bound are in the crate README.

mod format;
mod group;
mod history;
mod ingest;
mod input;
mod io;
mod keylog;
mod link;
mod live;
mod load;
mod lru;
mod output;
mod placement;
mod query;
mod read;
mod registry;
mod settle;
mod settled;
mod sort;
mod source;
mod stat;
mod summary;
mod track;
mod warnings;
mod writer;

#[cfg(test)]
mod tests;

pub use format::STORE_FORMAT_VERSION;
pub use history::{EventRef, History, HistoryUnavailable};
pub use live::{Feed, LiveTrack};
pub use placement::Placement;
pub use query::{Matches, Op, Query, Row, Search, Sort, SortKey, Term, appearances};
pub use read::TrackRead;
pub use registry::{SCENE_CACHE_BYTES, SCENE_EXTENSION, SCENE_FOLDER, TrackKey, TrackRegistry};
pub use track::{Decided, Ingest, Track, TrackSnapshot};
