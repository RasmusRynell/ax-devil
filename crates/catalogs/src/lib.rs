//! The render catalogs the app offers: the built-in v4 documents and the `*.json` files of the user's catalog folder,
//! which the playback socket serves. The frontend compiles them; nothing here does, so a file that is not a catalog is
//! still listed and served.
//!
//! A [`CatalogId`] names a catalog (`built-in:<name>` or `user:<file>`). The folder is polled
//! ([`Library::rescan`]) and never written.

mod built_in;
mod folder;
mod id;
mod library;

pub use id::CatalogId;
pub use library::{CatalogInfo, Library, Served};

/// The most bytes of a user catalog that are read; a larger file is listed with a problem.
pub const MAX_CATALOG_BYTES: u64 = 1 << 20;
/// The most files of the user's folder that are listed, the first by name.
pub const MAX_USER_CATALOGS: usize = 256;
