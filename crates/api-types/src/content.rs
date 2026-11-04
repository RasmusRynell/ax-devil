//! Wire types for what a workspace item resolved to.

use crate::live::{LiveOverlayKind, NewLiveWire};
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
        /// The entries of every playlist, in order.
        entries: Vec<EntryInfo>,
        /// The playlists, each covering the next run of `entries`; at least one.
        playlists: Vec<PlaylistSpanInfo>,
    },
    /// A camera's live stream. It is one video with one lane, and a ticket request names its stream by `id` as both
    /// the Content and the entry. Connection details only in `recipe`, which an audience without host details never
    /// gets.
    LiveVideo {
        /// Names the Content and its one stream.
        id: String,
        /// The commit of the Content's resolution, as for a video.
        #[ts(type = "number")]
        generation: u64,
        /// The name the viewer shows: the user's label, or `Live stream`.
        display_name: String,
        /// The lane's slot, which names it across resolutions.
        slot: String,
        /// Where the overlay comes from.
        overlay: LiveOverlayKind,
        /// The lane names: the overlay's, or the display name for the plain lane of a video without one.
        lanes: Vec<String>,
        /// The request that would make this stream again, as stored: `$NAME` references stay references and a literal
        /// password is left out. Only for an audience with host details; `null` for everyone else.
        recipe: Option<Box<NewLiveWire>>,
    },
}

/// One playlist of a playlist item: a run of its entries a viewer steps within.
#[derive(Serialize, Deserialize, TS)]
pub struct PlaylistSpanInfo {
    /// Names the playlist.
    pub id: String,
    /// What rows and the viewer's header show.
    pub label: String,
    /// How many entries it holds; the first playlist starts at the first entry.
    pub entries: u32,
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
