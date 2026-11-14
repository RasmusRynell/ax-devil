//! Workspace items, recipes and Content: what the user has added and how it resolves to something a viewer opens.
//!
//! An item is a saved recipe. Resolving the recipe gives the Content a viewer opens; a recipe that cannot resolve
//! stays in the workspace with its reason. [`store`] holds the workspace's state and its pure transitions.

mod bookmark;
mod content;
mod credentials;
mod derived;
mod host;
pub mod intake;
mod item;
mod live;
mod metadata;
mod references;
mod sources;
pub mod store;
mod workspace;

pub use bookmark::{Bookmark, BookmarkKind, BookmarkTarget};
pub use content::{
    Content, ContentId, Entry, ImageSequence, LaneSource, OverlayRef, PlaylistContent,
    PlaylistSpan, SeekableVideoContent, VideoRef, playlist_label,
};
pub use credentials::{Environment, Secret};
pub use derived::DerivedLane;
pub use foundation::{BookmarkId, ItemId};
pub use host::{DisplayHost, HostReading};
pub use item::{
    OverlayRecipe, PlaylistRecipe, Recipe, Source, Unreadable, VideoRecipe, WorkspaceItem,
    absolute_paths, check_readable_file,
};
pub use live::{
    DataHubSettings, KeptTransports, LIVE_SLOT, LiveOverlay, LiveRecipe, LiveVideoContent, Login,
    MqttSettings, Protocol, ResolvedCamera, RtspRoute, StreamUrl, url_password_dropped,
    url_without_password,
};
pub use metadata::metadata_fields;
pub use references::{Connection, Previews, Shown, preview};
pub use sources::{Snapshot, Watch};
pub use workspace::Workspace;

/// Visits every type this crate sends to the frontend, for `export-types`.
pub fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    bookmark::visit_api_types(types);
    live::visit_api_types(types);
    store::visit_api_types(types);
}
