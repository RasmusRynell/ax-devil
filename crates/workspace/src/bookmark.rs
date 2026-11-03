use foundation::{BookmarkId, ItemId, MediaTime};

/// What is reviewed or bookmarked: a video item (`entry: None`) or one entry of a playlist item, by its entry name.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct BookmarkTarget {
    /// The item.
    pub item: ItemId,
    /// The playlist entry's name; `None` for a video item. The entry may have left the playlist since.
    pub entry: Option<String>,
}

/// What a bookmark marks.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
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
