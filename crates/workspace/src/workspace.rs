use crate::{
    bookmark::{Bookmark, BookmarkTarget},
    item::WorkspaceItem,
};
use std::collections::BTreeSet;

/// The items a user works with, in order, the name they gave the workspace, and their bookmarks and reviewed marks.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Workspace {
    /// The user's name for the workspace; `None` is Untitled.
    pub name: Option<String>,
    /// Every item, resolved or not, in the order they were added.
    pub items: Vec<WorkspaceItem>,
    /// Every bookmark, in the order made.
    pub bookmarks: Vec<Bookmark>,
    /// The videos and playlist entries marked reviewed.
    pub reviewed: BTreeSet<BookmarkTarget>,
}
