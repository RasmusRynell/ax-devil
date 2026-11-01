//! The render catalogs the app offers: the built-in v4 documents the playback socket serves. The frontend compiles
//! them; nothing here does.
//!
//! A [`CatalogId`] names a catalog (`built-in:<name>` or `user:<file>`). Catalogs in the user's catalog folder are not
//! read yet.

mod built_in;
mod id;
mod library;

pub use id::CatalogId;
pub use library::{CatalogInfo, Library};
