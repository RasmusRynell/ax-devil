//! The render catalogs the app offers: the built-in v4 documents and the `*.json` files of the user's catalog folder,
//! which the playback socket serves. The frontend compiles them; nothing here does, so a file that is not a catalog is
//! still listed and served.
//!
//! A [`CatalogId`] names a catalog (`built-in:<name>` or `user:<file>`). The folder is polled
//! ([`Library::rescan`]); [`Library::copy`] (or [`Library::copy_document`]) and [`Library::delete`] are the only writes, and a copy never overwrites.
//! The built-ins are listed first, then the readable user files by name, then the invalid ones by file name.

mod built_in;
mod copy;
mod folder;
mod id;
mod library;
mod origin;
pub mod sheets;

pub use id::CatalogId;
pub use library::{CatalogInfo, Library, Served};
pub use origin::Origin;

/// The setting that holds the id of the catalog new lanes start with; empty is Standard.
pub const DEFAULT_SETTING: &str = "overlays.default_catalog";
/// The most bytes of a user catalog that are read; a larger file is listed with a problem.
pub const MAX_CATALOG_BYTES: u64 = 1 << 20;
/// The most files of the user's folder that are listed, the first by name.
pub const MAX_USER_CATALOGS: usize = 256;
