//! Wire types for what a workspace item resolved to.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Content by kind. Viewers register by kind.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum ContentInfo {
    /// A recorded video, with the overlays added to it.
    SeekableVideo {
        /// Names the Content; the playback socket opens it by this id.
        id: String,
        /// The name the viewer shows.
        display_name: String,
        /// The one entry: the video and its lanes.
        entry: EntryInfo,
    },
    /// Several videos in order.
    Playlist {
        /// Names the Content.
        id: String,
        /// The name the viewer shows.
        display_name: String,
        /// The entries in playlist order.
        entries: Vec<EntryInfo>,
    },
}

/// One video and its lanes: what a viewer opens.
#[derive(Serialize, Deserialize, TS)]
pub struct EntryInfo {
    /// Names the entry; the playback socket opens it by this id.
    pub id: String,
    /// The name rows and viewers show.
    pub label: String,
    /// The lane names, in order.
    pub lanes: Vec<String>,
}
