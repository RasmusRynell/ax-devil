use foundation::{BookmarkId, ItemId, MediaTime};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// What is reviewed or bookmarked: a video item (`entry: None`) or one entry of a playlist item, by its playlist and entry name.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct BookmarkTarget {
    /// The item.
    pub item: ItemId,
    /// The playlist entry as `{playlist name}/{entry name}`, the playlist name numbered as its item numbers it; `None`
    /// for a video item. The entry may have left the playlist since.
    pub entry: Option<String>,
}

/// What a bookmark marks.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum BookmarkKind {
    /// A remark.
    #[default]
    Note,
    /// A problem to fix.
    Issue,
    /// A good example.
    Example,
    /// Something to ask about.
    Question,
}

/// A marked frame of a video or playlist entry, with a note.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bookmark {
    /// Names the bookmark.
    pub id: BookmarkId,
    /// The video or entry the frame belongs to.
    pub target: BookmarkTarget,
    /// The frame's index.
    pub frame: u64,
    /// The frame's time, never negative (checked when a file is read).
    pub time: MediaTime,
    /// What the bookmark marks.
    pub kind: BookmarkKind,
    /// The user's text.
    pub note: String,
}

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<BookmarkKind>();
}
