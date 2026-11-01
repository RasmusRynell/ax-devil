//! The render catalogs the app offers: the six built into the app, checking a catalog file, and compiling each
//! catalog once for everyone who draws with it.
//!
//! A [`CatalogId`] names a catalog (`built-in:<name>` or `user:<file>`); [`Library::compiled`] gives its compiled form.
//! Catalogs in the user's catalog folder are not read yet, so only `check_file` looks at user files.

mod built_in;
mod check;
mod id;
mod library;

pub use check::{UNREADABLE, check_file};
pub use id::CatalogId;
pub use library::{CatalogInfo, Library};
pub use render_catalog::{CatalogDiagnostic, CatalogMetadata, CompiledCatalog};
