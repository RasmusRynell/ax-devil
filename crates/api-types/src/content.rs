//! Wire types for what a workspace item resolved to.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Content by kind. Viewers register by kind.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "kind")]
pub enum ContentInfo {
    /// A recorded video, with the overlays added to it.
    SeekableVideo {
        /// Names the Content; a ticket request names the stream by this id, `generation` and the entry's id.
        id: String,
        /// The commit of the Content's resolution. Re-resolving or reopening the item gives a new one, which makes
        /// tickets for the old one worthless.
        #[ts(type = "number")]
        generation: u64,
        /// The name the viewer shows.
        display_name: String,
        /// The one entry: the video and its lanes.
        entry: EntryInfo,
    },
    /// Several videos in order.
    Playlist {
        /// Names the Content.
        id: String,
        /// The commit of the Content's resolution, as for a video.
        #[ts(type = "number")]
        generation: u64,
        /// The name the viewer shows.
        display_name: String,
        /// The entries in playlist order.
        entries: Vec<EntryInfo>,
    },
}

/// One video and its lanes: what a viewer opens.
#[derive(Serialize, Deserialize, TS)]
pub struct EntryInfo {
    /// Names the entry; a ticket request names it by this id. A video's one entry has the Content's id.
    pub id: String,
    /// The name rows and viewers show.
    pub label: String,
    /// The lane names, in order.
    pub lanes: Vec<String>,
}
