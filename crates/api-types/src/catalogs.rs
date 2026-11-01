//! Wire types for the render catalogs the app offers.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The body of `GET /api/catalogs`.
#[derive(Serialize, Deserialize, TS)]
pub struct CatalogsInfo {
    /// The catalogs, built-ins first and Standard first among them.
    pub catalogs: Vec<CatalogInfoWire>,
    /// The id a new lane starts with.
    pub default: String,
}

/// One catalog the picker offers.
#[derive(Serialize, Deserialize, TS)]
pub struct CatalogInfoWire {
    /// The id, such as `built-in:standard`.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
}
