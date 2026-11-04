//! Wire types for the render catalogs the app offers.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// One catalog the picker offers.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
pub struct CatalogInfoWire {
    /// The id, such as `built-in:standard`.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
    /// Why the catalog cannot be used; absent for a usable catalog.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub problem: Option<String>,
}

/// A request to copy a catalog under a new name.
#[derive(Serialize, Deserialize, TS)]
pub struct CopyCatalogRequest {
    /// The id of the catalog to copy: `built-in:<name>` or `user:<file>`.
    pub source: String,
    /// What the user named the copy.
    pub name: String,
}

/// The reply to a copy: the id of the new catalog.
#[derive(Serialize, Deserialize, TS)]
pub struct CopiedCatalog {
    /// The id of the new user catalog, `user:<file>`.
    pub id: String,
}

/// A request to delete a user catalog.
#[derive(Serialize, Deserialize, TS)]
pub struct DeleteCatalogRequest {
    /// The id of the user catalog to delete, `user:<file>`.
    pub id: String,
}
