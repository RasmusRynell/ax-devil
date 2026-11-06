//! Bookmarks as the Session socket carries them: the commands that change them and the groups the Bookmarks view lists.

use foundation::ItemId;
use serde::{Deserialize, Serialize};
use ts_rs::TS;
use workspace::{BookmarkId, ContentId};

/// What a bookmark marks.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum BookmarkKindWire {
    /// A remark.
    Note,
    /// A problem to fix.
    Issue,
    /// A good example.
    Example,
    /// Something to ask about.
    Question,
}

/// A change to the bookmarks or reviewed marks.
#[derive(Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum BookmarkCommand {
    /// Marks a frame of a video or playlist entry, as a Note without text. Refused for a live stream, an item that has
    /// not resolved and an entry the item's Content lacks.
    Add {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
        /// The entry marked, by its id: the item's own id for a video.
        #[ts(type = "string")]
        entry: ContentId,
        /// The frame's index.
        #[ts(type = "number")]
        frame: u64,
        /// The frame's time in microseconds, not negative.
        #[ts(type = "number")]
        time_us: i64,
    },
    /// Changes a bookmark's text (an empty one clears it) or kind.
    Edit {
        /// The bookmark.
        #[ts(type = "string")]
        id: BookmarkId,
        /// The new text; unchanged when absent.
        #[serde(default)]
        #[ts(optional)]
        note: Option<String>,
        /// The new kind; unchanged when absent.
        #[serde(default)]
        #[ts(optional)]
        kind: Option<BookmarkKindWire>,
    },
    /// Deletes a bookmark.
    Remove {
        /// The bookmark.
        #[ts(type = "string")]
        id: BookmarkId,
    },
    /// Marks a video or playlist entry reviewed or not.
    SetReviewed {
        /// The item.
        #[ts(type = "string")]
        item: ItemId,
        /// The entry as its group names it; absent for a video.
        #[serde(default)]
        #[ts(optional)]
        entry: Option<String>,
        /// Whether it is reviewed afterwards.
        reviewed: bool,
    },
}

/// One bookmark.
#[derive(Serialize, Deserialize, TS)]
pub struct BookmarkInfo {
    /// Names the bookmark.
    pub id: String,
    /// The frame's index.
    #[ts(type = "number")]
    pub frame: u64,
    /// The frame's time in microseconds.
    #[ts(type = "number")]
    pub time_us: i64,
    /// What it marks.
    pub kind: BookmarkKindWire,
    /// The user's text; empty when there is none.
    pub note: String,
}

/// Whether a group can be opened.
#[derive(Serialize, Deserialize, TS)]
#[serde(tag = "status")]
pub enum BookmarkStanding {
    /// The target exists; opening it opens this entry of its item.
    Open {
        /// The entry's id.
        entry: String,
    },
    /// The item is still resolving.
    Pending,
    /// The item has no such target now.
    Missing,
}

/// A video or playlist entry with bookmarks or a reviewed mark.
#[derive(Serialize, Deserialize, TS)]
pub struct BookmarkGroupInfo {
    /// Names the group.
    pub key: String,
    /// The item.
    pub item: String,
    /// The item's name.
    pub item_name: String,
    /// The entry as shown under the item; empty for a video.
    pub entry: String,
    /// The entry as `setReviewed` names it; absent for a video.
    pub target: Option<String>,
    /// Whether it is marked reviewed.
    pub reviewed: bool,
    /// Whether it can be opened.
    pub standing: BookmarkStanding,
    /// The bookmarks by frame.
    pub bookmarks: Vec<BookmarkInfo>,
}
