//! Workspace items, recipes and Content: what the user has added and how it resolves to something a viewer opens.
//!
//! An item is a saved recipe. Resolving the recipe gives the Content a viewer opens; a recipe that cannot resolve
//! stays in the workspace with its reason.

mod bookmark;
mod content;
pub mod intake;
mod item;
mod workspace;

pub use bookmark::{Bookmark, BookmarkKind, BookmarkTarget};
pub use content::{
    Content, ContentId, Entry, ImageSequence, OverlayRef, PlaylistContent, SeekableVideoContent,
    VideoRef,
};
pub use foundation::{BookmarkId, ItemId};
pub use item::{
    OverlayRecipe, PlaylistRecipe, Recipe, Source, Unreadable, VideoRecipe, WorkspaceItem,
    absolute_paths,
};
pub use workspace::Workspace;
