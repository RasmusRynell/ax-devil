//! Where a viewer gets tracks and compiled catalogs from, as seams so tests can slow or fail them.

use std::sync::Arc;

use catalogs::{CatalogId, Library};
use foundation::{CancelToken, FrameSize, Interrupt, UserError};
use playlist_resolvers::OverlayRef;
use render_catalog::CompiledCatalog;
use scene_store::{Track, TrackRegistry};

/// Opens the track of an overlay file; blocking.
pub(crate) trait TrackSource: Send + Sync {
    fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>>;
}

impl TrackSource for TrackRegistry {
    fn open(
        &self,
        overlay: &OverlayRef,
        frame_size: FrameSize,
        cancel: &CancelToken,
    ) -> Result<Arc<Track>, Interrupt<UserError>> {
        TrackRegistry::open(self, overlay, frame_size, cancel)
    }
}

/// Compiles catalogs by id; blocking on first use.
pub(crate) trait CatalogSource: Send + Sync {
    fn default_id(&self) -> CatalogId;

    fn compiled(&self, id: &CatalogId) -> Result<Arc<CompiledCatalog>, UserError>;
}

impl CatalogSource for Library {
    fn default_id(&self) -> CatalogId {
        Library::default_id(self)
    }

    fn compiled(&self, id: &CatalogId) -> Result<Arc<CompiledCatalog>, UserError> {
        Library::compiled(self, id)
    }
}
