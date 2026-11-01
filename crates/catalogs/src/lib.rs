//! The render catalogs the app offers: the v4 documents the playback socket serves, and the six v3 catalogs compiled
//! once for everyone who still draws with them.
//!
//! A [`CatalogId`] names a catalog (`built-in:<name>` or `user:<file>`); [`Library::compiled`] gives the compiled form
//! of a v3 catalog. Catalogs in the user's catalog folder are not read yet.

mod built_in;
mod id;
mod library;

pub use id::CatalogId;
pub use library::{CatalogInfo, Library};
pub use render_catalog::{CatalogMetadata, CompiledCatalog};
